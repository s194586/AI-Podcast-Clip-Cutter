from __future__ import annotations

import time
from typing import Any

from langgraph.graph import END, START, StateGraph

from .nodes import (
    apply_review,
    build_review_context,
    finalize_cancelled,
    finalize_manual_review,
    finalize_provider_failure,
    invoke_reviewer,
    prepare_corrective_retry,
    validate_review,
)
from .routing import route_after_apply, route_review_result
from .runtime import ReviewGraphRuntime
from .state import ReviewGraphState


GRAPH_WORKFLOW_NAME = "langgraph_boundary_review"
GRAPH_WORKFLOW_VERSION = "1"


def build_review_graph():
    builder = StateGraph(ReviewGraphState, context_schema=ReviewGraphRuntime)
    builder.add_node("build_review_context", build_review_context)
    builder.add_node("invoke_reviewer", invoke_reviewer)
    builder.add_node("validate_review", validate_review)
    builder.add_node("prepare_corrective_retry", prepare_corrective_retry)
    builder.add_node("apply_review", apply_review)
    builder.add_node("finalize_manual_review", finalize_manual_review)
    builder.add_node("finalize_provider_failure", finalize_provider_failure)
    builder.add_node("finalize_cancelled", finalize_cancelled)

    builder.add_edge(START, "build_review_context")
    builder.add_edge("build_review_context", "invoke_reviewer")
    builder.add_edge("invoke_reviewer", "validate_review")
    builder.add_conditional_edges(
        "validate_review",
        route_review_result,
        {
            "apply": "apply_review",
            "retry": "prepare_corrective_retry",
            "manual": "finalize_manual_review",
            "provider_failure": "finalize_provider_failure",
            "cancelled": "finalize_cancelled",
        },
    )
    builder.add_edge("prepare_corrective_retry", "invoke_reviewer")
    builder.add_conditional_edges(
        "apply_review",
        route_after_apply,
        {"end": END, "cancelled": "finalize_cancelled"},
    )
    builder.add_edge("finalize_manual_review", END)
    builder.add_edge("finalize_provider_failure", END)
    builder.add_edge("finalize_cancelled", END)
    return builder.compile(name=GRAPH_WORKFLOW_NAME)


REVIEW_GRAPH = build_review_graph()


def run_review_workflow(
    *,
    runtime: ReviewGraphRuntime,
    initial_state: ReviewGraphState,
) -> ReviewGraphState:
    state: dict[str, Any] = {
        **initial_state,
        "attempt_number": 1,
        "retry_used": False,
        "cancelled": False,
        "terminal_route": None,
        "workflow_name": GRAPH_WORKFLOW_NAME,
        "workflow_version": GRAPH_WORKFLOW_VERSION,
        "started_ns": time.monotonic_ns(),
    }
    return REVIEW_GRAPH.invoke(state, context=runtime)
