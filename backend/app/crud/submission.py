from sqlalchemy.orm import Session

from app.models.submission import Submission
from app.models.submission_upload import SubmissionUploadItem
from app.schemas.submission import SubmissionCreate, SubmissionUpdate


def create(db: Session, data: SubmissionCreate):
    submission = Submission(**data.model_dump())
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission


def get(db: Session, submission_id: int):
    return db.query(Submission).filter(Submission.id == submission_id).first()


def get_by_assignment(db: Session, assignment_id: int):
    return (
        db.query(Submission)
        .filter(Submission.assignment_id == assignment_id)
        .all()
    )


def get_many(db: Session, submission_ids: list[int]) -> list[Submission]:
    return db.query(Submission).filter(Submission.id.in_(submission_ids)).all()


def update(db: Session, submission: Submission, data: SubmissionUpdate):
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(submission, key, value)

    db.commit()
    db.refresh(submission)
    return submission


def delete(db: Session, submission: Submission):
    db.delete(submission)
    db.commit()


def delete_many(db: Session, submissions: list[Submission]) -> list[int]:
    submission_ids = [submission.id for submission in submissions]
    upload_items = (
        db.query(SubmissionUploadItem)
        .filter(SubmissionUploadItem.submission_id.in_(submission_ids))
        .all()
    )
    for item in upload_items:
        item.submission_id = None
    for submission in submissions:
        db.delete(submission)
    db.commit()
    return submission_ids