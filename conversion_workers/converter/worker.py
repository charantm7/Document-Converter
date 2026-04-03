import tempfile
import shutil
import subprocess
import time
import os
import logging
from pypdf import PdfWriter, PdfReader
from pathlib import Path
from typing import Optional
from supabase import Client
from pdf2docx import Converter

from adobe.pdfservices.operation.auth.service_principal_credentials import ServicePrincipalCredentials
from adobe.pdfservices.operation.pdf_services import PDFServices
from adobe.pdfservices.operation.pdf_services_media_type import PDFServicesMediaType
from adobe.pdfservices.operation.pdfjobs.jobs.export_pdf_job import ExportPDFJob
from adobe.pdfservices.operation.pdfjobs.jobs.create_pdf_job import CreatePDFJob
from adobe.pdfservices.operation.pdfjobs.params.export_pdf.export_pdf_params import ExportPDFParams
from adobe.pdfservices.operation.pdfjobs.params.export_pdf.export_pdf_target_format import ExportPDFTargetFormat
from adobe.pdfservices.operation.pdfjobs.result.export_pdf_result import ExportPDFResult
from adobe.pdfservices.operation.pdfjobs.result.create_pdf_result import CreatePDFResult
from adobe.pdfservices.operation.exception.exceptions import (
    ServiceApiException, ServiceUsageException, SdkException
)

from conversion_workers.settings import settings
from sqlalchemy.orm import Session
from shared_database.repository import JobRepository
from shared_database.models import JobStatus, Jobs

# Exceptions
from conversion_workers.exception import (
    LibreOfficeNotFoundError,
    FileNotFoundError,
    ConversionTimeoutError,
    ConversionFailedError,
    UploadFailedError,
    CompressionFailedError
)

logger = logging.getLogger(__name__)

_MIME_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_MIME_PDF = "application/pdf"
_MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class JobRecordHelper:

    def __init__(self, db: Session):
        self.job_repo = JobRepository(db)

    def fail(self, record: Jobs) -> None:
        self.update_status(record, JobStatus.failed)

    def update_status(self, record: Jobs, status: JobStatus) -> None:
        self.job_repo.update_status(record, status)

    def update_record(self, record: Jobs, **kwargs) -> Jobs:
        return self.job_repo.update_records(record, **kwargs)

    def create_job_record(
        self,
        job_id: str,
        path: str,
        user_id: str,
        conversion_type: str
    ) -> Jobs:
        return self.job_repo.create(
            id=job_id,
            input_url=path,
            user_id=user_id,
            conversion_type=conversion_type,
            status=JobStatus.processing
        )


