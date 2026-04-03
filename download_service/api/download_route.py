from sqlalchemy.orm import Session
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status

from shared_database.connection import get_db
from shared_database.repository import JobRepository
from download_service.api.supabase_client import supabase
from download_service.settings import settings


downloader = APIRouter()


class DownloadSchema(BaseModel):
    job_id: str


@downloader.get("/download")
async def get_downloadable_link(data: DownloadSchema, db: Session = Depends(get_db)):

    conversion = ["convert_pdf_to_pptx",
                  "convert_docx_to_pdf", "convert_pdf_to_docx", "merge_pdf", "convert_pptx_to_pdf"]

    repo = JobRepository(db)
    record = repo.get_by_job_id(data.job_id)

    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No conversion record found")

    if record.dowload_url is None:

        bucket = (
            settings.SUPABASE_CONVERTED_BUCKET if record.conversion_type in conversion else settings.SUPABASE_COMPRESSED_BUCKET)

        url = supabase.storage.from_(
            bucket).create_signed_url(record.output_url, 3600)

        record.dowload_url = url["signedURL"]
        db.add(record)
        db.commit()
        db.refresh(record)

    return {"download_link": record.dowload_url}
