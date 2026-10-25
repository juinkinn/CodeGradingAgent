import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, SingleTurnParams
from langfuse import propagate_attributes

from app.evaluation.gemini_judge import GeminiJudge
from app.evaluation.nlp_metrics import rouge_l, token_f1
from app.services.grading_agent import AgentDecision, run_grading_agent


BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = BACKEND_DIR / "evaluation" / "golden_cases.json"
DEFAULT_REPORT_DIR = BACKEND_DIR / "evaluation" / "results"
logger = logging.getLogger("app.evaluation")


def _load_cases(dataset_path: Path) -> list[dict[str, Any]]:
    try:
        raw_cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load evaluation dataset: {dataset_path}") from exc
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("Evaluation dataset must be a non-empty JSON array.")

    required = {
        "id",
        "context",
        "history",
        "expected_action",
        "expected_output",
        "expected_response",
    }
    for index, case in enumerate(raw_cases):
        if not isinstance(case, dict) or not required.issubset(case):
            raise ValueError(
                f"Evaluation case at index {index} must include "
                f"{', '.join(sorted(required))}."
            )
        if not isinstance(case["history"], list) or not case["history"]:
            raise ValueError(f"Evaluation case '{case['id']}' needs a non-empty history.")
        if not all(
            isinstance(message, list)
            and len(message) == 2
            and message[0] in {"user", "assistant"}
            and isinstance(message[1], str)
            for message in case["history"]
        ):
            raise ValueError(
                f"Evaluation case '{case['id']}' has an invalid history format."
            )
        if not any(role == "user" for role, _ in case["history"]):
            raise ValueError(
                f"Evaluation case '{case['id']}' needs at least one user message."
            )
        if not all(
            isinstance(case[field], str)
            for field in (
                "id",
                "context",
                "expected_action",
                "expected_output",
                "expected_response",
            )
        ):
            raise ValueError(
                f"Evaluation case at index {index} has invalid string fields."
            )
    return raw_cases


def _invoke_case(case: dict[str, Any]) -> AgentDecision:
    return run_grading_agent(
        context=case["context"],
        history=[(role, content) for role, content in case["history"]],
        has_submissions=case.get("has_submissions", True),
        submissions=case.get("submissions"),
        available_submissions=case.get("available_submissions", []),
    )


def _evaluate_case(
    case: dict[str, Any],
    decision: AgentDecision,
    judge: GeminiJudge,
    *,
    threshold: float,
    agent_duration_seconds: float,
) -> dict[str, Any]:
    actual_output = decision.model_dump_json()
    expected_output = case["expected_output"]
    latest_user_message = next(
        content for role, content in reversed(case["history"]) if role == "user"
    )
    judge_metric = GEval(
        name="Agent behavior quality",
        criteria=(
            "Assess whether the agent selected the expected action and produced a "
            "helpful, accurate response that follows the user's request and the "
            "provided assignment/session context. Penalize unsupported claims that "
            "grades or rubrics were saved or approved. For grading, check that the "
            "grade is grounded in the supplied source and rubric."
        ),
        evaluation_params=[
            SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.EXPECTED_OUTPUT,
        ],
        model=judge,
        threshold=threshold,
        async_mode=False,
    )
    judge_case = LLMTestCase(
        input=(
            f"Latest instructor request:\n{latest_user_message}\n\n"
            f"Assignment/session context:\n{case['context']}\n\n"
            "Attached submission data:\n"
            f"{json.dumps(case.get('submissions') or [], ensure_ascii=False)}"
        ),
        actual_output=actual_output,
        expected_output=expected_output,
    )
    judge_started = time.perf_counter()
    judge_metric.measure(judge_case)
    judge_duration_seconds = time.perf_counter() - judge_started

    expected_response = case["expected_response"]
    actual_response = decision.response
    return {
        "id": case["id"],
        "expected_action": case["expected_action"],
        "actual_action": decision.action,
        "action_correct": decision.action == case["expected_action"],
        "judge_score": judge_metric.score,
        "judge_passed": judge_metric.is_successful(),
        "judge_reason": judge_metric.reason,
        "agent_duration_seconds": agent_duration_seconds,
        "judge_duration_seconds": judge_duration_seconds,
        "token_f1": token_f1(expected_response, actual_response),
        "rouge_l": rouge_l(expected_response, actual_response),
        "actual_output": json.loads(actual_output),
    }


def run_evaluation(
    dataset_path: Path,
    *,
    threshold: float,
) -> dict[str, Any]:
    cases = _load_cases(dataset_path)
    judge = GeminiJudge()
    results = []
    for case in cases:
        logger.info("Evaluating case %s", case["id"])
        with propagate_attributes(
            session_id=f"deepeval-{case['id']}",
            tags=["deepeval", "grading-agent-eval"],
            metadata={"eval_case_id": case["id"]},
        ):
            agent_started = time.perf_counter()
            decision = _invoke_case(case)
            agent_duration_seconds = time.perf_counter() - agent_started
            results.append(
                _evaluate_case(
                    case,
                    decision,
                    judge,
                    threshold=threshold,
                    agent_duration_seconds=agent_duration_seconds,
                )
            )

    action_accuracy = sum(result["action_correct"] for result in results) / len(results)
    mean_judge_score = sum(result["judge_score"] for result in results) / len(results)
    mean_token_f1 = sum(result["token_f1"] for result in results) / len(results)
    mean_rouge_l = sum(result["rouge_l"] for result in results) / len(results)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset_path),
        "agent_model": judge.get_model_name(),
        "judge_model": judge.get_model_name(),
        "summary": {
            "case_count": len(results),
            "action_accuracy": action_accuracy,
            "mean_judge_score": mean_judge_score,
            "mean_token_f1": mean_token_f1,
            "mean_rouge_l": mean_rouge_l,
            "mean_agent_duration_seconds": sum(
                result["agent_duration_seconds"] for result in results
            )
            / len(results),
            "mean_judge_duration_seconds": sum(
                result["judge_duration_seconds"] for result in results
            )
            / len(results),
            "passed": all(
                result["action_correct"] and result["judge_passed"]
                for result in results
            ),
        },
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run DeepEval/NLP evaluation against the grading agent."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--judge-threshold", type=float, default=0.7)
    args = parser.parse_args()
    if not 0 <= args.judge_threshold <= 1:
        parser.error("--judge-threshold must be between 0 and 1.")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    report = run_evaluation(args.dataset, threshold=args.judge_threshold)

    report_path = args.report or (
        DEFAULT_REPORT_DIR
        / f"eval-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], indent=2))
    print(f"Detailed report: {report_path}")
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
