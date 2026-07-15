from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ReviewMode = Literal["local_only", "llm_optional"]
PrivacyRisk = Literal["low", "medium", "high"]
RecommendedAction = Literal[
    "keep",
    "reject",
    "extend_context",
    "adjust_boundaries",
    "render_ready",
    "manual_review",
]
CropAdvice = Literal["speaker_focus", "wider_context", "keep_current", "manual_review"]


class ClipReviewRequest(BaseModel):
    project_id: int | None = None
    clip_id: str


class TranscriptContext(BaseModel):
    context_start: float
    context_end: float
    before_text: str = ""
    clip_text: str = ""
    after_text: str = ""
    segments: list[dict[str, Any]] = Field(default_factory=list)


class CandidateFeatures(BaseModel):
    ai_start: float
    ai_end: float
    edited_start: float
    edited_end: float
    local_score: float | None = None
    local_rank: int | None = None
    selection_reasons: list[Any] = Field(default_factory=list)
    local_features: dict[str, Any] = Field(default_factory=dict)


class SensitiveMatch(BaseModel):
    type: str
    text: str
    severity: Literal["low", "medium", "high"] = "medium"


class SensitiveCheckResult(BaseModel):
    privacy_risk: PrivacyRisk = "low"
    matches: list[SensitiveMatch] = Field(default_factory=list)


class BoundarySuggestion(BaseModel):
    suggested_start: float
    suggested_end: float
    start_advice: str
    end_advice: str
    confidence: float


class CropSuggestion(BaseModel):
    crop_advice: CropAdvice = "keep_current"
    reason: str


class ClipReviewEvaluation(BaseModel):
    project_id: int
    clip_id: str
    database_clip_id: int | None = None
    evaluation_id: int | None = None
    decision: str
    recommended_action: RecommendedAction
    quality_score: float
    context_score: float
    hook_score: float
    payoff_score: float
    boundary_score: float
    privacy_risk: PrivacyRisk
    needs_more_context: bool = False
    suggested_start: float | None = None
    suggested_end: float | None = None
    crop_advice: CropAdvice = "keep_current"
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    context_expansions: int = 0
    raw_result: dict[str, Any] = Field(default_factory=dict)
