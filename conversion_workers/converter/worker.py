import tempfile
import shutil
import subprocess
import time
import os
from fastapi import HTTPException
from pypdf import PdfWriter, PdfReader
from pathlib import Path
from typing import Optional
from supabase import Client
from pdf2docx import Converter

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


class LibreOfficeConverter:

    def __init__(
        self,
        soffice_path: Optional[str] = None,
        timeout_seconds: int = 120,
    ):
        self.soffice_path = soffice_path or self._detect_soffice()
        self.timeout_second = timeout_seconds

    def convert(self, input_path: Path, output_dir: Path, target_ext: str) -> Path:

        cmd = [
            self.soffice_path,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--norestore",
            "--convert-to",
            target_ext,
            "--outdir",
            str(output_dir),
            str(input_path)
        ]

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_second,
                check=True,
            )

        except subprocess.TimeoutExpired as e:
            raise ConversionTimeoutError(
                "LibreOffice Conversion timeout error") from e

        except subprocess.CalledProcessError as e:
            print(
                f"[DEBUG] CalledProcessError STDERR: {e.stderr.decode(errors='ignore')}")
            raise ConversionFailedError(
                e.stderr.decode(errors="ignore")) from e

        expected_output = output_dir / \
            f"{input_path.stem}.{target_ext.lower()}"
        if expected_output.exists():
            return expected_output

        for file in output_dir.iterdir():
            if file.suffix.lower() == f".{target_ext.lower()}" and file != input_path:
                return file

        raise ConversionFailedError(
            f"LibreOffice produced no output.\nSTDERR:\n{result.stderr.decode(errors='ignore')}"
        )

    def _detect_soffice(self) -> str:
        candidates = [
            shutil.which("soffice"),
            "/usr/bin/soffice",
            "/usr/local/bin/soffice",
            "C:\\Program Files\\LibreOffice\\program\\soffice.exe",
        ]

        for path in candidates:
            if path and Path(path).exists():
                return str(path)
        raise LibreOfficeNotFoundError(
            "Libreoffice not installed in the system")


