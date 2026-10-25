from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class GradingSession(Base):
    __tablename__ = "grading_sessions"

    id = Column(Integer, primary_key=True, index=True)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=False)
    type = Column(String(30), default="GRADING")
    status = Column(String(30), default="PENDING")
    auto_approve = Column(Boolean, nullable=False, default=False)
    proposal = Column(JSON, nullable=True)
    grading_rubric = Column(JSON, nullable=True)

    assignment = relationship("Assignment", back_populates="grading_sessions")
    session_submissions = relationship(
        "SessionSubmission",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    messages = relationship(
        "GradingMessage",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    @property
    def submission_ids(self) -> list[int]:
        return [link.submission_id for link in self.session_submissions]


class SessionSubmission(Base):
    __tablename__ = "session_submissions"

    __table_args__ = (UniqueConstraint("session_id", "submission_id"),)

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("grading_sessions.id"), nullable=False)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False)
    proposed_score = Column(Float, nullable=True)
    feedback = Column(Text, nullable=True)
    status = Column(String(30), default="PENDING")

    session = relationship("GradingSession", back_populates="session_submissions")
    submission = relationship("Submission", back_populates="session_submissions")
