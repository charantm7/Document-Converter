import aio_pika
import asyncio
from conversion_workers.settings import settings


async def get_rabbit_connection():
    retries = 10
    delay = 3

    for attempt in range(retries):
        try:
            print(f"[RabbitMQ] connection attempt {attempt+1}")

            connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)

            print("RabbitMQ Connected")
            return connection
        except Exception as e:
            print(f"[RabbitMQ] Failed: {e}")

            await asyncio.sleep(delay=delay)

    raise Exception("RabbitMQ connection failed after retries")
