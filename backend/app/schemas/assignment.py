from pydantic import BaseModel, ConfigDict, field_validator


class AssignmentCreate(BaseModel):
    title: str
    description: str | None = None
    language: str
    min_score: float = 0
    max_score: float = 10
    rubric: str | dict | None = None


class AssignmentUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    language: str | None = None
    min_score: float | None = None
    max_score: float | None = None
    rubric: str | dict | None = None

    @field_validator("title", "language", "rubric")
    @classmethod
    def required_fields_cannot_be_null(
        cls,
        value: str | dict | None,
        info,
    ) -> str | dict | None:
        if value is None and info.field_name != "rubric":
            raise ValueError("field cannot be null")
        return value


class AssignmentResponse(AssignmentCreate):
    id: int

    model_config = ConfigDict(from_attributes=True)