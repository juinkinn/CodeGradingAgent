from sqlalchemy.orm import Session

from app.crud.grading import replace_session_submissions
from app.models.assignment import Assignment
from app.models.grading_message import GradingMessage
from app.models.grading_session import GradingSession, SessionSubmission
from app.models.submission import Submission
from app.services.grading_agent import (
    AgentDecision,
    GradesAgentResponse,
    ProposedGrade,
    RubricAndGradesAgentResponse,
    RubricAgentResponse,
    SubmissionForGrading,
    SubmissionOption,
    SubmissionSelectionAgentResponse,
    TextAgentResponse,
    run_grading_agent,
)


class InvalidAgentProposal(ValueError):
    pass


class SessionSubmissionsLocked(RuntimeError):
    pass


def _pending_rubric_change(session: GradingSession) -> dict | None:
    proposal = session.proposal
    if not proposal or proposal.get("status") != "PENDING_APPROVAL":
        return None
    if proposal.get("kind") == "rubric":
        return proposal
    if proposal.get("kind") == "changes":
        for change in proposal.get("changes", []):
            if change.get("kind") == "rubric":
                return change
    return None


def _build_context(db: Session, session: GradingSession) -> str:
    assignment = db.query(Assignment).filter_by(id=session.assignment_id).first()
    if assignment is None:
        raise ValueError("Assignment not found")

    pending_rubric = _pending_rubric_change(session)
    effective_rubric = (
        pending_rubric["rubric"]
        if pending_rubric is not None
        else session.grading_rubric
        if session.grading_rubric is not None
        else assignment.rubric
    )
    context_parts = [
        f"Assignment title: {assignment.title}",
        f"Description: {assignment.description or '(none)'}",
        f"Language: {assignment.language}",
        f"Score range: {assignment.min_score} to {assignment.max_score}",
        f"Assignment rubric (instructor-provided natural language or structured data): "
        f"{assignment.rubric}",
        f"Session grading rubric: "
        f"{session.grading_rubric if session.grading_rubric is not None else '(not set)'}",
        f"Rubric to use: {effective_rubric}",
        f"Rubric available for grading: {'yes' if effective_rubric is not None else 'no'}",
    ]

    links = (
        db.query(SessionSubmission, Submission)
        .join(Submission, Submission.id == SessionSubmission.submission_id)
        .filter(SessionSubmission.session_id == session.id)
        .order_by(SessionSubmission.id)
        .all()
    )
    context_parts.append(f"Attached submissions: {len(links)}")
    context_parts.append(
        "Attached submission IDs: "
        + (
            ", ".join(
                f"{submission.id} ({submission.filename or 'unnamed file'})"
                for _, submission in links
            )
            if links
            else "(none)"
        )
    )
    options = _get_submission_options(db, session)
    context_parts.append(
        "Available submissions: "
        + (
            ", ".join(
                f"ID {item['submission_id']} ({item['filename'] or 'unnamed file'})"
                for item in options
            )
            if options
            else "(none)"
        )
    )

    context_parts.append(f"Current proposal: {session.proposal or '(none)'}")
    context_parts.append(f"Auto-approve enabled: {session.auto_approve}")
    return "\n".join(context_parts)


def _get_submission_context(
    db: Session,
    session: GradingSession,
) -> list[SubmissionForGrading]:
    links = (
        db.query(SessionSubmission, Submission)
        .join(Submission, Submission.id == SessionSubmission.submission_id)
        .filter(SessionSubmission.session_id == session.id)
        .order_by(SessionSubmission.id)
        .all()
    )
    return [
        {
            "submission_id": submission.id,
            "filename": submission.filename,
            "current_score": submission.score,
            "source_code": submission.source_code,
        }
        for _, submission in links
    ]


def _get_submission_options(
    db: Session,
    session: GradingSession,
) -> list[SubmissionOption]:
    submissions = (
        db.query(Submission)
        .filter_by(assignment_id=session.assignment_id)
        .order_by(Submission.id)
        .all()
    )
    return [
        {"submission_id": item.id, "filename": item.filename}
        for item in submissions
    ]


def _validate_grades(
    db: Session,
    session: GradingSession,
    grades: list[ProposedGrade],
) -> None:
    if not grades:
        raise InvalidAgentProposal("The agent returned an empty grading proposal.")

    attached_ids = {
        submission_id
        for (submission_id,) in db.query(SessionSubmission.submission_id)
        .filter_by(session_id=session.id)
        .all()
    }
    seen_ids: set[int] = set()
    for grade in grades:
        if grade.submission_id not in attached_ids:
            raise InvalidAgentProposal(
                f"The agent returned a grade for unattached submission "
                f"{grade.submission_id}."
            )
        if grade.submission_id in seen_ids:
            raise InvalidAgentProposal(
                f"The agent returned submission {grade.submission_id} more than once."
            )
        if (
            not session.assignment.min_score
            <= grade.score
            <= session.assignment.max_score
        ):
            raise InvalidAgentProposal(
                f"The proposed score for submission {grade.submission_id} is outside "
                f"the assignment range "
                f"{session.assignment.min_score} to {session.assignment.max_score}."
            )
        seen_ids.add(grade.submission_id)


