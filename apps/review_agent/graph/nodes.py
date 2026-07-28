from __future__ import annotations

import time
from typing import Any

from langgraph.runtime import Runtime

from apps.review_agent.providers import (
    COMPACT_REVIEW_REQUEST_CONTRACT_VERSION,
    REVIEW_RESPONSE_CONTRACT_VERSION,
)

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
    runtime.context.decision = None
    runtime.context.validated_result = None
    runtime.context.final_error = None
    return {
        "allowed_boundary_pair_count": len(context.get("allowed_boundary_pairs") or []),
        "review_request_contract_version": COMPACT_REVIEW_REQUEST_CONTRACT_VERSION,
        "review_response_contract_version": None,
        **_clear_selection_and_validation_state(),
        "current_node": "build_review_context",
    }


def invoke_reviewer(
    state: ReviewGraphState,
    runtime: Runtime[ReviewGraphRuntime],
) -> dict[str, Any]:
    if _is_cancelled(runtime.context):
        return _cancelled_update("invoke_reviewer")
    attempt = int(state.get("attempt_number") or 1)
    provider_attempt_count = int(state.get("provider_attempt_count") or 0) + 1
    if provider_attempt_count > 2:
        raise RuntimeError("invoke_reviewer cannot start more than two provider calls.")
    provider_metadata = {
        "provider_attempt_count": provider_attempt_count,
        "retry_used": provider_attempt_count == 2,
    }
    runtime.context.decision = None
    runtime.context.validated_result = None
    runtime.context.final_error = None
    try:
        runtime.context.decision = runtime.context.invoke_provider(
            runtime.context.provider,
            runtime.context.review_context or {},
            runtime.context.corrective_feedback,
        )
        decision = runtime.context.decision
        return {
            "attempt_number": attempt,
            **provider_metadata,
            "provider_decision": str(decision.decision),
            "provider_selected_start_segment_id": str(decision.start_segment_id),
            "provider_selected_end_segment_id": str(decision.end_segment_id),
            "review_response_contract_version": REVIEW_RESPONSE_CONTRACT_VERSION,
            **_clear_validation_state(),
            "validation_category": None,
            "safe_validation_error": None,
            "provider_failure_classification": None,
            "current_node": "invoke_reviewer",
        }
    except runtime.context.cancelled_errors as exc:
        runtime.context.final_error = exc
        return {
            **provider_metadata,
            **_cancelled_update("invoke_reviewer"),
        }
    except runtime.context.retryable_errors as exc:
        runtime.context.final_error = exc
        return {
            **provider_metadata,
            **_clear_selection_and_validation_state(),
            "validation_category": runtime.context.failure_category(exc),
            "safe_validation_error": str(exc),
            "current_node": "invoke_reviewer",
        }
    except runtime.context.provider_errors as exc:
        runtime.context.final_error = exc
        return {
            **provider_metadata,
            **_clear_selection_and_validation_state(),
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
            "validated_start_option_index": result.get("selected_start_option_index"),
            "validated_end_option_index": result.get("selected_end_option_index"),
            "validated_start_segment_id": result.get("selected_start_segment_id"),
            "validated_end_segment_id": result.get("selected_end_segment_id"),
            "mapped_start": result.get("reviewed_start"),
            "mapped_end": result.get("reviewed_end"),
            "validated_result": result,
            "validated_attempt_number": int(state.get("attempt_number") or 1),
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
        "first_attempt_validation_error": str(error),
        "review_response_contract_version": None,
        **_clear_selection_and_validation_state(),
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
    result = state.get("validated_result")
    if not isinstance(result, dict) or state.get("validated_attempt_number") != state.get("attempt_number"):
        raise RuntimeError("apply_review requires a validated result from the current provider attempt.")
    return {
        "result": result,
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
        **_clear_selection_and_validation_state(),
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
        **_clear_selection_and_validation_state(),
        "terminal_route": "provider_failure",
        "current_node": "finalize_provider_failure",
        "duration_ms": _duration_ms(state),
    }


def finalize_cancelled(
    state: ReviewGraphState,
    runtime: Runtime[ReviewGraphRuntime],
) -> dict[str, Any]:
    return {
        **_clear_selection_and_validation_state(),
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
    provider_attempt_count = int(state.get("provider_attempt_count") or 0)
    if not 0 <= provider_attempt_count <= 2:
        raise RuntimeError("provider_attempt_count must be between 0 and 2.")
    return {
        "retry_used": provider_attempt_count == 2,
        "provider_attempt_count": provider_attempt_count,
        "first_attempt_validation_error": (
            state.get("first_attempt_validation_error")
        ),
        "final_validation_error": str(final_error) if final_error is not None else None,
    }


def _is_cancelled(runtime: ReviewGraphRuntime) -> bool:
    return bool(runtime.cancellation_check and runtime.cancellation_check())


def _cancelled_update(node: str) -> dict[str, Any]:
    return {
        **_clear_selection_and_validation_state(),
        "cancelled": True,
        "current_node": node,
    }


def _clear_selection_and_validation_state() -> dict[str, Any]:
    return {
        "provider_decision": None,
        "provider_selected_start_segment_id": None,
        "provider_selected_end_segment_id": None,
        "review_response_contract_version": None,
        **_clear_validation_state(),
    }


def _clear_validation_state() -> dict[str, Any]:
    return {
        "validated_start_segment_id": None,
        "validated_end_segment_id": None,
        "validated_start_option_index": None,
        "validated_end_option_index": None,
        "mapped_start": None,
        "mapped_end": None,
        "validated_result": None,
        "validated_attempt_number": None,
    }


def _duration_ms(state: ReviewGraphState) -> int:
    started_ns = int(state.get("started_ns", time.monotonic_ns()))
    return max(0, int((time.monotonic_ns() - started_ns) / 1_000_000))
