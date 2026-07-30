from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from apps.review_agent.graph import (
    GRAPH_WORKFLOW_NAME,
    GRAPH_WORKFLOW_VERSION,
    REVIEW_GRAPH,
    run_review_workflow,
)
from apps.review_agent.graph.nodes import (
    apply_review,
    build_review_context,
    invoke_reviewer,
    prepare_corrective_retry,
    validate_review,
)
from apps.review_agent.graph.runtime import ReviewGraphRuntime
from apps.review_agent.providers import ReviewProviderError, ReviewProviderOutputError
from apps.review_agent.schemas import GeminiBoundaryDecision
from apps.review_agent.service import BoundaryOptionSelectionError


def _decision(
    decision: str = "adjust_boundaries",
    start_segment_id: str = "segment-1",
    end_segment_id: str = "segment-2",
) -> GeminiBoundaryDecision:
    return GeminiBoundaryDecision(
        review_response_contract_version=2,
        decision=decision,
        start_segment_id=start_segment_id,
        end_segment_id=end_segment_id,
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
        if decision.start_segment_id != "segment-1" or decision.end_segment_id != "segment-2":
            raise BoundaryOptionSelectionError("Selected pair is not in allowed_boundary_pairs.")
        return {
            "decision": decision.decision,
            "selected_start_option_index": 1,
            "selected_end_option_index": 2,
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
    def _run(
        self,
        harness: _Harness,
        runtime: ReviewGraphRuntime | None = None,
    ) -> dict[str, Any]:
        return run_review_workflow(
            runtime=runtime or harness.runtime(),
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

    def test_workflow_version_is_two(self):
        self.assertEqual(GRAPH_WORKFLOW_VERSION, "2")

    def test_valid_adjustment_is_applied_offline(self):
        harness = _Harness([_decision()])
        state = self._run(harness)
        self.assertEqual(state["terminal_route"], "applied")
        self.assertEqual(state["result"]["reviewed_start"], 10.0)
        self.assertEqual(state["provider_selected_start_segment_id"], "segment-1")
        self.assertEqual(state["validated_start_segment_id"], "segment-1")
        self.assertEqual(state["validated_start_option_index"], 1)
        self.assertEqual(state["review_request_contract_version"], 3)
        self.assertEqual(state["review_response_contract_version"], 2)
        self.assertEqual(harness.calls, 1)
        self.assertEqual(state["provider_attempt_count"], 1)
        self.assertFalse(state["retry_used"])
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
        self.assertEqual(state["provider_attempt_count"], 2)
        self.assertEqual(harness.calls, 2)
        self.assertIsNone(harness.feedback[0])
        self.assertIn("allowed pair", harness.feedback[1])

    def test_provider_selection_is_separate_from_backend_validation(self):
        harness = _Harness([_decision()])
        runtime = harness.runtime()
        node_runtime = SimpleNamespace(context=runtime)
        state: dict[str, Any] = {"attempt_number": 1, "provider_attempt_count": 0}
        state.update(build_review_context(state, node_runtime))
        state.update(invoke_reviewer(state, node_runtime))

        self.assertEqual(state["provider_selected_start_segment_id"], "segment-1")
        self.assertNotIn("selected_start_option_index", state)
        self.assertIsNone(state["validated_start_option_index"])
        self.assertIsNone(state["mapped_start"])
        self.assertIsNone(state["validated_result"])

        state.update(validate_review(state, node_runtime))
        self.assertEqual(state["validated_start_segment_id"], "segment-1")
        self.assertEqual(state["validated_start_option_index"], 1)
        self.assertEqual(state["mapped_start"], 10.0)
        self.assertIsInstance(state["validated_result"], dict)

    def test_corrective_retry_clears_provider_and_validation_state(self):
        harness = _Harness([_decision()])
        runtime = harness.runtime()
        node_runtime = SimpleNamespace(context=runtime)
        runtime.final_error = BoundaryOptionSelectionError("Invalid selected pair.")
        runtime.decision = _decision()
        runtime.validated_result = {"reviewed_start": 10.0, "reviewed_end": 40.0}
        state: dict[str, Any] = {
            "attempt_number": 1,
            "provider_attempt_count": 1,
            "retry_used": False,
            "provider_decision": "adjust_boundaries",
            "provider_selected_start_segment_id": "segment-1",
            "provider_selected_end_segment_id": "segment-2",
            "review_response_contract_version": 2,
            "validated_start_segment_id": "segment-1",
            "validated_end_segment_id": "segment-2",
            "validated_start_option_index": 1,
            "validated_end_option_index": 2,
            "mapped_start": 10.0,
            "mapped_end": 40.0,
            "validated_result": runtime.validated_result,
            "validated_attempt_number": 1,
        }
        state.update(prepare_corrective_retry(state, node_runtime))

        for field in (
            "provider_decision",
            "provider_selected_start_segment_id",
            "provider_selected_end_segment_id",
            "validated_start_segment_id",
            "validated_end_segment_id",
            "validated_start_option_index",
            "validated_end_option_index",
            "mapped_start",
            "mapped_end",
            "validated_result",
            "validated_attempt_number",
        ):
            self.assertIsNone(state[field], field)
        self.assertIsNone(runtime.validated_result)
        self.assertEqual(state["attempt_number"], 2)
        self.assertEqual(state["provider_attempt_count"], 1)
        self.assertFalse(state["retry_used"])

    def test_apply_review_rejects_missing_or_stale_validated_result(self):
        harness = _Harness([_decision()])
        node_runtime = SimpleNamespace(context=harness.runtime())
        for state in (
            {"attempt_number": 1, "validated_result": None, "validated_attempt_number": None},
            {"attempt_number": 2, "validated_result": {"decision": "adjust_boundaries"}, "validated_attempt_number": 1},
        ):
            with self.subTest(state=state):
                with self.assertRaisesRegex(RuntimeError, "current provider attempt"):
                    apply_review(state, node_runtime)

    def test_invalid_pair_gets_one_retry_then_manual_review(self):
        harness = _Harness([
            _decision(start_segment_id="segment-99"),
            _decision(start_segment_id="segment-98"),
        ])
        state = self._run(harness)
        self.assertEqual(state["terminal_route"], "manual_review")
        self.assertEqual(state["result"]["decision"], "manual_review")
        self.assertEqual(state["result"]["failure_category"], "boundary_validation")
        self.assertEqual(harness.calls, 2)
        self.assertEqual(state["provider_attempt_count"], 2)
        self.assertTrue(state["retry_used"])
        self.assertIsNone(state["validated_result"])
        self.assertIsNone(state["mapped_start"])
        self.assertIsNone(state["provider_selected_start_segment_id"])

    def test_provider_failures_do_not_retry(self):
        for message in ("HTTP 429 quota", "timeout", "HTTP 499", "HTTP 503", "invalid credentials"):
            with self.subTest(message=message):
                harness = _Harness([ReviewProviderError(message)])
                state = self._run(harness)
                self.assertEqual(state["terminal_route"], "provider_failure")
                self.assertEqual(harness.calls, 1)
                self.assertEqual(state["provider_attempt_count"], 1)
                self.assertFalse(state["retry_used"])
                self.assertIsNone(state["validated_result"])
                self.assertIsNone(state["mapped_start"])

    def test_cancellation_routes_without_provider_call(self):
        harness = _Harness([_decision()], cancelled=True)
        state = self._run(harness)
        self.assertEqual(state["terminal_route"], "cancelled")
        self.assertEqual(harness.calls, 0)
        self.assertEqual(state["provider_attempt_count"], 0)
        self.assertFalse(state["retry_used"])
        self.assertIsNone(state.get("result"))

    def test_cancellation_before_second_provider_call_keeps_first_call_count(self):
        harness = _Harness([ReviewProviderOutputError("invalid JSON"), _decision()])
        runtime = harness.runtime()
        original_corrective_message = runtime.corrective_message

        def cancel_after_retry_is_prepared(context: dict[str, Any], error: Exception) -> str:
            message = original_corrective_message(context, error)
            harness.cancelled = True
            return message

        runtime.corrective_message = cancel_after_retry_is_prepared
        state = self._run(harness, runtime)

        self.assertEqual(state["terminal_route"], "cancelled")
        self.assertEqual(harness.calls, 1)
        self.assertEqual(state["provider_attempt_count"], 1)
        self.assertFalse(state["retry_used"])
        for field in (
            "provider_selected_start_segment_id",
            "provider_selected_end_segment_id",
            "validated_result",
            "mapped_start",
            "mapped_end",
            "validated_start_option_index",
            "validated_end_option_index",
        ):
            self.assertIsNone(state[field], field)

    def test_provider_failure_during_second_started_call_counts_retry(self):
        harness = _Harness([
            ReviewProviderOutputError("invalid JSON"),
            ReviewProviderError("provider outage"),
        ])
        state = self._run(harness)

        self.assertEqual(state["terminal_route"], "provider_failure")
        self.assertEqual(harness.calls, 2)
        self.assertEqual(state["provider_attempt_count"], 2)
        self.assertTrue(state["retry_used"])

    def test_third_provider_call_is_rejected_before_invocation(self):
        harness = _Harness([_decision()])
        node_runtime = SimpleNamespace(context=harness.runtime())
        with self.assertRaisesRegex(RuntimeError, "more than two provider calls"):
            invoke_reviewer(
                {"attempt_number": 3, "provider_attempt_count": 2},
                node_runtime,
            )
        self.assertEqual(harness.calls, 0)

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
