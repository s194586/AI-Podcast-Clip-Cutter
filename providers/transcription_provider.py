from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str = ""
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TranscriptResult:
    provider: str
    language: str
    segments: list[Segment] = field(default_factory=list)
    speaker_count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["segments"] = [segment.to_dict() for segment in self.segments]
        return payload


class BaseTranscriptionProvider:
    provider_name = "base"

    def transcribe(self, media_path: Path, **kwargs: Any) -> TranscriptResult:
        raise NotImplementedError

    def _missing_api_key(self, env_var_name: str) -> RuntimeError:
        return RuntimeError(
            f"{self.provider_name} provider requires {env_var_name} to be set before transcription."
        )


class LocalWhisperProvider(BaseTranscriptionProvider):
    provider_name = "local_whisper"

    def transcribe(self, media_path: Path, **kwargs: Any) -> TranscriptResult:
        raise RuntimeError(
            "LocalWhisperProvider is a placeholder adapter. Continue using transcribe.py or wire this adapter in a dedicated migration step."
        )


class AssemblyAIProvider(BaseTranscriptionProvider):
    provider_name = "assemblyai"
    api_env_var = "ASSEMBLYAI_API_KEY"

    def transcribe(self, media_path: Path, **kwargs: Any) -> TranscriptResult:
        raise self._missing_api_key(self.api_env_var)


class DeepgramProvider(BaseTranscriptionProvider):
    provider_name = "deepgram"
    api_env_var = "DEEPGRAM_API_KEY"

    def transcribe(self, media_path: Path, **kwargs: Any) -> TranscriptResult:
        raise self._missing_api_key(self.api_env_var)
