from __future__ import annotations

import unittest
from typing import Any

from apps.review_agent.graph import GRAPH_WORKFLOW_NAME, REVIEW_GRAPH, run_review_workflow
from apps.review_agent.graph.runtime import ReviewGraphRuntime
from apps.review_agent.providers import ReviewProviderError, ReviewProviderOutputError
from apps.review_agent.schemas import GeminiBoundaryDecision
from apps.review_agent.service import BoundaryOptionSelectionError


def _decision(
    decision: str = "adjust_boundaries",
    start: int = 1,
    end: int = 2,
) -> GeminiBoundaryDecision:
    return GeminiBoundaryDecision(
        decision=decision,
        selected_start_option_index=start,
        selected_end_option_index=end,
        reasoning_summary="Safe summary.",
        start_reason="Complete start.",
        end_reason="Complete end.",
    )


class _Harness:
    def __init__(self, outcomes: list[Any], *, cancelled: bool = False) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0
        self.cancelled = cancelled
        self.feedback: list[str | None] = []
        self.context = {
            "allowed_boundary_pairs": [
                {"start_option_index": 1, "end_option_index": 2}
            ],
        }

    def runtime(self) -> ReviewGraphRuntime:
        return ReviewGraphRuntime(
            build_context=lambda: (self.context, object()),
            invoke_provider=self.invoke,
            validate_decision=self.validate,
            failed_result=self.failed,
            corrective_message=lambda _context, _error: (
                "The prior structured response was invalid. Choose one exact allowed pair."
            ),
            failure_category=lambda error: (
                "structured_output"
                if isinstance(error, ReviewProviderOutputError)
                else "boundary_validation"
                if isinstance(error, BoundaryOptionSelectionError)
                else "provider"
            ),
            cancellation_check=lambda: self.cancelled,
            retryable_errors=(ReviewProviderOutputError, BoundaryOptionSelectionError),
            provider_errors=(ReviewProviderError,),
            cancelled_errors=(CancelledError,),
        )

    def invoke(self, _provider: Any, _context: dict[str, Any], feedback: str | None) -> Any:
        self.calls += 1
        self.feedback.append(feedback)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def validate(
        self,
        _context: dict[str, Any],
        decision: GeminiBoundaryDecision,
        debug: dict[str, Any],
    ) -> dict[str, Any]:
        if decision.selected_start_option_index != 1 or decision.selected_end_option_index != 2:
            raise BoundaryOptionSelectionError("Selected pair is not in allowed_boundary_pairs.")
        return {
            "decision": decision.decision,
            "selected_start_segment_id": "segment-1",
            "selected_end_segment_id": "segment-2",
            "reviewed_start": 10.0 if decision.decision != "reject" else None,
            "reviewed_end": 40.0 if decision.decision != "reject" else None,
            "retry_used": debug["retry_used"],
            "provider_attempt_count": debug["provider_attempt_count"],
            "raw_result": {},
        }

    @staticmethod
    def failed(
        _context: dict[str, Any],
        warning: str,
        category: str | None,
        debug: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "decision": "manual_review",
            "failed": category == "provider",
            "failure_reason": warning,
            "failure_category": category,
            "retry_used": debug["retry_used"],
            "provider_attempt_count": debug["provider_attempt_count"],
            "raw_result": {},
        }


class CancelledError(RuntimeError):
    pass


