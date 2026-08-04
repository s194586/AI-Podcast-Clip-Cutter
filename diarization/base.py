from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_DIARIZATION_MODEL_ID = "pyannote/speaker-diarization-community-1"
DEFAULT_DIARIZATION_MODEL_REVISION = "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee"
SUPPORTED_DIARIZATION_MODES = ("pyannote", "off")


@dataclass(frozen=True)
class DiarizationConfig:
    mode: str = "pyannote"
    model_id: str = DEFAULT_DIARIZATION_MODEL_ID
    model_revision: str = DEFAULT_DIARIZATION_MODEL_REVISION
    device: str = "cpu"
    num_speakers: int | None = None
    min_speakers: int | None = None
    max_speakers: int | None = None

    def __post_init__(self) -> None:
        mode = str(self.mode or "").strip().lower()
        if mode not in SUPPORTED_DIARIZATION_MODES:
            raise ValueError("DIARIZATION_MODE must be one of: pyannote, off.")
        device = str(self.device or "").strip().lower()
        if device != "cpu":
            raise ValueError("DIARIZATION_DEVICE must be cpu; the diarization MVP is CPU-only.")
        model_id = str(self.model_id or "").strip()
        model_revision = str(self.model_revision or "").strip()
        if not model_id:
            raise ValueError("DIARIZATION_MODEL_ID must not be empty.")
        if not model_revision:
            raise ValueError("DIARIZATION_MODEL_REVISION must not be empty.")

        num_speakers = _optional_positive_integer(self.num_speakers, "DIARIZATION_NUM_SPEAKERS")
        min_speakers = _optional_positive_integer(self.min_speakers, "DIARIZATION_MIN_SPEAKERS")
        max_speakers = _optional_positive_integer(self.max_speakers, "DIARIZATION_MAX_SPEAKERS")
        if num_speakers is not None and (min_speakers is not None or max_speakers is not None):
            raise ValueError(
                "DIARIZATION_NUM_SPEAKERS is mutually exclusive with "
                "DIARIZATION_MIN_SPEAKERS and DIARIZATION_MAX_SPEAKERS."
            )
        if min_speakers is not None and max_speakers is not None and min_speakers > max_speakers:
            raise ValueError("DIARIZATION_MIN_SPEAKERS must be less than or equal to DIARIZATION_MAX_SPEAKERS.")

        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "model_id", model_id)
        object.__setattr__(self, "model_revision", model_revision)
        object.__setattr__(self, "device", device)
        object.__setattr__(self, "num_speakers", num_speakers)
        object.__setattr__(self, "min_speakers", min_speakers)
        object.__setattr__(self, "max_speakers", max_speakers)

    @property
    def enabled(self) -> bool:
        return self.mode == "pyannote"

    def speaker_constraints(self) -> dict[str, int]:
        if self.num_speakers is not None:
            return {"num_speakers": self.num_speakers}
        constraints: dict[str, int] = {}
        if self.min_speakers is not None:
            constraints["min_speakers"] = self.min_speakers
        if self.max_speakers is not None:
            constraints["max_speakers"] = self.max_speakers
        return constraints


@dataclass(frozen=True)
class SpeakerTurn:
    """Model-independent speaker interval carrying the backend's anonymous label."""

    start: float
    end: float
    raw_speaker: str


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


class OffDiarizationBackend(DiarizationBackend):
    name = "off"

    def __init__(self, config: DiarizationConfig):
        if config.mode != "off":
            raise ValueError("OffDiarizationBackend requires DIARIZATION_MODE=off.")
        self.config = config

    def assign_speakers(self, audio_path: Path, segments: list[Any]) -> DiarizationResult:
        del audio_path
        return DiarizationResult(
            backend=self.name,
            enabled=False,
            status="no_speech" if not segments else "disabled",
            speaker_count=0,
            diarization_seconds=0.0,
            used_fallback=False,
            extra_metadata={
                "diarization_mode": self.config.mode,
                "diarization_model_id": self.config.model_id,
                "diarization_model_revision": self.config.model_revision,
                "diarization_device": self.config.device,
                "diarization_exclusive": False,
                "diarization_turn_count": 0,
                "speaker_label_map": {},
                "non_overlapping_word_count": 0,
                "max_non_overlap_distance_seconds": 0.0,
            },
        )


def _optional_positive_integer(value: Any, field_name: str) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a positive integer when provided.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer when provided.") from exc
    if parsed <= 0 or (isinstance(value, float) and not value.is_integer()):
        raise ValueError(f"{field_name} must be a positive integer when provided.")
    return parsed