class Conversion:
    def __init__(self, supabase: Client, db: Session):
        self.db = db
        self.supabase_client = supabase
        self.job_repo = JobRepository(db)
        self._adobe_credentials = ServicePrincipalCredentials(
            client_id=settings.CLIENT_ID,
            client_secret=settings.CLIENT_SECRET
        )

    def convert_file_to_pdf(
        self,
        job_id: str,
        path: str,
        target_format: str,
        source_format: str
    ) -> None:
        """
        converts PDF -> DOCX

        Accepts params:
        - job_id
        - path

        Process:
        - 

        """
        record = self._bootstrap_job(
            job_id=job_id,
            path=path,
            expected_suffix=f".{source_format}",
            conversion_type=f"convert_{source_format}_to_{target_format}"
        )

        file_bytes = self._download_raw(record, path, job_id)

        with tempfile.TemporaryDirectory() as tmp:
            tempdir = Path(tmp)
            input_file = tempdir / f"input.{source_format}"
            output_pdf = tempdir / "output.pdf"

            input_file.write_bytes(file_bytes)

            self._adobe_create_pdf(
                record=record,
                input_path=input_file,
                output_path=output_pdf,
                job_id=job_id,
                source_format=source_format
            )

            output_storage_path = path.replace(
                f"original.{source_format}", "converted.pdf")
            self._upload_converted(
                record=record,
                local_path=output_pdf,
                storage_path=output_storage_path,
                mime_type=_MIME_PDF,
                job_id=job_id
            )

        logger.info("[worker] upload complete for job %s", job_id)

    def convert_pdf_to_file(self, job_id: str, path: str, target_format: str) -> None:
        """
        DOCX (Supabase) → PDF (Adobe PDF Services) → Supabase

        :param job_id: Unique job identifier
        :param path:   Supabase storage path of the source DOCX
        :param user_id: Requesting user ID
        """

        formats, conversion_type = self._get_conversion_type_and_format(
            target_format)

        record = self._bootstrap_job(
            job_id=job_id,
            path=path,
            expected_suffix=".pdf",
            conversion_type=conversion_type
        )

        file_bytes = self._download_raw(record, path, job_id)

        with tempfile.TemporaryDirectory() as tmp:
            tempdir = Path(tmp)
            input_pdf = tempdir / "input.pdf"
            output_file = tempdir / f"output.{target_format}"

            input_pdf.write_bytes(file_bytes)

            self._adobe_export(
                record=record,
                input_path=input_pdf,
                output_path=output_file,
                target_format=formats,
                job_id=job_id
            )

            output_storage_path = path.replace(
                "original.pdf", f"converted.{target_format}")
            self._upload_converted(
                record=record,
                local_path=output_file,
                storage_path=output_storage_path,
                mime_type=self._get_mime_type(target_format),
                job_id=job_id
            )

        logger.info("[worker] upload complete for job %s", job_id)

    """Adobe Helpers"""

    def _adobe_export(
        self,
        record: Jobs,
        input_path: Path,
        output_path: Path,
        target_format: ExportPDFTargetFormat,
        job_id: str
    ) -> None:
        """Export a PDF to another format via Adobe PDF Services."""
        try:
            pdf_services = PDFServices(credentials=self._adobe_credentials)

            with open(input_path, "rb") as f:
                input_asset = pdf_services.upload(
                    input_stream=f,
                    mime_type=PDFServicesMediaType.PDF
                )

            export_job = ExportPDFJob(
                input_asset=input_asset,
                export_pdf_params=ExportPDFParams(target_format=target_format)
            )

            location = pdf_services.submit(export_job)
            response = pdf_services.get_job_result(location, ExportPDFResult)

            stream_asset = pdf_services.get_content(
                response.get_result().get_asset()
            )
            output_path.write_bytes(stream_asset.get_input_stream())

        except (ServiceApiException, ServiceUsageException, SdkException) as e:
            JobRecordHelper(self.db).fail(record)
            logger.error(
                "[worker] Adobe export failed for job %s: %s", job_id, e)
            raise ConversionFailedError(f"Adobe export failed: {e}") from e

        self._assert_output_exists(record, output_path)

    def _adobe_create_pdf(
        self,
        record: Jobs,
        input_path: Path,
        output_path: Path,
        job_id: str,
        source_format: str
    ) -> None:
        """Create a PDF from a DOCX via Adobe PDF Services."""
        try:
            pdf_services = PDFServices(credentials=self._adobe_credentials)

            with open(input_path, "rb") as f:
                input_asset = pdf_services.upload(
                    input_stream=f,
                    mime_type=self._get_adobe_pdf_service_mime_type(
                        source_format)
                )

            create_job = CreatePDFJob(input_asset=input_asset)

            location = pdf_services.submit(create_job)
            response = pdf_services.get_job_result(location, CreatePDFResult)

            stream_asset = pdf_services.get_content(
                response.get_result().get_asset()
            )
            output_path.write_bytes(stream_asset.get_input_stream())

        except (ServiceApiException, ServiceUsageException, SdkException) as e:
            JobRecordHelper(self.db).fail(record)
            logger.error(
                "[worker] Adobe create PDF failed for job %s: %s", job_id, e)
            raise ConversionFailedError(f"Adobe create PDF failed: {e}") from e

        self._assert_output_exists(record, output_path)

    """Supabase Helpers"""

    def _download_raw(self, record: Jobs, path: str, job_id: str) -> bytes:
        """This function downloads the raw file from the supabase storage."""
        try:
            return self.supabase_client.storage.from_(
                settings.SUPABASE_RAW_BUCKET
            ).download(path=path)
        except Exception as e:
            JobRecordHelper(self.db).fail(record)
            logger.error("[worker] download failed for job %s: %s", job_id, e)
            raise FileNotFoundError(
                f"File not found in storage: {path}") from e

    def _upload_converted(
        self,
        record: Jobs,
        local_path: Path,
        storage_path: str,
        mime_type: str,
        job_id: str
    ) -> None:
        """This func upload a converted file to the converted supabase bucket."""
        try:
            with open(local_path, "rb") as f:
                self.supabase_client.storage.from_(
                    settings.SUPABASE_CONVERTED_BUCKET
                ).upload(
                    storage_path,
                    f,
                    {"content-type": mime_type, "x-upsert": "true"}
                )
            JobRecordHelper(self.db).update_record(
                record, output_url=storage_path)
            JobRecordHelper(self.db).update_status(
                record, JobStatus.completed)

        except Exception as e:
            JobRecordHelper(self.db).fail(record)
            logger.error("[worker] upload failed for job %s: %s", job_id, e)
            raise UploadFailedError(f"Upload failed: {e}") from e

    """Job record helpers"""

    def _bootstrap_job(
        self,
        job_id: str,
        path: str,
        expected_suffix: str,
        conversion_type: str
    ) -> Jobs:
        """
        1) validate the job record exists
        2) validate file extension,
        3) update the record with input metadata.
        """
        record = self.job_repo.get_by_job_id(job_id)
        if not record:
            raise Exception(f"Job not found: {job_id}")

        suffix = Path(path).suffix.lower()
        if suffix != expected_suffix:
            JobRecordHelper(self.db).fail(record)
            raise ConversionFailedError(f"Unsupported input format: {suffix}")

        return JobRecordHelper(self.db).update_record(
            record,
            input_url=path,
            conversion_type=conversion_type
        )

    def _assert_output_exists(self, record: Jobs, path: Path) -> None:
        """Raise ConversionFailedError if the expected output file is missing."""
        if not path.exists():
            JobRecordHelper(self.db).fail(record)
            raise ConversionFailedError(
                "Conversion failed: output file not found")

    def _get_conversion_type_and_format(self, target_format: str):
        formats = {
            "docx": ExportPDFTargetFormat.DOCX,
            "pptx": ExportPDFTargetFormat.PPTX
        }
        return formats.get(target_format), f"convert_pdf_to_{target_format}"

    def _get_adobe_pdf_service_mime_type(self, source_format: str):
        formats = {
            "docx": PDFServicesMediaType.DOCX,
            "pptx": PDFServicesMediaType.PPTX
        }
        return formats.get(source_format)

    def _get_mime_type(self, target_format: str):
        types = {
            "docx": _MIME_DOCX,
            "pdf": _MIME_PDF,
            "pptx": _MIME_PPTX
        }
        return types.get(target_format)