class LangGraphReviewTests(unittest.TestCase):
    def _run(self, harness: _Harness) -> dict[str, Any]:
        return run_review_workflow(
            runtime=harness.runtime(),
            initial_state={
                "project_id": 1,
                "clip_id": "clip-1",
                "review_mode": "gemini",
                "original_candidate_start": 10.0,
                "original_candidate_end": 40.0,
                "existing_reviewed_start": None,
                "existing_reviewed_end": None,
                "existing_edited_start": 10.0,
                "existing_edited_end": 40.0,
            },
        )

    def test_graph_has_expected_nodes_edges_and_explicit_terminals(self):
        graph = REVIEW_GRAPH.get_graph()
        expected = {
            "build_review_context",
            "invoke_reviewer",
            "validate_review",
            "prepare_corrective_retry",
            "apply_review",
            "finalize_manual_review",
            "finalize_provider_failure",
            "finalize_cancelled",
        }
        self.assertTrue(expected.issubset(graph.nodes))
        edges = {(edge.source, edge.target, edge.conditional) for edge in graph.edges}
        self.assertIn(("prepare_corrective_retry", "invoke_reviewer", False), edges)
        self.assertIn(("validate_review", "apply_review", True), edges)
        self.assertIn(("validate_review", "finalize_manual_review", True), edges)
        self.assertIn(("validate_review", "finalize_provider_failure", True), edges)
        for terminal in (
            "apply_review",
            "finalize_manual_review",
            "finalize_provider_failure",
            "finalize_cancelled",
        ):
            self.assertTrue(any(edge.source == terminal and edge.target == "__end__" for edge in graph.edges))

    def test_valid_adjustment_is_applied_offline(self):
        harness = _Harness([_decision()])
        state = self._run(harness)
        self.assertEqual(state["terminal_route"], "applied")
        self.assertEqual(state["result"]["reviewed_start"], 10.0)
        self.assertEqual(state["selected_start_segment_id"], "segment-1")
        self.assertEqual(harness.calls, 1)
        self.assertEqual(state["workflow_name"], GRAPH_WORKFLOW_NAME)

    def test_render_ready_and_reject_preserve_decision_meanings(self):
        for decision in ("render_ready", "reject"):
            with self.subTest(decision=decision):
                state = self._run(_Harness([_decision(decision)]))
                self.assertEqual(state["terminal_route"], "applied")
                self.assertEqual(state["result"]["decision"], decision)

    def test_structured_error_gets_exactly_one_corrective_retry(self):
        harness = _Harness([ReviewProviderOutputError("invalid JSON"), _decision()])
        state = self._run(harness)
        self.assertEqual(state["terminal_route"], "applied")
        self.assertTrue(state["retry_used"])
        self.assertEqual(state["attempt_number"], 2)
        self.assertEqual(harness.calls, 2)
        self.assertIsNone(harness.feedback[0])
        self.assertIn("allowed pair", harness.feedback[1])

    def test_invalid_pair_gets_one_retry_then_manual_review(self):
        harness = _Harness([_decision(start=99), _decision(start=98)])
        state = self._run(harness)
        self.assertEqual(state["terminal_route"], "manual_review")
        self.assertEqual(state["result"]["decision"], "manual_review")
        self.assertEqual(state["result"]["failure_category"], "boundary_validation")
        self.assertEqual(harness.calls, 2)

    def test_provider_failures_do_not_retry(self):
        for message in ("HTTP 429 quota", "timeout", "HTTP 499", "HTTP 503", "invalid credentials"):
            with self.subTest(message=message):
                harness = _Harness([ReviewProviderError(message)])
                state = self._run(harness)
                self.assertEqual(state["terminal_route"], "provider_failure")
                self.assertEqual(harness.calls, 1)
                self.assertFalse(state["retry_used"])

    def test_cancellation_routes_without_provider_call(self):
        harness = _Harness([_decision()], cancelled=True)
        state = self._run(harness)
        self.assertEqual(state["terminal_route"], "cancelled")
        self.assertEqual(harness.calls, 0)
        self.assertIsNone(state.get("result"))

    def test_corrective_feedback_is_concise_and_contains_no_sensitive_payload(self):
        harness = _Harness([ReviewProviderOutputError("invalid"), _decision()])
        harness.context["transcript"] = "COMPLETE TRANSCRIPT SENTINEL"
        self._run(harness)
        feedback = harness.feedback[1] or ""
        self.assertNotIn("COMPLETE TRANSCRIPT SENTINEL", feedback)
        self.assertNotIn("GEMINI_API_KEY", feedback)
        self.assertNotIn("C:\\", feedback)
        self.assertLess(len(feedback), 200)

    def test_no_checkpointer_is_configured(self):
        self.assertIsNone(REVIEW_GRAPH.checkpointer)


if __name__ == "__main__":
    unittest.main()
