from __future__ import annotations

from typing import Any, Literal, TypedDict


TerminalRoute = Literal[
    "applied",
    "manual_review",
    "provider_failure",
    "cancelled",
]


class ReviewGraphState(TypedDict, total=False):
    project_id: int
    clip_id: str
    review_mode: str
    attempt_number: int
    provider_attempt_count: int
    retry_used: bool
    original_candidate_start: float
    original_candidate_end: float
    existing_reviewed_start: float | None
    existing_reviewed_end: float | None
    existing_edited_start: float
    existing_edited_end: float
    allowed_boundary_pair_count: int
    provider_decision: str | None
    provider_selected_start_segment_id: str | None
    provider_selected_end_segment_id: str | None
    review_request_contract_version: int
    review_response_contract_version: int | None
    validated_start_segment_id: str | None
    validated_end_segment_id: str | None
    validated_start_option_index: int | None
    validated_end_option_index: int | None
    mapped_start: float | None
    mapped_end: float | None
    validated_result: dict[str, Any] | None
    validated_attempt_number: int | None
    validation_category: str | None
    safe_validation_error: str | None
    first_attempt_validation_error: str | None
    provider_failure_classification: str | None
    terminal_route: TerminalRoute | None
    cancelled: bool
    current_node: str
    workflow_name: str
    workflow_version: str
    duration_ms: int
    started_ns: int
    result: dict[str, Any] | None
