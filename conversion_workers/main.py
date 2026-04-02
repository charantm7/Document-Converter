import asyncio
from conversion_workers.queue.rabbitmq import init_rabbitmq
from conversion_workers.queue.consumer import start_consumer
from common_logging.configuration import setup_logging


async def main():
    setup_logging(service_name="conversion_workers")
    connection, channel, retry_exchange, dlx_exchange = await init_rabbitmq()
    await start_consumer(connection, channel, retry_exchange, dlx_exchange)


if __name__ == "__main__":
    asyncio.run(main())
