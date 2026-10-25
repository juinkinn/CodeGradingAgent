from sqlalchemy.orm import Session, selectinload

from app.models.submission_upload import (
    SubmissionUploadBatch,
    SubmissionUploadItem,
)


def create_batch(
    db: Session,
    assignment_id: int,
    files: list[tuple[str, str]],
) -> SubmissionUploadBatch:
    batch = SubmissionUploadBatch(assignment_id=assignment_id, status="QUEUED")
    batch.items = [
        SubmissionUploadItem(
            filename=filename,
            source_code=source_code,
            status="QUEUED",
        )
        for filename, source_code in files
    ]
    db.add(batch)
    db.commit()
    return get_batch(db, batch.id)


def get_batch(db: Session, batch_id: int) -> SubmissionUploadBatch | None:
    return (
        db.query(SubmissionUploadBatch)
        .options(selectinload(SubmissionUploadBatch.items))
        .filter(SubmissionUploadBatch.id == batch_id)
        .first()
    )
