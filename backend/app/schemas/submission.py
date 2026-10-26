from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class SubmissionBulkDeleteRequest(BaseModel):
    ids: list[int] = Field(min_length=1)

    @field_validator("ids")
    @classmethod
    def submission_ids_must_be_unique(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("submission ids must be unique")
        return value


class SubmissionBulkDeleteResponse(BaseModel):
    deleted_ids: list[int]


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
    queued_at: datetime | None
    items: list[SubmissionUploadItemResponse]

    model_config = ConfigDict(from_attributes=True)