class Conversion:
    def __init__(self, supabase: Client, db: Session):
        self.supabase_client = supabase
        self._libreoffice_converter = LibreOfficeConverter()
        self.job_repo = JobRepository(db)

    def convert_pdf_to_ppt(self, job_id: str, path: str, user_id: str):
        """
        Docstring for convert_pdf_to_ppt

        :param self: Description
        :param job_id: Description
        :type job_id: str
        :param path: Description
        :type path: str
        Converter PDF (supabase) -> PPT -> Supabase
        """

        record = self.job_repo.get_by_job_id(job_id)
        if not record:
            raise Exception("Job not found")

        payload = {
            "input_url": path,
            "conversion_type": "convert_pdf_to_ppt"
        }

        record = self._update_record(record, **payload)

        try:

            file_byte = self.supabase_client.storage.from_(
                settings.SUPABASE_RAW_BUCKET).download(path=path)
        except Exception as e:
            self._update_status(record, JobStatus.failed)
            raise FileNotFoundError(
                f"File not found in storage: {path}") from e

        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)

            suffix = Path(path).suffix.lower()

            if suffix != ".pdf":
                self._update_status(record, JobStatus.failed)
                raise ConversionFailedError(
                    f"Unsupported input format: {suffix}")

            input_pdf = tempdir / "input.pdf"
            output_dir = tempdir

            with open(input_pdf, "wb") as f:
                f.write(file_byte)

            output_file = self._libreoffice_converter.convert(
                input_path=input_pdf, output_dir=output_dir, target_ext='odp')

            output_file = self._libreoffice_converter.convert(
                input_path=output_file, output_dir=output_dir, target_ext='pptx')

            if not output_file.exists():
                self._update_status(record, JobStatus.failed)
                raise ConversionFailedError(
                    "Conversion failed: No output file found")

            output_storage_path = path.replace(
                "original.pdf", "converted.pptx")

            try:
                with open(output_file, "rb") as f:
                    self.supabase_client.storage.from_(settings.SUPABASE_CONVERTED_BUCKET).upload(
                        output_storage_path,
                        f,
                        {
                            "content-type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                            "x-upsert": "true"
                        },
                    )
                self._update_record(record, output_url=output_storage_path)
                self._update_status(record, JobStatus.completed)

            except Exception as e:
                self._update_status(record, JobStatus.failed)
                raise UploadFailedError(f"Upload failed: {str(e)}") from e
        print(f"[worker] upload complete for job {job_id}")

    def convert_docx_to_pdf(self, job_id: str, path: str, user_id: str):
        """
        Docstring for convert_docx_to_pdf

        :param self: Description
        :param job_id: Description
        :type job_id: str
        :param path: Description
        :type path: str

        Converter DOCX (supabase) -> PDF -> Supabase
        """

        record = self.job_repo.get_by_job_id(job_id)
        if not record:
            raise Exception("Job not found")
        payload = {
            "input_url": path,
            "conversion_type": "convert_docx_to_pdf"
        }

        record = self._update_record(record, **payload)

        try:

            file_byte = self.supabase_client.storage.from_(
                settings.SUPABASE_RAW_BUCKET).download(path=path)
        except Exception as e:
            self._update_status(record, JobStatus.failed)
            print(
                f"[worker] error downloading file for job {job_id}: {str(e)}")
            raise FileNotFoundError(
                f"File not found in storage: {path}") from e

        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)

            suffix = Path(path).suffix.lower()

            if suffix != ".docx":
                self._update_status(record, JobStatus.failed)
                raise ConversionFailedError(
                    f"Unsupported input format: {suffix}")

            input_docx = tempdir / 'input.docx'
            output_dir = tempdir / "output.pdf"

            with open(input_docx, "wb") as f:
                f.write(file_byte)

            output_file = self._libreoffice_converter.convert(
                input_path=input_docx, output_dir=output_dir, target_ext='pdf')

            if not output_file.exists():
                self._update_status(record, JobStatus.failed)
                raise ConversionFailedError(
                    "Conversion failed: No output file found")
            output_storage_path = path.replace(
                "original.docx", "converted.pdf")

            try:
                with open(output_file, "rb") as f:
                    self.supabase_client.storage.from_(settings.SUPABASE_CONVERTED_BUCKET).upload(
                        output_storage_path,
                        f,
                        {
                            "content-type": "application/pdf",
                            "x-upsert": "true"
                        },
                    )
                self._update_output_url(record, output_storage_path)
                self._update_status(record, JobStatus.completed)

            except Exception as e:
                self._update_status(record, JobStatus.failed)
                print(
                    f"[worker] error uploading file for job {job_id}: {str(e)}")
                raise UploadFailedError(f"Upload failed: {str(e)}") from e
        print(f"[worker] upload complete for job {job_id}")

    def convert_pdf_to_docx(self, job_id: str, path: str, user_id: str):
        """
        Docstring for convert_pdf_to_docx

        PDF (supabase) -> DOX -> Supabase
        """

        record = self.job_repo.get_by_job_id(job_id)
        print(record)
        if not record:
            raise Exception("Job not found")

        payload = {
            "input_url": path,
            "conversion_type": "convert_pdf_to_docx"
        }

        self._update_record(record, **payload)

        print(f"[wroker] starting convertion for job id {job_id}")

        try:

            file_byte = self.supabase_client.storage.from_(
                settings.SUPABASE_RAW_BUCKET).download(path=path)
        except Exception as e:
            self._update_status(record, JobStatus.failed)
            print(
                f"[worker] error downloading file for job {job_id}: {str(e)}")
            raise FileNotFoundError(
                f"File not found in storage: {path}") from e

        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)

            suffix = Path(path).suffix.lower()

            if suffix != ".pdf":
                self._update_status(record, JobStatus.failed)
                raise ConversionFailedError(
                    f"Unsupported input format: {suffix}")

            input_pdf = Path(tempdir) / "input.pdf"
            output_dir = Path(tempdir) / "output.docx"

            with open(input_pdf, "wb") as f:
                f.write(file_byte)

            try:
                cv = Converter(str(input_pdf))
                cv.convert(str(output_dir))
                cv.close()
            except Exception as e:
                self._update_status(record, JobStatus.failed)
                print(
                    f"[worker] error during conversion for job {job_id}: {str(e)}")
                raise ConversionFailedError(
                    f"Conversion failed: {str(e)}") from e

            if not output_dir.exists():
                self._update_status(record, JobStatus.failed)
                raise ConversionFailedError(
                    "Conversion failed: No output file found")

            output_storage_path = path.replace(
                "original.pdf", "converted.docx")

            try:

                with open(output_dir, "rb") as f:
                    self.supabase_client.storage.from_(settings.SUPABASE_CONVERTED_BUCKET).upload(
                        output_storage_path,
                        f,
                        {
                            "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            "x-upsert": "true"
                        },
                    )

            except Exception as e:
                self._update_status(record, JobStatus.failed)

                print(
                    f"[worker] error uploading file for job {job_id}: {str(e)}")
                raise UploadFailedError(f"Upload failed: {str(e)}") from e

            try:
                self._update_record(record, output_url=output_storage_path)
                self._update_status(record, JobStatus.completed)
                print("DB UPDATED SUCCESS")

            except Exception as e:
                print("DB ERROR:", str(e))
                raise

        print(f"[worker] upload complete for job {job_id}")

    def _create_job_record(self, job_id, path, user_id, conversion_type) -> Jobs:
        payload = {
            "id": job_id,
            "input_url": path,
            "user_id": user_id,
            "conversion_type": conversion_type,
            "status": JobStatus.processing
        }
        return self.job_repo.create(**payload)

    def _update_status(self, record, status) -> None:
        self.job_repo.update_status(record, status)
        return None

    def _update_record(self, record, **kw) -> Jobs:
        return self.job_repo.update_records(record, **kw)