class Compression:
    def __init__(self, supabase: Client, db: Session):
        self.record_helper = JobRecordHelper(db)
        self.supabase_client = supabase
        self.job_repo = JobRepository(db)

    def compress_pdf(self, job_id: str, path: str, quality: str = "ebook", pdf_bytes: Optional[bytes] = None):
        """
        Docstring for compress_pdf

        PDF (supabase) -> Compressed PDF -> Supabase
        """

        print(f"[wroker] starting compression for job id {job_id}")

        record = self._bootstrap_job(
            job_id=job_id,
            path=path,
            expected_suffix=".pdf",
            conversion_type="compress_pdf"
        )

        try:

            file_byte = self.supabase_client.storage.from_(
                settings.SUPABASE_RAW_BUCKET).download(path=path)
        except Exception as e:
            self.record_helper.fail(record)
            print(
                f"[worker] error downloading file for job {job_id}: {str(e)}")
            raise FileNotFoundError(
                f"File not found in storage: {path}") from e

        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)

            input_pdf = Path(tempdir) / "input.pdf"
            output_pdf = Path(tempdir) / "compressed.pdf"

            with open(input_pdf, "wb") as f:
                f.write(file_byte)

            result = subprocess.run(
                [
                    "gs",
                    "-sDEVICE=pdfwrite",
                    "-dCompatibilityLevel=1.4",
                    f"-dPDFSETTINGS=/{quality}",
                    "-dNOPAUSE",
                    "-dQUIET",
                    "-dBATCH",
                    f"-sOutputFile={output_pdf}",
                    str(input_pdf),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if result.returncode != 0:
                self.record_helper.fail(record)
                print(
                    f"[worker] error during compression for job {job_id}: {result.stderr.decode(errors='ignore')}")
                raise CompressionFailedError(
                    f"Compression failed: {result.stderr.decode(errors='ignore')}")

            if not output_pdf.exists():
                self.record_helper.fail(record)
                raise CompressionFailedError(
                    "Compression failed: No output file found")

            output_storage_path = path.replace(
                "original.pdf", "compressed.pdf")

            try:

                with open(output_pdf, "rb") as f:
                    self.supabase_client.storage.from_(settings.SUPABASE_COMPRESSED_BUCKET).upload(
                        output_storage_path,
                        f,
                        {
                            "content-type": "application/pdf",
                            "x-upsert": "true"
                        },
                    )
                    self.record_helper.update_record(
                        record, output_url=output_storage_path)
                    self.record_helper.update_status(
                        record, JobStatus.completed)
            except Exception as e:
                self.record_helper.fail(record)

                print(
                    f"[worker] error uploading file for job {job_id}: {str(e)}")
                raise UploadFailedError(f"Upload failed: {str(e)}") from e

        print(f"[worker] upload complete for job {job_id}")

    def _bootstrap_job(
        self,
        job_id: str,
        path: str,
        expected_suffix: str,
        conversion_type: str
    ) -> Jobs:
        """
        1) validate the job record exists
        2) validate file extension,
        3) update the record with input metadata.
        """
        record = self.job_repo.get_by_job_id(job_id)
        if not record:
            raise Exception(f"Job not found: {job_id}")

        suffix = Path(path).suffix.lower()
        if suffix != expected_suffix:
            self.record_helper.fail(record)
            raise ConversionFailedError(
                f"Unsupported input format: {suffix}")

        return self.record_helper.update_record(
            record,
            input_url=path,
            conversion_type=conversion_type
        )


class Customization:
    def __init__(self, supabase: Client, db: Session):
        self.record_helper = JobRecordHelper(db)
        self.supabase_client = supabase
        self.job_repo = JobRepository(db)

    def merge_pdf(self, job_id: str, path: list[str]):
        """
        Docstring for merge_pdf

        PDF (supabase) -> Merged PDF -> Supabase
        """

        record = self._bootstrap_job(
            job_id=job_id,
            path=path,
            expected_suffix=".pdf",
            conversion_type="merge_pdf"
        )

        file_bytes_list = self._download_pdf(job_id, path, record)

        output_pdf_bytes = self._merge_pdfs(file_bytes_list, record)
        output_storage_path = str(Path(path[0]).with_name('merged.pdf'))
        try:
            self.supabase_client.storage.from_(settings.SUPABASE_CONVERTED_BUCKET).upload(
                output_storage_path,
                output_pdf_bytes,
                {
                    "content-type": "application/pdf",
                    "x-upsert": "true"
                },
            )
            self.record_helper.update_record(
                record, output_url=output_storage_path)
            self.record_helper.update_status(
                record, JobStatus.completed)
        except Exception as e:
            self.record_helper.fail(record)
            print(f"[worker] error uploading file for job {job_id}: {str(e)}")
            raise UploadFailedError(f"Upload failed: {str(e)}") from e

        print(f"[worker] upload complete for job {job_id}")

    def _merge_pdfs(self, pdf_bytes_list: list[bytes], record):

        if len(pdf_bytes_list) < 2:
            self.record_helper.fail(record)
            raise ValueError(
                "At least two PDF files are required for merging.")

        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)

            merger = PdfWriter()

            for i, pdf_bytes in enumerate(pdf_bytes_list):
                temp_pdf_path = tempdir / f"temp_{i}.pdf"
                with open(temp_pdf_path, "wb") as f:
                    f.write(pdf_bytes)
                reader = PdfReader(str(temp_pdf_path))
                merger.append(reader)

            output_pdf_path = tempdir / "merged.pdf"
            merger.write(str(output_pdf_path))
            merger.close()

            if not output_pdf_path.exists():
                self.record_helper.fail(record)

                raise ConversionFailedError(
                    "Merging failed: No output file found")

            return output_pdf_path.read_bytes()

    def _download_pdf(self, job_id: str, path: list[str], record) -> list[bytes]:
        """
        Docstring for download_pdf

        PDF (supabase) -> bytes
        """
        pdf_bytes = []
        for p in path:
            try:

                file_byte = self.supabase_client.storage.from_(
                    settings.SUPABASE_RAW_BUCKET).download(path=p)
                pdf_bytes.append(file_byte)
            except Exception as e:
                self.record_helper.fail(record)

                print(
                    f"[worker] error downloading file for job {job_id}: {str(e)}")
                raise FileNotFoundError(
                    f"File not found in storage: {p}") from e
        return pdf_bytes

    def _bootstrap_job(
        self,
        job_id: str,
        path: list[str],
        expected_suffix: str,
        conversion_type: str
    ) -> Jobs:
        """
        1) validate the job record exists
        2) validate file extension,
        3) update the record with input metadata.
        """
        record = self.job_repo.get_by_job_id(job_id)
        if not record:
            raise Exception(f"Job not found: {job_id}")

        for p in path:
            suffix = Path(p).suffix.lower()
            if suffix != expected_suffix:
                self.record_helper.fail(record)
                raise ConversionFailedError(
                    f"Unsupported input format: {suffix}")

        return self.record_helper.update_record(
            record,
            input_url=path,
            conversion_type=conversion_type
        )
