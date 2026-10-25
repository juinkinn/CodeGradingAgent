from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.database import Base


class SubmissionUploadBatch(Base):
    __tablename__ = "submission_upload_batches"

    id = Column(Integer, primary_key=True, index=True)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=False)
    status = Column(String(30), nullable=False, default="QUEUED")
    created_at = Column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    assignment = relationship("Assignment", back_populates="upload_batches")
    items = relationship(
        "SubmissionUploadItem",
        back_populates="batch",
        cascade="all, delete-orphan",
    )


class SubmissionUploadItem(Base):
    __tablename__ = "submission_upload_items"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(
        Integer, ForeignKey("submission_upload_batches.id"), nullable=False
    )
    filename = Column(String(255), nullable=False)
    source_code = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="QUEUED")
    error = Column(Text, nullable=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=True)

    batch = relationship("SubmissionUploadBatch", back_populates="items")
    submission = relationship("Submission", back_populates="upload_items")
