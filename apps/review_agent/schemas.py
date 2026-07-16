from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt


ReviewMode = Literal["local_stub", "gemini"]
ReviewDecision = Literal["render_ready", "adjust_boundaries", "reject", "manual_review"]
GeminiReviewDecision = Literal["render_ready", "adjust_boundaries", "reject"]
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


class TranscriptSegment(BaseModel):
    segment_id: str
    start: float
    end: float
    text: str
    speaker: str | None = None


class BoundaryOption(BaseModel):
    option_index: int
    segment_id: str
    start: float
    end: float
    text: str


class BoundaryOptionPair(BaseModel):
    start_option_index: int
    end_option_index: int


class ClipTranscriptContext(BaseModel):
    clip_id: str | None = None
    candidate_start: float
    candidate_end: float
    context_seconds: float = 20.0
    context_before: list[TranscriptSegment] = Field(default_factory=list)
    candidate_segments: list[TranscriptSegment] = Field(default_factory=list)
    context_after: list[TranscriptSegment] = Field(default_factory=list)
    earliest_allowed_start: float
    latest_allowed_end: float
    current_aligned_start_option_index: int | None = None
    current_aligned_end_option_index: int | None = None
    current_aligned_start_segment_id: str | None = None
    current_aligned_end_segment_id: str | None = None
    start_boundary_options: list[BoundaryOption] = Field(default_factory=list)
    end_boundary_options: list[BoundaryOption] = Field(default_factory=list)
    allowed_boundary_pairs: list[BoundaryOptionPair] = Field(default_factory=list)


class GeminiBoundaryDecision(BaseModel):
    decision: GeminiReviewDecision
    selected_start_option_index: StrictInt
    selected_end_option_index: StrictInt
    reasoning_summary: str
    start_reason: str
    end_reason: str
    warnings: list[str] = Field(default_factory=list)


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
    provider: str = "local_stub"
    model: str = "local_stub"
    decision: str
    recommended_action: RecommendedAction
    quality_score: float | None = None
    context_score: float | None = None
    hook_score: float | None = None
    payoff_score: float | None = None
    boundary_score: float | None = None
    privacy_risk: PrivacyRisk | None = None
    needs_more_context: bool = False
    selected_start_option_index: int | None = None
    selected_end_option_index: int | None = None
    selected_start_segment_id: str | None = None
    selected_end_segment_id: str | None = None
    suggested_start: float | None = None
    suggested_end: float | None = None
    reviewed_start: float | None = None
    reviewed_end: float | None = None
    start_delta_seconds: float | None = None
    end_delta_seconds: float | None = None
    reasoning_summary: str = ""
    start_reason: str = ""
    end_reason: str = ""
    crop_advice: CropAdvice | None = None
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    context_expansions: int = 0
    context_seconds: float | None = None
    failed: bool = False
    failure_reason: str | None = None
    failure_category: str | None = None
    retry_used: bool = False
    provider_attempt_count: int = 1
    first_attempt_validation_error: str | None = None
    final_validation_error: str | None = None
    raw_result: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
