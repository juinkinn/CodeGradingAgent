from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class SubmissionCreate(BaseModel):
    assignment_id: int
    source_code: str
    filename: str | None = None


class SubmissionUpdate(BaseModel):
    source_code: str | None = None
    score: float | None = None

    @field_validator("source_code")
    @classmethod
    def source_code_cannot_be_null(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("source_code cannot be null")
        return value


class SubmissionResponse(BaseModel):
    id: int
    assignment_id: int
    filename: str | None
    source_code: str
    score: float | None
    status: str

    model_config = ConfigDict(from_attributes=True)


class SubmissionUploadItemResponse(BaseModel):
    id: int
    filename: str
    status: str
    error: str | None
    submission_id: int | None

    model_config = ConfigDict(from_attributes=True)


class SubmissionUploadBatchResponse(BaseModel):
    id: int
    assignment_id: int
    status: str
    created_at: datetime
    items: list[SubmissionUploadItemResponse]

    model_config = ConfigDict(from_attributes=True)
