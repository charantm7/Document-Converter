import asyncio
import json
import aio_pika

from conversion_workers.storage.s3_client import supabase
from conversion_workers.settings import settings
from conversion_workers.converter.worker import Conversion, Compression, Customization
from shared_database.connection import SessionLocal

MAX_RETRIES = 3


def process_job(data):
    target_format = data["target_format"]
    user_id = data["user_id"]
    job_id = data["job_id"]
    path = data["path"]
    db = SessionLocal()

    if target_format == "docx":
        Conversion(supabase, db).convert_pdf_to_docx(
            job_id, path, user_id)

    elif target_format == "pdf":
        Conversion(supabase, db).convert_docx_to_pdf(
            job_id, path, user_id)

    elif target_format == "pptx":
        Conversion(supabase, db).convert_pdf_to_ppt(
            job_id, path, user_id)

    elif target_format == "merge":
        Customization(supabase).merge_pdf(
            job_id, path, user_id)

    elif target_format == "compress":
        Compression(supabase).compress_pdf(
            job_id, path, user_id)

    else:
        raise ValueError("Unsupported Formate")


async def start_consumer(connection, channel, retry_exchange, dlx_exchange):

    queue = await channel.get_queue("main_queue")

    print(f"[worker] waiting for conversion job...")

    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            async with message.process(requeue=False):

                data = json.loads(message.body)
                job_id = data["job_id"]
                retry_count = data["retry_count"]
                try:
                    print(f"[worker] processing {job_id}, retry={retry_count}")

                    process_job(data)

                    print(f"[worker] finished job {job_id}")

                except Exception as e:
                    print(f"[Worker] Job failed: {e}")

                    if retry_count < MAX_RETRIES:
                        retry_count += 1

                        await retry_exchange.publish(
                            aio_pika.Message(
                                body=json.dumps({
                                    **data,
                                    "retry_count": retry_count,
                                }).encode(),
                                delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                            ),
                            routing_key="retry"
                        )
                        print(f"[worker] retry {job_id} ({retry_count})")
                    else:

                        await dlx_exchange.publish(
                            aio_pika.Message(
                                body=message.body,
                                delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                            ),
                            routing_key="dead"
                        )
                        print(f"[worker] moved to DLQ {job_id}")
