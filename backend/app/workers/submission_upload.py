import json
import logging
import sys

import pika
from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal
from app.infrastructure.rabbitmq import (
    RABBITMQ_URL,
    SUBMISSION_UPLOAD_QUEUE,
)
from app.models.submission import Submission
from app.models.submission_upload import (
    SubmissionUploadBatch,
    SubmissionUploadItem,
)


logger = logging.getLogger(__name__)


def process_batch(batch_id: int) -> None:
    db = SessionLocal()
    try:
        batch = db.query(SubmissionUploadBatch).filter_by(id=batch_id).first()
        if batch is None or batch.status in {"COMPLETED", "COMPLETED_WITH_ERRORS"}:
            return

        batch.status = "PROCESSING"
        db.commit()
        item_ids = [
            item_id
            for (item_id,) in db.query(SubmissionUploadItem.id)
            .filter_by(batch_id=batch_id, status="QUEUED")
            .order_by(SubmissionUploadItem.id)
            .all()
        ]

        for item_id in item_ids:
            item = (
                db.query(SubmissionUploadItem)
                .filter_by(id=item_id, status="QUEUED")
                .first()
            )
            if item is None:
                continue
            if item.source_code is None:
                db.query(SubmissionUploadItem).filter_by(
                    id=item_id,
                    status="QUEUED",
                ).update(
                    {
                        SubmissionUploadItem.status: "FAILED",
                        SubmissionUploadItem.error: "Uploaded source content is missing.",
                    },
                    synchronize_session=False,
                )
                db.commit()
                continue

            submission = Submission(
                assignment_id=batch.assignment_id,
                filename=item.filename,
                source_code=item.source_code,
            )
            db.add(submission)
            db.flush()
            updated_items = (
                db.query(SubmissionUploadItem)
                .filter_by(id=item_id, status="QUEUED")
                .update(
                    {
                        SubmissionUploadItem.submission_id: submission.id,
                        SubmissionUploadItem.source_code: None,
                        SubmissionUploadItem.status: "COMPLETED",
                        SubmissionUploadItem.error: None,
                    },
                    synchronize_session=False,
                )
            )
            if updated_items != 1:
                db.rollback()
                continue
            db.commit()

        batch = db.query(SubmissionUploadBatch).filter_by(id=batch_id).first()
        if batch is None:
            return
        failed_count = (
            db.query(SubmissionUploadItem)
            .filter_by(batch_id=batch_id, status="FAILED")
            .count()
        )
        batch.status = "COMPLETED_WITH_ERRORS" if failed_count else "COMPLETED"
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    finally:
        db.close()


def run_worker() -> None:
    connection = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
    channel = connection.channel()
    channel.queue_declare(queue=SUBMISSION_UPLOAD_QUEUE, durable=True)
    channel.basic_qos(prefetch_count=1)

    def handle_message(channel, method, _properties, body):
        try:
            message = json.loads(body)
            batch_id = message["batch_id"]
            if type(batch_id) is not int or batch_id < 1:
                raise ValueError("batch_id must be a positive integer")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.exception("Discarding invalid submission upload message")
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        try:
            process_batch(batch_id)
        except SQLAlchemyError:
            logger.exception("Could not process submission upload batch %s", batch_id)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return
        channel.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(
        queue=SUBMISSION_UPLOAD_QUEUE,
        on_message_callback=handle_message,
    )
    logger.info("Waiting for submission upload batches")
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        channel.stop_consuming()
    finally:
        if connection.is_open:
            connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        run_worker()
    except pika.exceptions.AMQPError:
        logger.exception("Submission upload worker could not connect to RabbitMQ")
        sys.exit(1)
