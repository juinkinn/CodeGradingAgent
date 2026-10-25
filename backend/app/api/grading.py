import json
import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.crud import assignment as assignment_crud
from app.crud import grading as grading_crud
from app.crud import submission as submission_crud
from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.schemas.grading import (
    AgentChatRequest,
    AgentChatResponse,
    AgentSessionCreate,
    AgentSessionSubmissionsUpdate,
    AgentSessionSettingsUpdate,
    GradingMessageCreate,
    GradingMessageResponse,
    GradingSessionCreate,
    GradingSessionResponse,
)
from app.services.grading_agent import AgentInvocationError, AgentNotConfigured
from app.services.grading_agent_workflow import (
    InvalidAgentProposal,
    SessionSubmissionsLocked,
    apply_proposal,
    chat_with_agent,
)


router = APIRouter(tags=["grading"])
logger = logging.getLogger(__name__)


@router.post(
    "/grading-sessions",
    response_model=GradingSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_grading_session(
    data: GradingSessionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assignment = assignment_crud.get(db, data.assignment_id)
    if assignment is None or assignment.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignment not found",
        )

    if len(data.submission_ids) != len(set(data.submission_ids)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="submission_ids must not contain duplicates",
        )

    for submission_id in data.submission_ids:
        submission = submission_crud.get(db, submission_id)
        if submission is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Submission {submission_id} not found",
            )
        if submission.assignment_id != data.assignment_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Submission {submission_id} does not belong to "
                    f"assignment {data.assignment_id}"
                ),
            )

    return grading_crud.create_session(db, data)


@router.get(
    "/assignments/{assignment_id}/grading-sessions",
    response_model=list[GradingSessionResponse],
)
def get_grading_sessions_by_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
):
    if assignment_crud.get(db, assignment_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignment not found",
        )
    return grading_crud.get_sessions_by_assignment(db, assignment_id)


@router.get(
    "/grading-sessions/{session_id}",
    response_model=GradingSessionResponse,
)
def get_grading_session(session_id: int, db: Session = Depends(get_db)):
    session = grading_crud.get_session(db, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grading session not found",
        )
    return session


@router.post(
    "/grading-sessions/{session_id}/messages",
    response_model=GradingMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_grading_message(
    session_id: int,
    data: GradingMessageCreate,
    db: Session = Depends(get_db),
):
    if grading_crud.get_session(db, session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grading session not found",
        )
    return grading_crud.add_message(db, session_id, data)


@router.get(
    "/grading-sessions/{session_id}/messages",
    response_model=list[GradingMessageResponse],
)
def get_grading_messages(session_id: int, db: Session = Depends(get_db)):
    if grading_crud.get_session(db, session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grading session not found",
        )
    return grading_crud.get_messages(db, session_id)


@router.post(
    "/assignments/{assignment_id}/agent-sessions",
    response_model=GradingSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_agent_session(
    assignment_id: int,
    data: AgentSessionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assignment = assignment_crud.get(db, assignment_id)
    if assignment is None or assignment.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignment not found",
        )
    if len(data.submission_ids) != len(set(data.submission_ids)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="submission_ids must not contain duplicates",
        )
    for submission_id in data.submission_ids:
        submission = submission_crud.get(db, submission_id)
        if submission is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Submission {submission_id} not found",
            )
        if submission.assignment_id != assignment_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Submission {submission_id} does not belong to "
                    f"assignment {assignment_id}"
                ),
            )

    session_data = GradingSessionCreate(
        assignment_id=assignment_id,
        type="AI_ASSISTANT",
        submission_ids=data.submission_ids,
    )
    return grading_crud.create_session(
        db,
        session_data,
        session_type="AI_ASSISTANT",
        auto_approve=data.auto_approve,
        grading_rubric=data.rubric,
    )


@router.post(
    "/grading-sessions/{session_id}/chat",
    response_model=AgentChatResponse,
)
def chat_with_grading_agent(
    session_id: int,
    data: AgentChatRequest,
    db: Session = Depends(get_db),
):
    session = grading_crud.get_session(db, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grading session not found",
        )
    if session.type != "AI_ASSISTANT":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This grading session is not an AI assistant chat.",
        )
    try:
        user_message, message = chat_with_agent(db, session, data.content)
    except AgentNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except AgentInvocationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except SessionSubmissionsLocked as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except InvalidAgentProposal as exc:
        logger.exception("Grading agent returned an invalid proposal")
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The grading agent returned an invalid proposal. Please try again.",
        ) from exc
    db.refresh(session)
    return {
        "session": session,
        "user_message": user_message,
        "message": message,
    }


