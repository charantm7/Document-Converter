import asyncio
import json
import aio_pika

from conversion_workers.storage.s3_client import supabase
from conversion_workers.converter.worker import Conversion, Compression, Customization
from shared_database.connection import SessionLocal
from conversion_workers.queue.connection import get_rabbit_connection


MAX_RETRIES = 3


async def process_job(data):

    target_format = data["target_format"]
    user_id = data["user_id"]
    job_id = data["job_id"]
    path = data["path"]
    db = SessionLocal()

    pdf_to_formats = ["docx", "pptx"]

    if target_format in pdf_to_formats:
        await asyncio.to_thread(
            Conversion(supabase, db).convert_pdf_to_file,
            job_id,
            path,
            target_format
        )

    elif target_format == "pdf":
        source_format = data["source_format"]
        await asyncio.to_thread(
            Conversion(supabase, db).convert_file_to_pdf,
            job_id,
            path,
            target_format,
            source_format
        )

    elif target_format == "merge":
        await asyncio.to_thread(
            Customization(supabase, db).merge_pdf,
            job_id,
            path
        )

    elif target_format == "compress":
        await asyncio.to_thread(
            Compression(supabase, db).compress_pdf,
            job_id,
            path
        )

    else:
        raise ValueError("Unsupported Format")


async def start_consumer(queue, retry_exchange, dlx_exchange):

    print(f"[worker] waiting for conversion job...")

    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            try:
                data = json.loads(message.body)
                job_id = data["job_id"]
                retry_count = data.get("retry_count", 0)

                print("RECEIVED:", data)

                await process_job(data)

                await message.ack()

                print("SUCCESS:", job_id)

            except Exception as e:
                import traceback
                print("ERROR:")
                traceback.print_exc()

                await message.reject(requeue=False)

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
                else:
                    await dlx_exchange.publish(
                        aio_pika.Message(
                            body=message.body,
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                        ),
                        routing_key="dead"
                    )
