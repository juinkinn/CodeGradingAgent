import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.evaluation.gemini_judge import GeminiJudge
from app.evaluation.nlp_metrics import rouge_l, token_f1
from app.evaluation.runner import DEFAULT_DATASET, _evaluate_case, _load_cases
from app.services.grading_agent import TextAgentResponse


class NlpEvaluationMetricTests(unittest.TestCase):
    def test_token_f1_uses_multiset_overlap_case_insensitively(self):
        self.assertAlmostEqual(token_f1("The sum is 2", "sum is 2"), 6 / 7)

    def test_token_f1_is_zero_for_disjoint_text(self):
        self.assertEqual(token_f1("alpha beta", "gamma delta"), 0.0)

    def test_rouge_l_scores_ordered_overlap(self):
        self.assertAlmostEqual(rouge_l("a b c d", "a c d"), 6 / 7)

    def test_empty_text_is_defined(self):
        self.assertEqual(token_f1("", ""), 1.0)
        self.assertEqual(rouge_l("", "nonempty"), 0.0)


class EvaluationDatasetTests(unittest.TestCase):
    def test_default_golden_dataset_is_valid(self):
        cases = _load_cases(DEFAULT_DATASET)
        self.assertGreaterEqual(len(cases), 4)
        self.assertEqual(
            {case["expected_action"] for case in cases},
            {"response", "rubric", "submissions", "grades"},
        )

    def test_dataset_must_contain_non_empty_message_history(self):
        path = Path(__file__).with_name("invalid-eval-cases.json")
        path.write_text(
            json.dumps(
                [
                    {
                        "id": "invalid",
                        "context": "",
                        "history": [],
                        "expected_action": "response",
                        "expected_output": "answer",
                        "expected_response": "answer",
                    }
                ]
            ),
            encoding="utf-8",
        )
        try:
            with self.assertRaisesRegex(ValueError, "non-empty history"):
                _load_cases(path)
        finally:
            path.unlink(missing_ok=True)


class GeminiJudgeTests(unittest.TestCase):
    def test_judge_uses_configured_gemini_for_structured_output(self):
        with (
            patch("app.evaluation.gemini_judge.settings") as settings,
            patch(
                "app.evaluation.gemini_judge.ChatGoogleGenerativeAI"
            ) as model_factory,
        ):
            settings.gemini_api_key = "test-key"
            settings.gemini_model = "gemini-test"
            client = model_factory.return_value
            expected = TextAgentResponse(response="Test response.")
            client.with_structured_output.return_value.invoke.return_value = expected

            judge = GeminiJudge()
            actual = judge.generate("evaluate this", schema=TextAgentResponse)

        self.assertIs(actual, expected)
        model_factory.assert_called_once_with(
            model="gemini-test",
            google_api_key="test-key",
            temperature=0,
            max_retries=2,
        )

    def test_case_evaluation_combines_judge_and_nlp_scores(self):
        decision = TextAgentResponse(response="The assignment adds two integers.")
        judge = GeminiJudge.__new__(GeminiJudge)
        case = {
            "id": "assignment-question",
            "context": "Write a function that adds two integers.",
            "history": [["user", "What should the function do?"]],
            "submissions": [
                {
                    "submission_id": 1,
                    "filename": "answer.py",
                    "source_code": "def answer(): return 42",
                }
            ],
            "expected_action": "response",
            "expected_output": "Explain the addition requirement.",
            "expected_response": "The assignment adds two integers.",
        }
        judge_metric = MagicMock()
        judge_metric.score = 0.9
        judge_metric.reason = "The answer is consistent with the context."
        judge_metric.is_successful.return_value = True

        with patch(
            "app.evaluation.runner.GEval",
            return_value=judge_metric,
        ) as geval:
            result = _evaluate_case(
                case,
                decision,
                judge,
                threshold=0.7,
                agent_duration_seconds=0.25,
            )

        self.assertEqual(result["judge_score"], 0.9)
        self.assertTrue(result["action_correct"])
        self.assertEqual(result["token_f1"], 1.0)
        self.assertEqual(result["rouge_l"], 1.0)
        self.assertEqual(result["agent_duration_seconds"], 0.25)
        geval.assert_called_once()
        judge_metric.measure.assert_called_once()
        judge_case = judge_metric.measure.call_args.args[0]
        self.assertIn("def answer(): return 42", judge_case.input)


if __name__ == "__main__":
    unittest.main()
