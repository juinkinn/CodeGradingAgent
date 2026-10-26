import os
import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from google.genai.errors import APIError
from langchain_google_genai.chat_models import GoogleAPIError, GoogleRateLimitError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database
from app.main import app
from app.models.submission_upload import SubmissionUploadBatch, SubmissionUploadItem
from app.services.grading_agent import (
    AgentIntent,
    GradesAgentResponse,
    ProposedGrade,
    RubricAndGradesAgentResponse,
    RubricAgentResponse,
    SubmissionSelectionAgentResponse,
    TextAgentResponse,
    _invoke_model_with_quota_retry,
    run_grading_agent,
)


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

    def create_session(self, *, auto_approve=False):
        response = self.client.post(
            f"/assignments/{self.assignment_id}/agent-sessions",
            json={
                "submission_ids": [self.submission_id],
                "auto_approve": auto_approve,
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
            patch(
                "app.services.grading_agent._wait_for_model_request_slot",
                return_value=None,
            ),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_model_retries_quota_error_after_server_retry_delay(self):
        quota_error = APIError(
            429,
            {
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.RetryInfo",
                            "retryDelay": "45.4s",
                        }
                    ],
                }
            },
        )
        model = MagicMock()

        def invoke(_messages):
            if model.invoke.call_count == 1:
                try:
                    raise quota_error
                except APIError as cause:
                    raise GoogleRateLimitError(
                        "429 RESOURCE_EXHAUSTED. Please retry in 45.4s."
                    ) from cause
            return "model response"

        model.invoke.side_effect = invoke

        with (
            patch(
                "app.services.grading_agent._wait_for_model_request_slot"
            ) as wait_for_slot,
            patch(
                "app.services.grading_agent._extend_model_cooldown"
            ) as extend_cooldown,
        ):
            result = _invoke_model_with_quota_retry(model, [])

        self.assertEqual(result, "model response")
        self.assertEqual(model.invoke.call_count, 2)
        self.assertEqual(wait_for_slot.call_count, 2)
        extend_cooldown.assert_called_once_with(45.4)

    def test_model_retries_temporary_high_demand_error(self):
        high_demand_error = GoogleAPIError(
            code=503,
            response_json={
                "error": {
                    "status": "UNAVAILABLE",
                    "message": "This model is currently experiencing high demand",
                }
            },
        )
        model = MagicMock()
        model.invoke.side_effect = [high_demand_error, "model response"]

        with (
            patch(
                "app.services.grading_agent._wait_for_model_request_slot"
            ) as wait_for_slot,
            patch(
                "app.services.grading_agent._extend_model_cooldown"
            ) as extend_cooldown,
        ):
            result = _invoke_model_with_quota_retry(model, [])

        self.assertEqual(result, "model response")
        self.assertEqual(model.invoke.call_count, 2)
        self.assertEqual(wait_for_slot.call_count, 2)
        extend_cooldown.assert_called_once_with(2.0)

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
                context=(
                    "Assignment title: Test\n"
                    "Attached submissions: 79\n"
                    "Attached submission IDs: 10, 20, and 77 others"
                ),
                routing_context="Assignment title: Test",
                grading_context="Assignment title: Test",
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
        for prompt in grading_prompts:
            self.assertNotIn("Attached submissions: 79", prompt)
            self.assertNotIn("77 others", prompt)
        router_prompt = next(
            messages[0].content
            for schema, messages in invocations
            if schema is AgentIntent
        )
        self.assertNotIn("Attached submissions: 79", router_prompt)
        self.assertIn("one submission at a time", router_prompt)


if __name__ == "__main__":
    unittest.main()
