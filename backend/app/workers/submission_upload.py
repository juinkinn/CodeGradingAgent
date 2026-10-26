import json
import logging
import sys
import time
from datetime import datetime, timezone

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
    started_at = time.perf_counter()
    db = SessionLocal()
    try:
        batch = db.query(SubmissionUploadBatch).filter_by(id=batch_id).first()
        if batch is None or batch.status in {"COMPLETED", "COMPLETED_WITH_ERRORS"}:
            logger.info(
                "Skipping submission upload batch %s with status %s",
                batch_id,
                batch.status if batch is not None else "MISSING",
            )
            return

        queued_at = batch.queued_at or batch.created_at
        if queued_at is not None:
            queued_at = queued_at.replace(tzinfo=timezone.utc)
            queue_wait_seconds = max(
                0.0,
                (datetime.now(timezone.utc) - queued_at).total_seconds(),
            )
            logger.info(
                "Submission upload batch %s queue wait: %.3f seconds "
                "(queued_at=%s timestamp_source=%s)",
                batch_id,
                queue_wait_seconds,
                queued_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "queued_at" if batch.queued_at is not None else "created_at",
            )
        else:
            logger.warning(
                "Submission upload batch %s has no queued_at timestamp",
                batch_id,
            )

        batch.status = "PROCESSING"
        db.commit()
        logger.info("Started processing submission upload batch %s", batch_id)
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
        logger.info(
            "Completed submission upload batch %s: status=%s files=%s duration_ms=%.2f",
            batch_id,
            batch.status,
            len(item_ids),
            (time.perf_counter() - started_at) * 1000,
        )
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
            logger.info("Worker received submission upload batch %s", batch_id)
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
    logging.getLogger().setLevel(logging.INFO)
    try:
        run_worker()
    except pika.exceptions.AMQPError:
        logger.exception("Submission upload worker could not connect to RabbitMQ")
        sys.exit(1)
