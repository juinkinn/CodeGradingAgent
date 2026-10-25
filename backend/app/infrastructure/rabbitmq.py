import json

import pika

from app.env import settings


RABBITMQ_URL = settings.rabbitmq_url
SUBMISSION_UPLOAD_QUEUE = settings.submission_upload_queue


class RabbitMQUnavailable(RuntimeError):
    pass


def publish_submission_upload(batch_id: int) -> None:
    connection = None
    try:
        connection = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
        channel = connection.channel()
        channel.queue_declare(queue=SUBMISSION_UPLOAD_QUEUE, durable=True)
        channel.confirm_delivery()
        confirmed = channel.basic_publish(
            exchange="",
            routing_key=SUBMISSION_UPLOAD_QUEUE,
            body=json.dumps({"batch_id": batch_id}),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,
            ),
        )
        if confirmed is False:
            raise RabbitMQUnavailable("RabbitMQ did not confirm the upload batch.")
    except (pika.exceptions.AMQPError, OSError) as exc:
        raise RabbitMQUnavailable(
            "Could not publish the upload batch to RabbitMQ."
        ) from exc
    finally:
        if connection is not None and connection.is_open:
            try:
                connection.close()
            except (pika.exceptions.AMQPError, OSError) as exc:
                raise RabbitMQUnavailable(
                    "RabbitMQ connection could not be closed cleanly."
                ) from exc
