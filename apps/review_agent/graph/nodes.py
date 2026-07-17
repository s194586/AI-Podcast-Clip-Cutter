from __future__ import annotations

import time
from typing import Any

from langgraph.runtime import Runtime

from .runtime import ReviewGraphRuntime
from .state import ReviewGraphState


def build_review_context(
    state: ReviewGraphState,
    runtime: Runtime[ReviewGraphRuntime],
) -> dict[str, Any]:
    if _is_cancelled(runtime.context):
        return _cancelled_update("build_review_context")
    context, provider = runtime.context.build_context()
    runtime.context.review_context = context
    runtime.context.provider = provider
    return {
        "allowed_boundary_pair_count": len(context.get("allowed_boundary_pairs") or []),
        "current_node": "build_review_context",
    }


def invoke_reviewer(
    state: ReviewGraphState,
    runtime: Runtime[ReviewGraphRuntime],
) -> dict[str, Any]:
    if _is_cancelled(runtime.context):
        return _cancelled_update("invoke_reviewer")
    attempt = int(state.get("attempt_number") or 1)
    try:
        runtime.context.decision = runtime.context.invoke_provider(
            runtime.context.provider,
            runtime.context.review_context or {},
            runtime.context.corrective_feedback,
        )
        decision = runtime.context.decision
        return {
            "attempt_number": attempt,
            "decision": str(decision.decision),
            "selected_start_option_index": int(decision.selected_start_option_index),
            "selected_end_option_index": int(decision.selected_end_option_index),
            "validation_category": None,
            "safe_validation_error": None,
            "provider_failure_classification": None,
            "current_node": "invoke_reviewer",
        }
    except runtime.context.cancelled_errors as exc:
        runtime.context.final_error = exc
        return _cancelled_update("invoke_reviewer")
    except runtime.context.retryable_errors as exc:
        runtime.context.final_error = exc
        return {
            "validation_category": runtime.context.failure_category(exc),
            "safe_validation_error": str(exc),
            "current_node": "invoke_reviewer",
        }
    except runtime.context.provider_errors as exc:
        runtime.context.final_error = exc
        return {
            "provider_failure_classification": runtime.context.failure_category(exc),
            "safe_validation_error": str(exc),
            "current_node": "invoke_reviewer",
        }


def validate_review(
    state: ReviewGraphState,
    runtime: Runtime[ReviewGraphRuntime],
) -> dict[str, Any]:
    if state.get("cancelled") or _is_cancelled(runtime.context):
        return _cancelled_update("validate_review")
    if state.get("provider_failure_classification") or runtime.context.decision is None:
        return {"current_node": "validate_review"}
    try:
        result = runtime.context.validate_decision(
            runtime.context.review_context or {},
            runtime.context.decision,
            _debug_metadata(state),
        )
        runtime.context.validated_result = result
        return {
            "selected_start_segment_id": result.get("selected_start_segment_id"),
            "selected_end_segment_id": result.get("selected_end_segment_id"),
            "mapped_start": result.get("reviewed_start"),
            "mapped_end": result.get("reviewed_end"),
            "validation_category": None,
            "safe_validation_error": None,
            "current_node": "validate_review",
        }
    except runtime.context.retryable_errors as exc:
        runtime.context.final_error = exc
        return {
            "validation_category": runtime.context.failure_category(exc),
            "safe_validation_error": str(exc),
            "current_node": "validate_review",
        }
    except runtime.context.cancelled_errors as exc:
        runtime.context.final_error = exc
        return _cancelled_update("validate_review")
    except runtime.context.provider_errors as exc:
        runtime.context.final_error = exc
        return {
            "provider_failure_classification": runtime.context.failure_category(exc),
            "safe_validation_error": str(exc),
            "current_node": "validate_review",
        }


def prepare_corrective_retry(
    state: ReviewGraphState,
    runtime: Runtime[ReviewGraphRuntime],
) -> dict[str, Any]:
    error = runtime.context.final_error or RuntimeError("Invalid structured boundary response.")
    runtime.context.first_validation_error = error
    runtime.context.corrective_feedback = runtime.context.corrective_message(
        runtime.context.review_context or {},
        error,
    )
    runtime.context.decision = None
    runtime.context.validated_result = None
    runtime.context.final_error = None
    return {
        "attempt_number": 2,
        "retry_used": True,
        "first_attempt_validation_error": str(error),
        "validation_category": None,
        "safe_validation_error": None,
        "current_node": "prepare_corrective_retry",
    }


def apply_review(
    state: ReviewGraphState,
    runtime: Runtime[ReviewGraphRuntime],
) -> dict[str, Any]:
    if _is_cancelled(runtime.context):
        return _cancelled_update("apply_review")
    return {
        "result": runtime.context.validated_result,
        "terminal_route": "applied",
        "current_node": "apply_review",
        "duration_ms": _duration_ms(state),
    }


def finalize_manual_review(
    state: ReviewGraphState,
    runtime: Runtime[ReviewGraphRuntime],
) -> dict[str, Any]:
    error = runtime.context.final_error or RuntimeError("Boundary response remained invalid.")
    result = runtime.context.failed_result(
        runtime.context.review_context or {},
        str(error),
        runtime.context.failure_category(error),
        _debug_metadata(state, final_error=error),
    )
    return {
        "result": result,
        "terminal_route": "manual_review",
        "current_node": "finalize_manual_review",
        "duration_ms": _duration_ms(state),
    }


def finalize_provider_failure(
    state: ReviewGraphState,
    runtime: Runtime[ReviewGraphRuntime],
) -> dict[str, Any]:
    error = runtime.context.final_error or RuntimeError("Boundary provider failed.")
    result = runtime.context.failed_result(
        runtime.context.review_context or {},
        str(error),
        state.get("provider_failure_classification") or runtime.context.failure_category(error),
        _debug_metadata(state, final_error=error),
    )
    return {
        "result": result,
        "terminal_route": "provider_failure",
        "current_node": "finalize_provider_failure",
        "duration_ms": _duration_ms(state),
    }


def finalize_cancelled(
    state: ReviewGraphState,
    runtime: Runtime[ReviewGraphRuntime],
) -> dict[str, Any]:
    return {
        "cancelled": True,
        "terminal_route": "cancelled",
        "current_node": "finalize_cancelled",
        "duration_ms": _duration_ms(state),
    }


def _debug_metadata(
    state: ReviewGraphState,
    *,
    final_error: Exception | None = None,
) -> dict[str, Any]:
    return {
        "retry_used": bool(state.get("retry_used")),
        "provider_attempt_count": int(state.get("attempt_number") or 1),
        "first_attempt_validation_error": (
            state.get("first_attempt_validation_error")
        ),
        "final_validation_error": str(final_error) if final_error is not None else None,
    }


def _is_cancelled(runtime: ReviewGraphRuntime) -> bool:
    return bool(runtime.cancellation_check and runtime.cancellation_check())


def _cancelled_update(node: str) -> dict[str, Any]:
    return {
        "cancelled": True,
        "current_node": node,
    }


def _duration_ms(state: ReviewGraphState) -> int:
    started_ns = int(state.get("started_ns", time.monotonic_ns()))
    return max(0, int((time.monotonic_ns() - started_ns) / 1_000_000))
