import os
import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database
from app.main import app
from app.models.submission_upload import SubmissionUploadBatch, SubmissionUploadItem
from app.services.grading_agent import (
    AgentNotConfigured,
    AgentIntent,
    GradesAgentResponse,
    ProposedGrade,
    RubricAndGradesAgentResponse,
    RubricAgentResponse,
    SubmissionSelectionAgentResponse,
    TextAgentResponse,
    AgentInvocationError,
    _resolve_system_prompt,
    run_grading_agent,
)


class GradingAgentLangfuseTests(unittest.TestCase):
    def test_prompt_resolution_requires_langfuse(self):
        with patch.dict(
            os.environ,
            {"LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""},
        ):
            with self.assertRaisesRegex(AgentNotConfigured, "both LANGFUSE"):
                run_grading_agent(
                    context="assignment context",
                    history=[("user", "Hello")],
                )

    def test_remote_prompt_is_compiled_with_the_configured_label(self):
        with patch.dict(
            os.environ,
            {
                "LANGFUSE_PUBLIC_KEY": "public-test-key",
                "LANGFUSE_SECRET_KEY": "secret-test-key",
                "LANGFUSE_PROMPT_LABEL": "staging",
            },
        ), patch("app.services.grading_agent.get_client") as get_client:
            prompt = get_client.return_value.get_prompt.return_value
            prompt.variables = ["context"]
            prompt.compile.return_value = "managed prompt"

            resolved, linked_prompt = _resolve_system_prompt(
                "grading-agent-response",
                context="assignment context",
            )

        self.assertEqual(resolved, "managed prompt")
        self.assertIs(linked_prompt, prompt)
        get_client.return_value.get_prompt.assert_called_once_with(
            "grading-agent-response",
            type="text",
            label="staging",
        )
        prompt.compile.assert_called_once_with(context="assignment context")

    def test_partial_langfuse_credentials_are_rejected(self):
        with patch.dict(
            os.environ,
            {"LANGFUSE_PUBLIC_KEY": "public-test-key", "LANGFUSE_SECRET_KEY": ""},
        ):
            with self.assertRaisesRegex(AgentNotConfigured, "both LANGFUSE"):
                run_grading_agent(
                    context="assignment context",
                    history=[("user", "Hello")],
                )

    def test_prompt_resolution_rejects_unresolved_template_variables(self):
        with patch(
            "app.services.grading_agent.get_client"
        ) as get_client:
            prompt = get_client.return_value.get_prompt.return_value
            prompt.variables = ["context", "routing_instructions"]

            with self.assertRaisesRegex(
                AgentInvocationError,
                "routing_instructions",
            ):
                _resolve_system_prompt(
                    "grading-agent-router",
                    context="assignment context",
                )
        prompt.compile.assert_not_called()

    def test_agent_invocation_adds_session_scoped_langfuse_tracing(self):
        with patch.dict(
            os.environ,
            {
                "LANGFUSE_PUBLIC_KEY": "public-test-key",
                "LANGFUSE_SECRET_KEY": "secret-test-key",
                "GEMINI_MODEL": "gemini-3.8-flash",
            },
        ), patch("app.services.grading_agent._build_graph") as build_graph, patch(
            "app.services.grading_agent.get_client"
        ), patch(
            "app.services.grading_agent.propagate_attributes",
            return_value=nullcontext(),
        ) as propagate_attributes, patch(
            "app.services.grading_agent.CallbackHandler"
        ) as callback_handler:
            build_graph.return_value.invoke.return_value = {
                "text_response": TextAgentResponse(response="Ready.")
            }

            result = run_grading_agent(
                context="assignment context",
                history=[("user", "Hello")],
                session_id=42,
            )

        self.assertEqual(result.response, "Ready.")
        propagate_attributes.assert_called_once_with(
            tags=["grading-agent"],
            metadata={"model": "gemini-3.8-flash"},
            session_id="42",
        )
        invocation_config = build_graph.return_value.invoke.call_args.kwargs["config"]
        self.assertEqual(invocation_config["run_name"], "grading-agent")
        self.assertEqual(invocation_config["callbacks"], [callback_handler.return_value])


class GradingAgentApiTests(unittest.TestCase):
    def setUp(self):
        self.auth_env = patch.dict(
            os.environ,
            {"AUTH_SECRET_KEY": "test-only-session-secret"},
        )
        self.auth_env.start()
        self.password_iterations = patch(
            "app.services.auth.PASSWORD_ITERATIONS",
            10_000,
        )
        self.password_iterations.start()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.engine = self.engine
        database.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
        )
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        registration = self.client.post(
            "/auth/register",
            json={"email": "test@example.com", "password": "test-password-123"},
        )
        self.assertEqual(registration.status_code, 201, registration.text)
        assignment_response = self.client.post(
            "/assignments",
            json={
                "title": "Agent test",
                "language": "python",
                "min_score": 0,
                "max_score": 10,
                "rubric": {"correctness": 10},
            },
        )
        self.assertEqual(assignment_response.status_code, 201)
        self.assignment_id = assignment_response.json()["id"]
        submission_response = self.client.post(
            "/submissions",
            json={
                "assignment_id": self.assignment_id,
                "source_code": "def answer(): return 42",
            },
        )
        self.assertEqual(submission_response.status_code, 201)
        self.submission_id = submission_response.json()["id"]

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.engine.dispose()
        self.password_iterations.stop()
        self.auth_env.stop()

    def create_session(self, *, auto_approve=False, rubric=None):
        response = self.client.post(
            f"/assignments/{self.assignment_id}/agent-sessions",
            json={
                "submission_ids": [self.submission_id],
                "auto_approve": auto_approve,
                "rubric": rubric,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["submission_ids"], [self.submission_id])
        return response.json()["id"]

    def test_second_account_cannot_access_first_accounts_workspace(self):
        logout = self.client.post("/auth/logout")
        self.assertEqual(logout.status_code, 204)

        registration = self.client.post(
            "/auth/register",
            json={"email": "second@example.com", "password": "another-password"},
        )
        self.assertEqual(registration.status_code, 201, registration.text)
        self.assertEqual(self.client.get("/assignments").json(), [])
        self.assertEqual(
            self.client.get(f"/assignments/{self.assignment_id}").status_code,
            404,
        )

        self.client.post("/auth/logout")
        login = self.client.post(
            "/auth/login",
            json={"email": "TEST@example.com", "password": "test-password-123"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(
            [assignment["id"] for assignment in self.client.get("/assignments").json()],
            [self.assignment_id],
        )

    def test_authentication_is_required_for_workspace_api(self):
        self.client.post("/auth/logout")
        self.assertEqual(self.client.get("/assignments").status_code, 401)

    def test_chat_receives_full_code_of_submissions_attached_to_session(self):
        session_id = self.create_session()
        with patch(
            "app.services.grading_agent_workflow.run_grading_agent",
            return_value=TextAgentResponse(response="I can read the attached code."),
        ) as run_agent:
            response = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "What does this code do?"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["user_message"]["content"], "What does this code do?")
        self.assertEqual(
            run_agent.call_args.kwargs["submissions"],
            [
                {
                    "submission_id": self.submission_id,
                    "filename": None,
                    "current_score": None,
                    "source_code": "def answer(): return 42",
                }
            ],
        )
        session = self.client.get(f"/grading-sessions/{session_id}").json()
        self.assertEqual(session["submission_ids"], [self.submission_id])

    def test_streaming_chat_emits_progress_and_completed_response(self):
        session_id = self.create_session()
        with patch(
            "app.services.grading_agent_workflow.run_grading_agent",
            return_value=TextAgentResponse(response="I read the attached code."),
        ):
            response = self.client.post(
                f"/grading-sessions/{session_id}/chat/stream",
                json={"content": "What does this code do?"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(
            response.headers["content-type"].startswith("text/event-stream")
        )
        self.assertIn('event: progress\ndata: {"status": "started"', response.text)
        self.assertIn("event: complete", response.text)
        self.assertIn('"user_message": {"id":', response.text)
        self.assertIn('"content": "What does this code do?"', response.text)
        self.assertIn("I read the attached code.", response.text)

    def test_delete_assignment_removes_related_sessions_and_submissions(self):
        session_id = self.create_session()
        message_response = self.client.post(
            f"/grading-sessions/{session_id}/messages",
            json={"role": "user", "content": "Please grade this."},
        )
        self.assertEqual(message_response.status_code, 201, message_response.text)
        with database.SessionLocal() as db:
            batch = SubmissionUploadBatch(
                assignment_id=self.assignment_id,
                status="COMPLETED",
            )
            db.add(batch)
            db.flush()
            db.add(
                SubmissionUploadItem(
                    batch_id=batch.id,
                    filename="answer.py",
                    source_code="print(42)",
                    status="COMPLETED",
                    submission_id=self.submission_id,
                )
            )
            db.commit()

        response = self.client.delete(f"/assignments/{self.assignment_id}")

        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(
            self.client.get(f"/assignments/{self.assignment_id}").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/submissions/{self.submission_id}").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/grading-sessions/{session_id}").status_code,
            404,
        )
        with database.SessionLocal() as db:
            self.assertEqual(db.query(SubmissionUploadBatch).count(), 0)
            self.assertEqual(db.query(SubmissionUploadItem).count(), 0)

    def test_session_submissions_can_be_attached_and_detached_after_creation(self):
        response = self.client.post(
            f"/assignments/{self.assignment_id}/agent-sessions",
            json={"submission_ids": []},
        )
        self.assertEqual(response.status_code, 201, response.text)
        session_id = response.json()["id"]
        self.assertEqual(response.json()["submission_ids"], [])

        response = self.client.patch(
            f"/grading-sessions/{session_id}/submissions",
            json={"submission_ids": [self.submission_id]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["submission_ids"], [self.submission_id])

        response = self.client.patch(
            f"/grading-sessions/{session_id}/submissions",
            json={"submission_ids": []},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["submission_ids"], [])

    def test_session_submissions_cannot_be_changed_to_another_assignment(self):
        session_id = self.client.post(
            f"/assignments/{self.assignment_id}/agent-sessions",
            json={"submission_ids": []},
        ).json()["id"]
        other_assignment_id = self.client.post(
            "/assignments",
            json={
                "title": "Other assignment",
                "language": "python",
                "min_score": 0,
                "max_score": 10,
            },
        ).json()["id"]
        other_submission_id = self.client.post(
            "/submissions",
            json={
                "assignment_id": other_assignment_id,
                "source_code": "print('other')",
            },
        ).json()["id"]

        response = self.client.patch(
            f"/grading-sessions/{session_id}/submissions",
            json={"submission_ids": [other_submission_id]},
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            self.client.get(f"/grading-sessions/{session_id}").json()[
                "submission_ids"
            ],
            [],
        )

    def test_chat_submission_intent_attaches_selected_submission(self):
        session_id = self.client.post(
            f"/assignments/{self.assignment_id}/agent-sessions",
            json={"submission_ids": []},
        ).json()["id"]
        decision = SubmissionSelectionAgentResponse(
            response="I attached the requested submission.",
            add_submission_ids=[self.submission_id],
        )

        with patch(
            "app.services.grading_agent_workflow.run_grading_agent",
            return_value=decision,
        ) as run_agent:
            response = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "Add the first submission to this chat."},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["session"]["submission_ids"],
            [self.submission_id],
        )
        self.assertEqual(
            run_agent.call_args.kwargs["available_submissions"],
            [{"submission_id": self.submission_id, "filename": None}],
        )
        self.assertEqual(
            self.client.get(f"/grading-sessions/{session_id}").json()[
                "submission_ids"
            ],
            [self.submission_id],
        )

    def test_pending_proposal_blocks_session_submission_changes(self):
        session_id = self.create_session()
        decision = GradesAgentResponse(
            response="Proposed a grade.",
            grades=[
                ProposedGrade(
                    submission_id=self.submission_id,
                    score=8,
                    feedback="Correct result.",
                )
            ],
        )
        with patch(
            "app.services.grading_agent_workflow.run_grading_agent",
            return_value=decision,
        ):
            chat_response = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "Grade this submission."},
            )
        self.assertEqual(chat_response.status_code, 200, chat_response.text)

        response = self.client.patch(
            f"/grading-sessions/{session_id}/submissions",
            json={"submission_ids": []},
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            self.client.get(f"/grading-sessions/{session_id}").json()[
                "submission_ids"
            ],
            [self.submission_id],
        )

    def test_assignment_accepts_natural_language_rubric(self):
        rubric = (
            "Correct functionality is worth 6 points. Edge cases are 2 points. "
            "Readable, maintainable code is 2 points."
        )
        response = self.client.post(
            "/assignments",
            json={
                "title": "Natural rubric assignment",
                "description": "Implement a binary search tree.",
                "language": "python",
                "min_score": 0,
                "max_score": 10,
                "rubric": rubric,
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["rubric"], rubric)

    def test_assignment_can_be_created_without_rubric(self):
        response = self.client.post(
            "/assignments",
            json={
                "title": "Assignment without rubric",
                "description": "Implement a binary search tree.",
                "language": "python",
                "min_score": 0,
                "max_score": 10,
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertIsNone(response.json()["rubric"])

    def test_assignment_can_be_edited(self):
        response = self.client.patch(
            f"/assignments/{self.assignment_id}",
            json={
                "title": "Updated assignment",
                "description": "Updated requirements",
                "rubric": "Correctness is worth 10 points.",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["title"], "Updated assignment")
        self.assertEqual(response.json()["description"], "Updated requirements")
        self.assertEqual(
            response.json()["rubric"],
            "Correctness is worth 10 points.",
        )

    def test_generated_rubric_and_grades_are_saved_together_on_approval(self):
        assignment_response = self.client.post(
            "/assignments",
            json={
                "title": "No-rubric grading",
                "description": "Implement a binary search tree.",
                "language": "python",
                "min_score": 0,
                "max_score": 10,
            },
        )
        assignment_id = assignment_response.json()["id"]
        submission_response = self.client.post(
            "/submissions",
            json={
                "assignment_id": assignment_id,
                "source_code": "def answer(): return 42",
            },
        )
        submission_id = submission_response.json()["id"]
        session_response = self.client.post(
            f"/assignments/{assignment_id}/agent-sessions",
            json={"submission_ids": [submission_id]},
        )
        session_id = session_response.json()["id"]
        rubric = {"correctness": {"description": "Correct output", "points": 10}}
        decision = RubricAndGradesAgentResponse(
            response="Generated a rubric and graded the submission.",
            rubric=rubric,
            grades=[
                ProposedGrade(
                    submission_id=submission_id,
                    score=9,
                    feedback="The output is correct.",
                )
            ],
        )
        with patch(
            "app.services.grading_agent_workflow.run_grading_agent",
            return_value=decision,
        ):
            chat = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "Grade this submission."},
            )

        self.assertEqual(chat.status_code, 200, chat.text)
        proposal = chat.json()["session"]["proposal"]
        self.assertEqual(proposal["kind"], "changes")
        self.assertEqual(
            [change["kind"] for change in proposal["changes"]],
            ["rubric", "grades"],
        )
        self.assertIsNone(self.client.get(f"/assignments/{assignment_id}").json()["rubric"])
        self.assertIsNone(self.client.get(f"/submissions/{submission_id}").json()["score"])

        approval = self.client.post(f"/grading-sessions/{session_id}/approve")
        self.assertEqual(approval.status_code, 200, approval.text)
        self.assertEqual(
            self.client.get(f"/assignments/{assignment_id}").json()["rubric"],
            rubric,
        )
        self.assertEqual(
            self.client.get(f"/submissions/{submission_id}").json()["score"],
            9,
        )

    def test_grading_without_attached_submissions_returns_guidance(self):
        session_response = self.client.post(
            f"/assignments/{self.assignment_id}/agent-sessions",
            json={"submission_ids": []},
        )
        session_id = session_response.json()["id"]
        with patch(
            "app.services.grading_agent_workflow.run_grading_agent",
            return_value=TextAgentResponse(
                response=(
                    "No submissions are attached. Upload code and create a "
                    "grading session that includes it."
                )
            ),
        ) as run_agent:
            response = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "Grade the submissions."},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("No submissions are attached", response.json()["message"]["content"])
        self.assertFalse(run_agent.call_args.kwargs["has_submissions"])

    def test_rubric_proposal_requires_approval_before_saving(self):
        session_id = self.create_session()
        proposed_rubric = {"correctness": {"points": 10}}
        decision = RubricAgentResponse(
            response="I propose this rubric.",
            rubric=proposed_rubric,
        )
        with patch(
            "app.services.grading_agent_workflow.run_grading_agent",
            return_value=decision,
        ):
            response = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "Create a rubric."},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["session"]["proposal"]["status"],
            "PENDING_APPROVAL",
        )

        assignment = self.client.get(f"/assignments/{self.assignment_id}").json()
        self.assertEqual(assignment["rubric"], {"correctness": 10})

        approval = self.client.post(f"/grading-sessions/{session_id}/approve")
        self.assertEqual(approval.status_code, 200, approval.text)
        self.assertEqual(approval.json()["proposal"]["status"], "APPROVED")
        assignment = self.client.get(f"/assignments/{self.assignment_id}").json()
        self.assertEqual(assignment["rubric"], proposed_rubric)

    def test_feedback_replaces_grade_proposal_and_approval_saves_score(self):
        session_id = self.create_session()
        first_decision = GradesAgentResponse(
            response="Proposed score: 7.",
            grades=[
                ProposedGrade(
                    submission_id=self.submission_id,
                    score=7,
                    feedback="Initial feedback.",
                )
            ],
        )
        revised_decision = GradesAgentResponse(
            response="Revised score: 8.",
            grades=[
                ProposedGrade(
                    submission_id=self.submission_id,
                    score=8,
                    feedback="Revised feedback.",
                )
            ],
        )
        with patch(
            "app.services.grading_agent_workflow.run_grading_agent",
            side_effect=[first_decision, revised_decision],
        ):
            first = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "Grade this submission."},
            )
            feedback = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "Reconsider the score and feedback."},
            )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(feedback.status_code, 200, feedback.text)
        self.assertEqual(
            feedback.json()["session"]["proposal"]["grades"][0]["score"],
            8,
        )
        self.assertIsNone(
            self.client.get(f"/submissions/{self.submission_id}").json()["score"]
        )

        approval = self.client.post(f"/grading-sessions/{session_id}/approve")
        self.assertEqual(approval.status_code, 200, approval.text)
        submission = self.client.get(f"/submissions/{self.submission_id}").json()
        self.assertEqual(submission["score"], 8)

    def test_grading_after_rubric_proposal_preserves_both_for_approval(self):
        session_id = self.create_session()
        proposed_rubric = {"correctness": {"points": 10}}
        rubric_decision = RubricAgentResponse(
            response="Proposed rubric.",
            rubric=proposed_rubric,
        )
        grade_decision = GradesAgentResponse(
            response="Proposed score using the new rubric.",
            grades=[
                ProposedGrade(
                    submission_id=self.submission_id,
                    score=8,
                    feedback="Correct output.",
                )
            ],
        )
        revised_grade_decision = GradesAgentResponse(
            response="Revised score using stricter grading.",
            grades=[
                ProposedGrade(
                    submission_id=self.submission_id,
                    score=6,
                    feedback="One edge case is not handled.",
                )
            ],
        )
        with patch(
            "app.services.grading_agent_workflow.run_grading_agent",
            side_effect=[
                rubric_decision,
                grade_decision,
                revised_grade_decision,
            ],
        ):
            rubric_response = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "Create a rubric."},
            )
            grade_response = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "Now grade the attached submission."},
            )
            feedback_response = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "That grade is too easy; be stricter."},
            )

        self.assertEqual(rubric_response.status_code, 200, rubric_response.text)
        self.assertEqual(grade_response.status_code, 200, grade_response.text)
        self.assertEqual(feedback_response.status_code, 200, feedback_response.text)
        proposal = feedback_response.json()["session"]["proposal"]
        self.assertEqual(proposal["kind"], "changes")
        self.assertEqual(
            [change["kind"] for change in proposal["changes"]],
            ["rubric", "grades"],
        )
        self.assertIsNone(
            self.client.get(f"/submissions/{self.submission_id}").json()["score"]
        )

        approval = self.client.post(f"/grading-sessions/{session_id}/approve")
        self.assertEqual(approval.status_code, 200, approval.text)
        self.assertEqual(
            self.client.get(f"/assignments/{self.assignment_id}").json()["rubric"],
            proposed_rubric,
        )
        self.assertEqual(
            self.client.get(f"/submissions/{self.submission_id}").json()["score"],
            6,
        )

    def test_session_rubric_can_be_set_from_settings_and_used_for_grading(self):
        session_id = self.create_session()
        custom_rubric = {"security": {"points": 10}}
        settings_response = self.client.patch(
            f"/grading-sessions/{session_id}/settings",
            json={"rubric": custom_rubric},
        )
        self.assertEqual(settings_response.status_code, 200, settings_response.text)
        self.assertEqual(
            settings_response.json()["grading_rubric"],
            custom_rubric,
        )

        decision = GradesAgentResponse(
            response="Proposed score.",
            grades=[
                ProposedGrade(
                    submission_id=self.submission_id,
                    score=7,
                    feedback="Reviewed against security criteria.",
                )
            ],
        )
        with patch(
            "app.services.grading_agent_workflow.run_grading_agent",
            return_value=decision,
        ) as run_agent:
            response = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "Grade using the configured rubric."},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(custom_rubric.__str__(), run_agent.call_args.kwargs["context"])

    def test_revised_session_rubric_does_not_change_assignment_rubric(self):
        session_rubric = {"correctness": {"points": 8}}
        revised_rubric = {"correctness": {"points": 6}, "clarity": {"points": 4}}
        session_id = self.create_session(rubric=session_rubric)
        decision = RubricAgentResponse(
            response="Here is the revised session rubric.",
            rubric=revised_rubric,
        )
        with patch(
            "app.services.grading_agent_workflow.run_grading_agent",
            return_value=decision,
        ):
            proposal_response = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "Revise this rubric to include clarity."},
            )

        self.assertEqual(proposal_response.status_code, 200, proposal_response.text)
        self.assertEqual(
            proposal_response.json()["session"]["proposal"]["target"],
            "session",
        )
        approval = self.client.post(f"/grading-sessions/{session_id}/approve")
        self.assertEqual(approval.status_code, 200, approval.text)
        self.assertEqual(
            self.client.get(f"/assignments/{self.assignment_id}").json()["rubric"],
            {"correctness": 10},
        )
        self.assertEqual(approval.json()["grading_rubric"], revised_rubric)

    def test_follow_up_question_keeps_pending_proposal(self):
        session_id = self.create_session()
        grade_decision = GradesAgentResponse(
            response="Proposed score.",
            grades=[
                ProposedGrade(
                    submission_id=self.submission_id,
                    score=7,
                    feedback="Initial feedback.",
                )
            ],
        )
        question_decision = TextAgentResponse(
            response="The main issue is the missing edge case.",
        )
        with patch(
            "app.services.grading_agent_workflow.run_grading_agent",
            side_effect=[grade_decision, question_decision],
        ):
            self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "Grade this submission."},
            )
            follow_up = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "What is the main insight?"},
            )

        self.assertEqual(follow_up.status_code, 200, follow_up.text)
        self.assertEqual(
            follow_up.json()["session"]["proposal"]["status"],
            "PENDING_APPROVAL",
        )
        self.assertEqual(
            follow_up.json()["session"]["proposal"]["grades"][0]["score"],
            7,
        )
        self.assertIn("main issue", follow_up.json()["message"]["content"])

    def test_auto_approve_applies_grade_when_enabled(self):
        session_id = self.create_session(auto_approve=True)
        decision = GradesAgentResponse(
            response="Proposed score: 9.",
            grades=[
                ProposedGrade(
                    submission_id=self.submission_id,
                    score=9,
                    feedback="Meets the rubric.",
                )
            ],
        )
        with patch(
            "app.services.grading_agent_workflow.run_grading_agent",
            return_value=decision,
        ):
            response = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "Grade this submission."},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["session"]["proposal"]["status"], "APPROVED")
        self.assertEqual(
            self.client.get(f"/submissions/{self.submission_id}").json()["score"],
            9,
        )

    def test_grade_outside_assignment_range_is_not_saved(self):
        session_id = self.create_session()
        decision = GradesAgentResponse(
            response="Invalid proposed score.",
            grades=[
                ProposedGrade(
                    submission_id=self.submission_id,
                    score=11,
                    feedback="Invalid.",
                )
            ],
        )
        with (
            patch(
                "app.services.grading_agent_workflow.run_grading_agent",
                return_value=decision,
            ),
            self.assertLogs("app.api.grading", level="ERROR"),
        ):
            response = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "Grade this submission."},
            )
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIsNone(
            self.client.get(f"/submissions/{self.submission_id}").json()["score"]
        )

    def test_agent_requires_gemini_api_key(self):
        session_id = self.create_session()
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""},
        ):
            response = self.client.post(
                f"/grading-sessions/{session_id}/chat",
                json={"content": "Hello"},
            )
        self.assertEqual(response.status_code, 503, response.text)
        messages = self.client.get(f"/grading-sessions/{session_id}/messages").json()
        self.assertEqual(messages[-1]["content"], "Hello")

    def test_auto_approve_setting_can_be_changed(self):
        session_id = self.create_session()
        response = self.client.patch(
            f"/grading-sessions/{session_id}/settings",
            json={"auto_approve": True},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["auto_approve"])


