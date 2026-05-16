from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ClipCandidate:
    candidate_start: float
    candidate_end: float
    story_score: float
    hook_score: float
    context_score: float
    payoff_score: float
    reason: str = ""
    reject_reason: str = ""
    is_ad_or_intro: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VideoUnderstandingResult:
    provider: str
    candidates: list[ClipCandidate] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidates"] = [candidate.to_dict() for candidate in self.candidates]
        return payload


class BaseVideoUnderstandingProvider:
    provider_name = "base"

    def analyze(self, media_path: Path, **kwargs: Any) -> VideoUnderstandingResult:
        raise NotImplementedError


class GeminiVideoProvider(BaseVideoUnderstandingProvider):
    provider_name = "gemini_video"
    api_env_var = "GEMINI_API_KEY"

    def analyze(self, media_path: Path, **kwargs: Any) -> VideoUnderstandingResult:
        raise RuntimeError(
            f"{self.provider_name} provider is a placeholder. Set up {self.api_env_var} and implement timestamped video understanding in a dedicated integration step."
        )
