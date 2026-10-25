from sqlalchemy import Column, Integer, String, Text, Float, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


class Submission(Base):
    __tablename__ = "submissions"

    id = Column(Integer, primary_key=True, index=True)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=False)
    filename = Column(String(255), nullable=True)
    source_code = Column(Text, nullable=False)
    score = Column(Float, nullable=True)
    status = Column(String(30), default="PENDING")

    assignment = relationship("Assignment", back_populates="submissions")
    session_submissions = relationship(
        "SessionSubmission",
        back_populates="submission",
        cascade="all, delete-orphan",
    )
    upload_items = relationship(
        "SubmissionUploadItem",
        back_populates="submission",
    )