@router.post("/grading-sessions/{session_id}/chat/stream")
def stream_chat_with_grading_agent(
    session_id: int,
    data: AgentChatRequest,
    db: Session = Depends(get_db),
):
    session = grading_crud.get_session(db, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grading session not found",
        )
    if session.type != "AI_ASSISTANT":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This grading session is not an AI assistant chat.",
        )

    def event_stream() -> Iterator[str]:
        def encode_event(event: str, payload: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

        def process_chat() -> dict:
            user_message, message = chat_with_agent(db, session, data.content)
            db.refresh(session)
            return jsonable_encoder(
                AgentChatResponse(
                    session=session,
                    user_message=user_message,
                    message=message,
                )
            )

        yield encode_event(
            "progress",
            {"status": "started", "message": "Đã nhận tin nhắn, đang xử lý..."},
        )
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(process_chat)
                while True:
                    try:
                        result = future.result(timeout=10)
                        break
                    except FutureTimeoutError:
                        yield encode_event(
                            "progress",
                            {
                                "status": "processing",
                                "message": "Agent vẫn đang phân tích yêu cầu và bài làm...",
                            },
                        )
            yield encode_event("complete", result)
        except AgentNotConfigured as exc:
            yield encode_event(
                "error",
                {"status": status.HTTP_503_SERVICE_UNAVAILABLE, "detail": str(exc)},
            )
        except AgentInvocationError as exc:
            yield encode_event(
                "error",
                {"status": status.HTTP_502_BAD_GATEWAY, "detail": str(exc)},
            )
        except SessionSubmissionsLocked as exc:
            db.rollback()
            yield encode_event(
                "error",
                {"status": status.HTTP_409_CONFLICT, "detail": str(exc)},
            )
        except InvalidAgentProposal:
            logger.exception("Grading agent returned an invalid proposal")
            db.rollback()
            yield encode_event(
                "error",
                {
                    "status": status.HTTP_502_BAD_GATEWAY,
                    "detail": (
                        "The grading agent returned an invalid proposal. "
                        "Please try again."
                    ),
                },
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.patch(
    "/grading-sessions/{session_id}/submissions",
    response_model=GradingSessionResponse,
)
def update_grading_session_submissions(
    session_id: int,
    data: AgentSessionSubmissionsUpdate,
    db: Session = Depends(get_db),
):
    session = grading_crud.get_session(db, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grading session not found",
        )
    if session.type != "AI_ASSISTANT":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This grading session is not an AI assistant chat.",
        )
    if len(data.submission_ids) != len(set(data.submission_ids)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="submission_ids must not contain duplicates",
        )
    if (
        session.proposal
        and session.proposal.get("status") == "PENDING_APPROVAL"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Approve or replace the pending proposal before changing "
                "the session submissions."
            ),
        )
    for submission_id in data.submission_ids:
        submission = submission_crud.get(db, submission_id)
        if submission is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Submission {submission_id} not found",
            )
        if submission.assignment_id != session.assignment_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Submission {submission_id} does not belong to "
                    f"assignment {session.assignment_id}"
                ),
            )
    return grading_crud.replace_session_submissions(
        db,
        session,
        data.submission_ids,
    )


@router.post(
    "/grading-sessions/{session_id}/approve",
    response_model=GradingSessionResponse,
)
def approve_grading_agent_proposal(
    session_id: int,
    db: Session = Depends(get_db),
):
    session = grading_crud.get_session(db, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grading session not found",
        )
    if session.type != "AI_ASSISTANT":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This grading session is not an AI assistant chat.",
        )
    if not session.proposal or session.proposal.get("status") != "PENDING_APPROVAL":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="There is no proposal waiting for approval.",
        )
    try:
        apply_proposal(db, session, session.proposal)
    except InvalidAgentProposal as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    session.proposal = {**session.proposal, "status": "APPROVED"}
    session.status = "ACTIVE"
    db.commit()
    db.refresh(session)
    return session


@router.patch(
    "/grading-sessions/{session_id}/settings",
    response_model=GradingSessionResponse,
)
def update_grading_agent_settings(
    session_id: int,
    data: AgentSessionSettingsUpdate,
    db: Session = Depends(get_db),
):
    session = grading_crud.get_session(db, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grading session not found",
        )
    if session.type != "AI_ASSISTANT":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This grading session is not an AI assistant chat.",
        )
    if (
        "rubric" in data.model_fields_set
        and session.proposal
        and session.proposal.get("status") == "PENDING_APPROVAL"
        and data.rubric != session.grading_rubric
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Resolve the pending proposal in chat before changing the "
                "session rubric."
            ),
        )
    if "auto_approve" in data.model_fields_set:
        session.auto_approve = data.auto_approve
    if "rubric" in data.model_fields_set:
        session.grading_rubric = data.rubric
    db.commit()
    db.refresh(session)
    return session