def _create_proposal(
    db: Session,
    session: GradingSession,
    decision: AgentDecision,
) -> dict | None:
    if isinstance(decision, RubricAgentResponse):
        if not decision.rubric:
            return None
        target = "session" if session.grading_rubric is not None else "assignment"
        return {
            "kind": "rubric",
            "rubric": decision.rubric,
            "target": target,
        }

    if isinstance(decision, RubricAndGradesAgentResponse):
        if not decision.rubric:
            raise InvalidAgentProposal(
                "The agent did not return a rubric for the grading proposal."
            )
        changes = [
            {
                "kind": "rubric",
                "rubric": decision.rubric,
                "target": (
                    "session"
                    if session.grading_rubric is not None
                    else "assignment"
                ),
            }
        ]
        if decision.grades:
            _validate_grades(db, session, decision.grades)
            changes.append(
                {
                    "kind": "grades",
                    "grades": [grade.model_dump() for grade in decision.grades],
                }
            )
        return {"kind": "changes", "changes": changes}

    if isinstance(decision, GradesAgentResponse):
        if not decision.grades:
            return None
        _validate_grades(db, session, decision.grades)
        return {
            "kind": "grades",
            "grades": [grade.model_dump() for grade in decision.grades],
        }

    if isinstance(decision, SubmissionSelectionAgentResponse):
        return None

    if not isinstance(decision, TextAgentResponse):
        raise InvalidAgentProposal("The agent returned an unknown response type.")
    return None


def apply_proposal(
    db: Session,
    session: GradingSession,
    proposal: dict,
) -> None:
    if proposal["kind"] == "changes":
        for change in proposal["changes"]:
            apply_proposal(db, session, change)
        return

    if proposal["kind"] == "rubric":
        if proposal.get("target") == "session":
            session.grading_rubric = proposal["rubric"]
        else:
            session.assignment.rubric = proposal["rubric"]
        return

    if proposal["kind"] == "grades":
        _validate_grades(
            db,
            session,
            [ProposedGrade.model_validate(grade) for grade in proposal["grades"]],
        )
        for grade_data in proposal["grades"]:
            grade = ProposedGrade.model_validate(grade_data)
            link = (
                db.query(SessionSubmission)
                .filter_by(
                    session_id=session.id,
                    submission_id=grade.submission_id,
                )
                .first()
            )
            submission = db.query(Submission).filter_by(id=grade.submission_id).first()
            if link is None or submission is None:
                raise InvalidAgentProposal(
                    f"Submission {grade.submission_id} is no longer attached "
                    "to this grading session."
                )
            link.proposed_score = grade.score
            link.feedback = grade.feedback
            link.status = "APPROVED"
            submission.score = grade.score
        return

    raise InvalidAgentProposal("Unknown proposal type.")


def chat_with_agent(
    db: Session,
    session: GradingSession,
    content: str,
) -> tuple[GradingMessage, GradingMessage]:
    user_message = GradingMessage(
        session_id=session.id,
        role="user",
        content=content,
    )
    db.add(user_message)
    db.commit()
    db.refresh(user_message)

    history = [
        (message.role, message.content)
        for message in (
            db.query(GradingMessage)
            .filter_by(session_id=session.id)
            .order_by(GradingMessage.id)
            .all()
        )
    ]
    submission_context = _get_submission_context(db, session)
    available_submissions = _get_submission_options(db, session)
    decision = run_grading_agent(
        context=_build_context(db, session),
        history=history,
        session_id=session.id,
        has_submissions=bool(submission_context),
        submissions=submission_context,
        available_submissions=available_submissions,
    )
    if isinstance(decision, SubmissionSelectionAgentResponse):
        if (
            session.proposal
            and session.proposal.get("status") == "PENDING_APPROVAL"
        ):
            raise SessionSubmissionsLocked(
                "Approve or replace the pending proposal before changing "
                "the session submissions."
            )
        available_ids = {
            item["submission_id"] for item in available_submissions
        }
        requested_ids = (
            decision.add_submission_ids + decision.remove_submission_ids
        )
        if any(submission_id not in available_ids for submission_id in requested_ids):
            raise InvalidAgentProposal(
                "The agent requested a submission outside this assignment."
            )
        if set(decision.add_submission_ids) & set(decision.remove_submission_ids):
            raise InvalidAgentProposal(
                "The agent requested conflicting submission selection changes."
            )
        current_ids = {item["submission_id"] for item in submission_context}
        selected_ids = (
            (current_ids - set(decision.remove_submission_ids))
            | set(decision.add_submission_ids)
        )
        ordered_ids = [
            item["submission_id"]
            for item in available_submissions
            if item["submission_id"] in selected_ids
        ]
        replace_session_submissions(db, session, ordered_ids)

    proposal = _create_proposal(db, session, decision)
    response = decision.response.strip()
    if not response:
        raise InvalidAgentProposal("The agent returned an empty response.")

    if proposal is not None:
        pending_rubric = _pending_rubric_change(session)
        if (
            proposal["kind"] == "grades"
            and pending_rubric is not None
        ):
            proposal = {
                "kind": "changes",
                "changes": [
                    {
                        key: value
                        for key, value in pending_rubric.items()
                        if key != "status"
                    },
                    proposal,
                ],
            }
        proposal["status"] = "PENDING_APPROVAL"
        if session.auto_approve:
            apply_proposal(db, session, proposal)
            proposal["status"] = "APPROVED"
            session.status = "ACTIVE"
            response += "\n\nProposal automatically approved by this session's setting."
        else:
            session.status = "AWAITING_APPROVAL"
            response += (
                "\n\nThis is a proposal. Review and approve it to save the changes."
            )
        session.proposal = proposal
    elif session.proposal and session.proposal.get("status") == "PENDING_APPROVAL":
        response += "\n\nThe existing proposal is still waiting for your approval."

    assistant_message = GradingMessage(
        session_id=session.id,
        role="assistant",
        content=response,
    )
    db.add(assistant_message)
    db.commit()
    db.refresh(assistant_message)
    return user_message, assistant_message
