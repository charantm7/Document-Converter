import json
import aio_pika
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Header, status, Request, Depends
from sqlalchemy.orm import Session

from upload_service.src.api.v1.upload_route.schema import PreSignedSchema, ConvertRequest, MergeRequest, CompressRequest
from upload_service.src.api.v1.upload_route.service import build_storage_path
from upload_service.src.config.rabbitmq_connection import get_rabbit_connection
from upload_service.src.queue.producer import publish_job
from upload_service.src.storage.supabase_client import supabase
from shared_database.connection import get_db
from shared_database.repository import UserRepository, JobRepository
from shared_database.models import JobStatus
from upload_service.settings import settings

upload_service = APIRouter()


@upload_service.post("/upload/presigned")
async def generate_presigned_url(
    body: PreSignedSchema,
    user_id: str = Header(..., alias="User-Id"),
    db: Session = Depends(get_db)
):
    if not body.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Filename Required")

    file_path, job_id = build_storage_path(
        user_id=user_id, filename=body.filename)

    repo = JobRepository(db)

    record = repo.get_by_job_id(job_id)

    if record:
        return {
            "upload_url": record.upload_url,
            "path": record.input_url,
            "job_id": record.id,
            "bucket": settings.SUPABASE_BUCKET,
            "status": record.status
        }

    try:

        signed = supabase.storage.from_(
            settings.SUPABASE_BUCKET).create_signed_upload_url(file_path)
    except Exception as e:
        raise HTTPException(500, f"Supabase error: {str(e)}")

    repo.create(
        id=job_id,
        user_id=user_id,
        status=JobStatus.processing,
        upload_url=signed["signedUrl"]
    )

    return {
        "upload_url": signed["signedUrl"],
        "path": file_path,
        "job_id": job_id,
        "bucket": settings.SUPABASE_BUCKET
    }


@upload_service.post("/merge/start")
async def merge_files(body: MergeRequest, user_id: str = Header(..., alias="User-Id"), db: Session = Depends(get_db)):

    job_id = str(uuid4())
    repo = JobRepository(db)

    repo.create(
        id=job_id,
        user_id=user_id,
        status=JobStatus.processing,
    )

    message = {
        "job_id": job_id,
        "path": body.path,
        "user_id": user_id,
        "target_format": body.target_format
    }

    await publish_job(message)

    return {"message": "Merge job queued", "job_id": job_id}


@upload_service.post("/compress/start")
async def compress_files(body: CompressRequest, user_id: str = Header(..., alias="User-Id")):

    message = {
        "job_id": body.job_id,
        "path": body.path,
        "user_id": user_id,
        "target_format": body.target_format
    }

    await publish_job(message)

    return {"message": "Merge job queued", "job_id": body.job_id}


@upload_service.post("/conversion/start")
async def convert_file(body: ConvertRequest, request: Request):

    user_id = request.headers.get("User-Id")

    message = {
        "user_id": user_id,
        "job_id": body.job_id,
        "path": body.path,
        "target_format": body.target_format,
        "source_format": body.source_format
    }

    await publish_job(message)

    return {"message": "Conversion job queued", "job_id": body.job_id}
