from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DiarizationConfig:
    backend: str = "heuristic_cluster"
    enabled: bool = True
    sample_rate: int = 16000
    max_speakers: int = 4
    min_segment_seconds: float = 0.35
    similarity_threshold: float = 0.8


@dataclass
class DiarizationResult:
    backend: str
    enabled: bool
    status: str
    speaker_count: int
    diarization_seconds: float
    used_fallback: bool
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "backend": self.backend,
            "enabled": self.enabled,
            "status": self.status,
            "speaker_count": self.speaker_count,
            "diarization_seconds": round(self.diarization_seconds, 3),
            "used_fallback": self.used_fallback,
        }
        payload.update(self.extra_metadata)
        return payload


class DiarizationBackend:
    name = "base"

    def assign_speakers(self, audio_path: Path, segments: list[Any]) -> DiarizationResult:
        raise NotImplementedError
