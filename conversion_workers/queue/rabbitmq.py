from conversion_workers.queue.connection import get_rabbitmq_connection


connection = None
channel = None


main_exchange = None
retry_exchange = None
dlx_exchange = None


async def init_rabbitmq():
    global connection, channel
    global main_exchange, retry_exchange, dlx_exchange

    connection = await get_rabbitmq_connection()
    channel = await connection.channel()

    main_exchange = await channel.declare_exchange("main_exchange", durable=True)
    retry_exchange = await channel.declare_exchange("retry_exchange", durable=True)
    dlx_exchange = await channel.declare_exchange("dlx_exchange", durable=True)

    main_queue = await channel.declare_queue(
        "main_queue",
        durable=True,
        arguments={
            "x-dead-letter-exchange": "retry_exchange",
            "x-dead-letter-routing-key": "retry"
        }
    )

    retry_queue = await channel.declare_queue(
        "retry_queue",
        durable=True,
        arguments={
            "x-message-ttl": 5000,
            "x-dead-letter-exchange": "main_exchange",
            "x-dead-letter-routing-key": "main"
        }
    )

    dead_queue = await channel.declare_queue(
        "dead_letter_queue",
        durable=True
    )

    await main_queue.bind(main_exchange, routing_key="main")
    await retry_queue.bind(retry_exchange, routing_key="retry")
    await dead_queue.bind(dlx_exchange, routing_key="dead")

    print(f"[RabbitMQ] ready")
    return connection, channel, retry_exchange, dlx_exchange
