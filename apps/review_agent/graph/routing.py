from __future__ import annotations

from typing import Literal

from .state import ReviewGraphState


ReviewRoute = Literal["apply", "retry", "manual", "provider_failure", "cancelled"]


def route_review_result(state: ReviewGraphState) -> ReviewRoute:
    if state.get("cancelled"):
        return "cancelled"
    if state.get("provider_failure_classification"):
        return "provider_failure"
    if state.get("validation_category"):
        return "manual" if state.get("retry_used") else "retry"
    return "apply"


def route_after_apply(state: ReviewGraphState) -> Literal["end", "cancelled"]:
    return "cancelled" if state.get("cancelled") else "end"
