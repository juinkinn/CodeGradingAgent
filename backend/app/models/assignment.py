from sqlalchemy import Column, ForeignKey, Integer, String, Text, Float, JSON
from sqlalchemy.orm import relationship

from app.database import Base


class Assignment(Base):
    __tablename__ = "assignments"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    language = Column(String(50), nullable=False)
    min_score = Column(Float, default=0)
    max_score = Column(Float, default=10)
    rubric = Column(JSON, nullable=True)

    submissions = relationship(
        "Submission",
        back_populates="assignment",
        cascade="all, delete-orphan",
    )
    grading_sessions = relationship(
        "GradingSession",
        back_populates="assignment",
        cascade="all, delete-orphan",
    )
    upload_batches = relationship(
        "SubmissionUploadBatch",
        back_populates="assignment",
        cascade="all, delete-orphan",
    )