class Compression:
    def __init__(self, supabase: Client):
        self.supabase_client = supabase

    def compress_pdf(self, job_id: str, path: str, quality: str = "ebook", pdf_bytes: Optional[bytes] = None):
        """
        Docstring for compress_pdf

        PDF (supabase) -> Compressed PDF -> Supabase
        """

        print(f"[wroker] starting compression for job id {job_id}")

        try:

            file_byte = self.supabase_client.storage.from_(
                settings.SUPABASE_RAW_BUCKET).download(path=path)
        except Exception as e:
            print(
                f"[worker] error downloading file for job {job_id}: {str(e)}")
            raise FileNotFoundError(
                f"File not found in storage: {path}") from e

        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)

            suffix = Path(path).suffix.lower()

            if suffix != ".pdf":
                raise ConversionFailedError(
                    f"Unsupported input format: {suffix}")

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
                print(
                    f"[worker] error during compression for job {job_id}: {result.stderr.decode(errors='ignore')}")
                raise CompressionFailedError(
                    f"Compression failed: {result.stderr.decode(errors='ignore')}")

            if not output_pdf.exists():
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
            except Exception as e:
                print(
                    f"[worker] error uploading file for job {job_id}: {str(e)}")
                raise UploadFailedError(f"Upload failed: {str(e)}") from e

        print(f"[worker] upload complete for job {job_id}")


class Customization:
    def __init__(self, supabase: Client):
        self.supabase_client = supabase

    def merge_pdf(self, job_id: str, path: list[str]):
        """
        Docstring for merge_pdf

        PDF (supabase) -> Merged PDF -> Supabase
        """

        file_bytes_list = self._download_pdf(job_id, path)

        output_pdf_bytes = self._merge_pdfs(file_bytes_list)
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
        except Exception as e:
            print(f"[worker] error uploading file for job {job_id}: {str(e)}")
            raise UploadFailedError(f"Upload failed: {str(e)}") from e
        print(f"[worker] upload complete for job {job_id}")

    def _merge_pdfs(self, pdf_bytes_list: list[bytes]):

        if len(pdf_bytes_list) < 2:
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
                raise ConversionFailedError(
                    "Merging failed: No output file found")

            return output_pdf_path.read_bytes()

    def _download_pdf(self, job_id: str, path: list[str]) -> list[bytes]:
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
                print(
                    f"[worker] error downloading file for job {job_id}: {str(e)}")
                raise FileNotFoundError(
                    f"File not found in storage: {p}") from e
        return pdf_bytes
