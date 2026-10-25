from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class GradingSessionCreate(BaseModel):
    assignment_id: int
    type: str = "GRADING"
    submission_ids: list[int]


class GradingSessionResponse(BaseModel):
    id: int
    assignment_id: int
    type: str
    status: str
    auto_approve: bool = False
    proposal: dict | None = None
    grading_rubric: dict | None = None
    submission_ids: list[int] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class GradingMessageCreate(BaseModel):
    role: str = "user"
    content: str


class GradingMessageResponse(BaseModel):
    id: int
    session_id: int
    role: str
    content: str

    model_config = ConfigDict(from_attributes=True)


class AgentSessionCreate(BaseModel):
    submission_ids: list[int] = Field(default_factory=list)
    auto_approve: bool = False
    rubric: dict | None = None


class AgentSessionSubmissionsUpdate(BaseModel):
    submission_ids: list[int]


class AgentChatRequest(BaseModel):
    content: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]


class AgentChatResponse(BaseModel):
    session: GradingSessionResponse
    user_message: GradingMessageResponse
    message: GradingMessageResponse


class AgentSessionSettingsUpdate(BaseModel):
    auto_approve: bool | None = None
    rubric: dict | None = None

    @model_validator(mode="after")
    def require_setting(self) -> "AgentSessionSettingsUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one setting must be provided.")
        return self