class GradingAgentGraphTests(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(
            os.environ,
            {
                "LANGFUSE_PUBLIC_KEY": "public-test-key",
                "LANGFUSE_SECRET_KEY": "secret-test-key",
            },
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

        client = MagicMock()

        def get_prompt(name, **_kwargs):
            prompt = MagicMock()
            prompt.variables = ["context"]
            prompt.compile.side_effect = lambda **variables: (
                f"managed prompt {name}\n"
                f"Assignment context:\n{variables['context']}"
            )
            return prompt

        client.get_prompt.side_effect = get_prompt
        patchers = [
            patch("app.services.grading_agent.get_client", return_value=client),
            patch(
                "app.services.grading_agent.propagate_attributes",
                return_value=nullcontext(),
            ),
            patch(
                "app.services.grading_agent.CallbackHandler",
                return_value=MagicMock(),
            ),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_graph_runs_with_structured_gemini_output(self):
        expected = RubricAgentResponse(
            response="Proposed rubric.",
            rubric={"correctness": 10},
        )
        grade_response = GradesAgentResponse(response="Proposed grade.")
        text_response = TextAgentResponse(response="How can I help?")
        selection_response = SubmissionSelectionAgentResponse(
            response="I attached the requested file.",
            add_submission_ids=[12],
        )

        cases = [
            (AgentIntent(action="rubric"), expected),
            (AgentIntent(action="grades"), grade_response),
            (AgentIntent(action="submissions"), selection_response),
            (AgentIntent(action="response"), text_response),
        ]

        for intent, expected_response in cases:
            invocations = []

            class FakeStructuredModel:
                def __init__(self, schema):
                    self.schema = schema

                def invoke(self, messages):
                    invocations.append((self.schema, messages))
                    if self.schema is AgentIntent:
                        return intent
                    if self.schema is type(expected_response):
                        return expected_response
                    raise AssertionError(f"Unexpected node schema: {self.schema}")

            class FakeModel:
                def with_structured_output(self, schema):
                    return FakeStructuredModel(schema)

            with (
                patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
                patch(
                    "app.services.grading_agent.ChatGoogleGenerativeAI",
                    return_value=FakeModel(),
                ),
            ):
                decision = run_grading_agent(
                    context="Assignment title: Test",
                    history=[("user", "Hello")],
                )

            self.assertEqual(decision, expected_response)
            self.assertEqual(
                [schema for schema, _ in invocations],
                [AgentIntent, type(expected_response)],
            )
            self.assertIn("Hello", invocations[-1][1][-1].content)
            self.assertIn(
                {
                    "rubric": "managed prompt grading-agent-rubric",
                    "grades": "managed prompt grading-agent-grades",
                    "submissions": "managed prompt grading-agent-submissions",
                    "response": "managed prompt grading-agent-response",
                }[intent.action],
                invocations[-1][1][0].content,
            )
            self.assertIn(
                "managed prompt grading-agent-router",
                invocations[0][1][0].content,
            )

    def test_graph_routes_follow_up_using_conversation_history(self):
        invocations = []
        expected_response = GradesAgentResponse(
            response="Revised score using your feedback.",
        )

        class FakeStructuredModel:
            def __init__(self, schema):
                self.schema = schema

            def invoke(self, messages):
                invocations.append((self.schema, messages))
                if self.schema is AgentIntent:
                    return AgentIntent(action="grades")
                if self.schema is GradesAgentResponse:
                    return expected_response
                raise AssertionError(f"Unexpected node schema: {self.schema}")

        class FakeModel:
            def with_structured_output(self, schema):
                return FakeStructuredModel(schema)

        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
            patch(
                "app.services.grading_agent.ChatGoogleGenerativeAI",
                return_value=FakeModel(),
            ),
        ):
            decision = run_grading_agent(
                context="Current proposal: score 8",
                history=[
                    ("user", "Grade this submission."),
                    ("assistant", "I propose a score of 8."),
                    ("user", "That is too easy; be stricter."),
                ],
            )

        self.assertEqual(decision, expected_response)
        router_prompt, router_history = invocations[0][1][0], invocations[0][1][1:]
        self.assertIn("managed prompt grading-agent-router", router_prompt.content)
        self.assertEqual(
            [message.content for message in router_history],
            [
                "Grade this submission.",
                "I propose a score of 8.",
                "That is too easy; be stricter.",
            ],
        )

    def test_graph_generates_rubric_before_grading_when_routed_together(self):
        invocations = []
        generated_rubric = RubricAgentResponse(
            response="I generated a rubric.",
            rubric={"correctness": {"points": 10}},
        )
        grades = GradesAgentResponse(
            response="I graded the submission.",
            grades=[
                ProposedGrade(
                    submission_id=1,
                    score=8,
                    feedback="Correct output.",
                )
            ],
        )

        class FakeStructuredModel:
            def __init__(self, schema):
                self.schema = schema

            def invoke(self, messages):
                invocations.append((self.schema, messages))
                if self.schema is AgentIntent:
                    return AgentIntent(action="rubric_and_grades")
                if self.schema is RubricAgentResponse:
                    return generated_rubric
                if self.schema is GradesAgentResponse:
                    return grades
                raise AssertionError(f"Unexpected node schema: {self.schema}")

        class FakeModel:
            def with_structured_output(self, schema):
                return FakeStructuredModel(schema)

        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
            patch(
                "app.services.grading_agent.ChatGoogleGenerativeAI",
                return_value=FakeModel(),
            ),
        ):
            decision = run_grading_agent(
                context="Assignment rubric: None",
                history=[("user", "Grade this submission.")],
            )

        self.assertIsInstance(decision, RubricAndGradesAgentResponse)
        self.assertEqual(decision.rubric, generated_rubric.rubric)
        self.assertEqual(decision.grades, grades.grades)
        self.assertIn("generated a rubric", decision.response)
        self.assertIn("graded the submission", decision.response)
        router_prompt = invocations[0][1][0].content
        self.assertIn("managed prompt grading-agent-router", router_prompt)
        self.assertIn("Assignment rubric: None", router_prompt)
        self.assertEqual(
            [schema for schema, _ in invocations],
            [AgentIntent, RubricAgentResponse, GradesAgentResponse],
        )
        grade_prompt = invocations[-1][1][0].content
        self.assertIn("Newly generated rubric", grade_prompt)
        self.assertIn(str(generated_rubric.rubric), grade_prompt)

    def test_graph_does_not_invoke_grades_node_without_attached_submissions(self):
        invocations = []
        expected_response = TextAgentResponse(
            action="request_submissions",
            response="Upload submissions and create a grading session first.",
        )

        class FakeStructuredModel:
            def __init__(self, schema):
                self.schema = schema

            def invoke(self, messages):
                invocations.append((self.schema, messages))
                if self.schema is AgentIntent:
                    return AgentIntent(action="grades")
                if self.schema is TextAgentResponse:
                    return expected_response
                raise AssertionError(f"Unexpected node schema: {self.schema}")

        class FakeModel:
            def with_structured_output(self, schema):
                return FakeStructuredModel(schema)

        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
            patch(
                "app.services.grading_agent.ChatGoogleGenerativeAI",
                return_value=FakeModel(),
            ),
        ):
            decision = run_grading_agent(
                context="Attached submissions: 0",
                history=[("user", "Grade the submissions.")],
                has_submissions=False,
            )

        self.assertEqual(decision, expected_response)
        self.assertEqual(
            [schema for schema, _ in invocations],
            [AgentIntent, TextAgentResponse],
        )

    def test_node_responses_accept_request_submissions_action(self):
        grade_response = GradesAgentResponse.model_validate(
            {
                "action": "request_submissions",
                "response": "Attach submissions before grading.",
            }
        )
        text_response = TextAgentResponse.model_validate(
            {
                "action": "request_submissions",
                "response": "Upload submissions before grading.",
            }
        )

        self.assertEqual(grade_response.action, "request_submissions")
        self.assertEqual(text_response.action, "request_submissions")

    def test_graph_sends_full_submission_code_to_grades_one_at_a_time(self):
        invocations = []
        first_code = "first-source-" + ("a" * 32_000)
        second_code = "second-source-" + ("b" * 36_000)

        class FakeStructuredModel:
            def __init__(self, schema):
                self.schema = schema

            def invoke(self, messages):
                invocations.append((self.schema, messages))
                if self.schema is AgentIntent:
                    return AgentIntent(action="grades")
                prompt = messages[0].content
                submission_id = 10 if "Submission ID: 10" in prompt else 20
                return GradesAgentResponse(
                    response=f"Reviewed submission {submission_id}.",
                    grades=[
                        ProposedGrade(
                            submission_id=submission_id,
                            score=8,
                            feedback=f"Reviewed {submission_id}.",
                        )
                    ],
                )

        class FakeModel:
            def with_structured_output(self, schema):
                return FakeStructuredModel(schema)

        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
            patch(
                "app.services.grading_agent.ChatGoogleGenerativeAI",
                return_value=FakeModel(),
            ),
        ):
            decision = run_grading_agent(
                context="Assignment title: Test",
                history=[("user", "Grade every submission.")],
                submissions=[
                    {
                        "submission_id": 10,
                        "filename": "first.py",
                        "current_score": None,
                        "source_code": first_code,
                    },
                    {
                        "submission_id": 20,
                        "filename": "second.py",
                        "current_score": None,
                        "source_code": second_code,
                    },
                ],
            )

        self.assertEqual([grade.submission_id for grade in decision.grades], [10, 20])
        grading_prompts = [
            messages[0].content
            for schema, messages in invocations
            if schema is GradesAgentResponse
        ]
        self.assertEqual(len(grading_prompts), 2)
        self.assertIn(first_code, grading_prompts[0])
        self.assertNotIn(second_code, grading_prompts[0])
        self.assertIn(second_code, grading_prompts[1])
        self.assertNotIn(first_code, grading_prompts[1])


if __name__ == "__main__":
    unittest.main()
