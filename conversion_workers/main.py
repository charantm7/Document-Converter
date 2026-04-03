from common_logging.configuration import setup_logging
from conversion_workers.queue.consumer import start_consumer
from conversion_workers.queue.rabbitmq import init_rabbitmq

import asyncio


async def main():
    setup_logging(service_name="conversion-workers")
    queue, retry_exchange, dlx_exchange = await init_rabbitmq()
    await start_consumer(queue, retry_exchange, dlx_exchange)

if __name__ == "__main__":
    asyncio.run(main())
