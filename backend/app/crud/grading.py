from sqlalchemy.orm import Session

from app.models.grading_session import GradingSession, SessionSubmission
from app.models.grading_message import GradingMessage
from app.schemas.grading import GradingSessionCreate, GradingMessageCreate


def create_session(
    db: Session,
    data: GradingSessionCreate,
    *,
    session_type: str | None = None,
    auto_approve: bool = False,
    grading_rubric: dict | None = None,
):
    session = GradingSession(
        assignment_id=data.assignment_id,
        type=session_type or data.type,
        auto_approve=auto_approve,
        grading_rubric=grading_rubric,
    )
    db.add(session)
    db.flush()

    for submission_id in data.submission_ids:
        db.add(
            SessionSubmission(
                session_id=session.id,
                submission_id=submission_id,
            )
        )

    db.commit()
    db.refresh(session)
    return session


def get_session(db: Session, session_id: int):
    return db.query(GradingSession).filter(GradingSession.id == session_id).first()


def replace_session_submissions(
    db: Session,
    session: GradingSession,
    submission_ids: list[int],
) -> GradingSession:
    existing = {
        link.submission_id: link
        for link in session.session_submissions
    }
    requested = set(submission_ids)
    for submission_id, link in existing.items():
        if submission_id not in requested:
            db.delete(link)
    for submission_id in submission_ids:
        if submission_id not in existing:
            db.add(
                SessionSubmission(
                    session_id=session.id,
                    submission_id=submission_id,
                )
            )
    db.commit()
    db.refresh(session)
    return session


def get_sessions_by_assignment(db: Session, assignment_id: int):
    return (
        db.query(GradingSession)
        .filter(GradingSession.assignment_id == assignment_id)
        .all()
    )


def add_message(db: Session, session_id: int, data: GradingMessageCreate):
    message = GradingMessage(
        session_id=session_id,
        role=data.role,
        content=data.content,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def get_messages(db: Session, session_id: int):
    return (
        db.query(GradingMessage)
        .filter(GradingMessage.session_id == session_id)
        .order_by(GradingMessage.id)
        .all()
    )
