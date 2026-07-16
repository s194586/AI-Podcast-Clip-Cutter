from __future__ import annotations

from typing import Any, TypedDict


class ReviewAgentState(TypedDict, total=False):
    project_id: int
    clip_id: str
    project: dict[str, Any]
    clip: dict[str, Any]
    transcript_path: str | None
    transcript_segments: list[dict[str, Any]]
    context: dict[str, Any]
    context_padding_seconds: float
    context_expansions: int
    max_context_expansions: int
    apply_safe_suggestions: bool
    quality_score: float
    context_score: float
    hook_score: float
    payoff_score: float
    boundary_score: float
    needs_more_context: bool
    sensitive_check: dict[str, Any]
    boundary_suggestion: dict[str, Any]
    crop_suggestion: dict[str, Any]
    decision: str
    recommended_action: str
    reasons: list[str]
    warnings: list[str]
    result: dict[str, Any]
    evaluation_id: int
