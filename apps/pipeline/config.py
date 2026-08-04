from __future__ import annotations

from dataclasses import dataclass, field
import os

from apps.pipeline.content_mode import normalize_content_type_mode
from diarization import (
    DEFAULT_DIARIZATION_MODEL_ID,
    DEFAULT_DIARIZATION_MODEL_REVISION,
    DiarizationConfig,
)
from layout import normalize_layout_mode
from pipeline_modes import (
    AI_MODE_LOCAL_ONLY,
    allows_gemini,
    default_subtitle_checker_mode,
    normalize_ai_mode,
    normalize_subtitle_checker_mode,
    subtitle_checker_uses_ai,
)


@dataclass(frozen=True)
class PipelineConfig:
    """Safe, serializable pipeline options. Secrets are resolved by providers."""

    cleanup: bool = False
    skip_download: bool = False
    skip_subtitle_checker: bool = False
    skip_smart_context: bool = False
    force_subtitle_checker: bool = False
    auto_fix_subtitles: bool = True
    ai_mode: str = "gemini_optional"
    subtitle_checker_mode: str | None = None
    subtitle_checker_ai_samples: int = 8
    transcription_backend: str = "faster_whisper"
    whisper_model: str = "small"
    transcription_device: str = "auto"
    transcription_compute_type: str = "auto"
    diarization_mode: str = field(default_factory=lambda: _environment_text("DIARIZATION_MODE", "pyannote"))
    diarization_model_id: str = field(
        default_factory=lambda: _environment_text("DIARIZATION_MODEL_ID", DEFAULT_DIARIZATION_MODEL_ID)
    )
    diarization_model_revision: str = field(
        default_factory=lambda: _environment_text(
            "DIARIZATION_MODEL_REVISION", DEFAULT_DIARIZATION_MODEL_REVISION
        )
    )
    diarization_device: str = field(default_factory=lambda: _environment_text("DIARIZATION_DEVICE", "cpu"))
    diarization_num_speakers: int | str | None = field(
        default_factory=lambda: os.environ.get("DIARIZATION_NUM_SPEAKERS")
    )
    diarization_min_speakers: int | str | None = field(
        default_factory=lambda: os.environ.get("DIARIZATION_MIN_SPEAKERS")
    )
    diarization_max_speakers: int | str | None = field(
        default_factory=lambda: os.environ.get("DIARIZATION_MAX_SPEAKERS")
    )
    content_type: str = "auto"
    layout_mode: str = "auto"

    def __post_init__(self) -> None:
        ai_mode = normalize_ai_mode(AI_MODE_LOCAL_ONLY if self.skip_smart_context else self.ai_mode)
        checker_mode = normalize_subtitle_checker_mode(
            self.subtitle_checker_mode or default_subtitle_checker_mode(ai_mode)
        )
        if not allows_gemini(ai_mode) and subtitle_checker_uses_ai(checker_mode):
            checker_mode = "local_only"

        object.__setattr__(self, "ai_mode", ai_mode)
        object.__setattr__(self, "subtitle_checker_mode", checker_mode)
        object.__setattr__(self, "subtitle_checker_ai_samples", max(0, int(self.subtitle_checker_ai_samples)))
        object.__setattr__(self, "transcription_backend", _text_or_default(self.transcription_backend, "faster_whisper"))
        object.__setattr__(self, "whisper_model", _text_or_default(self.whisper_model, "small"))
        object.__setattr__(self, "transcription_device", _text_or_default(self.transcription_device, "auto"))
        object.__setattr__(
            self,
            "transcription_compute_type",
            _text_or_default(self.transcription_compute_type, "auto"),
        )
        diarization = DiarizationConfig(
            mode=self.diarization_mode,
            model_id=self.diarization_model_id,
            model_revision=self.diarization_model_revision,
            device=self.diarization_device,
            num_speakers=self.diarization_num_speakers,
            min_speakers=self.diarization_min_speakers,
            max_speakers=self.diarization_max_speakers,
        )
        object.__setattr__(self, "diarization_mode", diarization.mode)
        object.__setattr__(self, "diarization_model_id", diarization.model_id)
        object.__setattr__(self, "diarization_model_revision", diarization.model_revision)
        object.__setattr__(self, "diarization_device", diarization.device)
        object.__setattr__(self, "diarization_num_speakers", diarization.num_speakers)
        object.__setattr__(self, "diarization_min_speakers", diarization.min_speakers)
        object.__setattr__(self, "diarization_max_speakers", diarization.max_speakers)
        object.__setattr__(self, "content_type", normalize_content_type_mode(self.content_type))
        object.__setattr__(self, "layout_mode", normalize_layout_mode(self.layout_mode))


def _text_or_default(value: str | None, default: str) -> str:
    return str(value or default).strip() or default


def _environment_text(name: str, default: str) -> str:
    return str(os.environ.get(name) or default).strip() or default
