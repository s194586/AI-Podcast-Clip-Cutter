from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import re
from typing import Any

from .segment_identity import (
    SEGMENT_ID_SCHEME,
    SEGMENT_ID_VERSION,
    TRANSCRIPT_SCHEMA_VERSION,
    canonical_segment_id,
    canonical_time_range,
    centiseconds_to_hms,
)


PROFANITY_TOKENS = {
    "cholera",
    "fuck",
    "ja pierdole",
    "jebac",
    "jebie",
    "jprdl",
    "kurde",
    "kurwa",
    "pierdole",
    "wtf",
}

EMPHASIS_TOKENS = {
    "ale",
    "czemu",
    "jak",
    "look",
    "nice",
    "patrz",
    "serio",
    "teraz",
    "uwaga",
    "what",
    "wow",
}

WORD_RE = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)


@dataclass
class TranscriptWord:
    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": sec_to_hms(self.start),
            "end": sec_to_hms(self.end),
            "text": self.text,
        }


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    speaker: str = ""
    importance: int = 3
    chaos: bool = False
    words: list[TranscriptWord] = field(default_factory=list)
    extra_fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        start_centiseconds, end_centiseconds = canonical_time_range(self.start, self.end)
        reserved_fields = {
            "segment_id",
            "start",
            "end",
            "text",
            "speaker",
            "importance",
            "chaos",
            "words",
        }
        payload = {
            key: value
            for key, value in self.extra_fields.items()
            if key not in reserved_fields
        }
        payload.update({
            "segment_id": canonical_segment_id(self.start, self.end),
            "start": centiseconds_to_hms(start_centiseconds),
            "end": centiseconds_to_hms(end_centiseconds),
            "text": self.text,
            "importance": int(self.importance),
            "chaos": bool(self.chaos),
        })
        if str(self.speaker or "").strip():
            payload["speaker"] = normalize_speaker_label(self.speaker)
        if self.words:
            payload["words"] = [word.to_dict() for word in self.words]
        return payload


@dataclass
class TranscriptionConfig:
    backend: str = "faster_whisper"
    model: str = "small"
    language: str | None = None
    device: str = "auto"
    compute_type: str = "auto"
    beam_size: int = 5
    vad_filter: bool = True
    word_timestamps: bool = True
    cache_dir: Path = Path("models") / "faster-whisper"


@dataclass
class TranscriptionResult:
    backend: str
    model: str
    audio_path: Path
    language: str
    duration_seconds: float
    transcription_seconds: float
    segments: list[TranscriptSegment]
    device: str
    compute_type: str
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        metadata = {
            "backend": self.backend,
            "model": self.model,
            "audio": str(self.audio_path),
            "language": self.language,
            "duration_seconds": round(self.duration_seconds, 3),
            "transcription_seconds": round(self.transcription_seconds, 3),
            "device": self.device,
            "compute_type": self.compute_type,
        }
        metadata.update(self.extra_metadata)
        segments = [segment.to_dict() for segment in self.segments]
        segment_ids = [segment["segment_id"] for segment in segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("Transcript contains duplicate canonical segment time ranges.")
        return {
            "transcript_schema_version": TRANSCRIPT_SCHEMA_VERSION,
            "segment_id_scheme": SEGMENT_ID_SCHEME,
            "segment_id_version": SEGMENT_ID_VERSION,
            "segments": segments,
            "metadata": metadata,
        }


def sec_to_hms(seconds: float) -> str:
    """Format a general timestamp without applying strict segment validation."""

    if isinstance(seconds, bool):
        raise ValueError("Timestamp must be numeric, not a boolean.")
    try:
        value = Decimal(str(seconds))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Timestamp must be numeric.") from exc
    if not value.is_finite():
        raise ValueError("Timestamp must be finite.")
    centiseconds = int((max(Decimal(0), value) * 100).to_integral_value(rounding=ROUND_HALF_UP))
    return centiseconds_to_hms(centiseconds)


def parse_time_to_seconds(value: str | float | int) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    parts = [float(part) for part in str(value).strip().replace(",", ".").split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


def normalize_speaker_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "Speaker 0"
    match = re.search(r"(\d+)", text)
    if match:
        return f"Speaker {int(match.group(1))}"
    if text.lower().startswith("speaker "):
        suffix = text.split()[-1].strip().upper()
        if len(suffix) == 1 and "A" <= suffix <= "Z":
            return f"Speaker {ord(suffix) - ord('A')}"
    return f"Speaker {text}"


def compact_text(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def tokenize(text: str) -> list[str]:
    return WORD_RE.findall(str(text or "").lower())


def estimate_importance(text: str, duration: float) -> int:
    normalized = compact_text(text)
    if not normalized:
        return 1

    tokens = tokenize(normalized)
    score = 3
    if any(token in normalized.lower() for token in PROFANITY_TOKENS):
        score += 1
    if normalized.count("!") >= 1 or normalized.count("?") >= 2:
        score += 1
    if any(token in tokens for token in EMPHASIS_TOKENS):
        score += 1
    if 0 < duration <= 2.0 and len(tokens) <= 6:
        score += 1
    if len(tokens) >= 22 and duration > 9.0:
        score -= 1
    return max(1, min(5, score))


def estimate_chaos(text: str, duration: float, speaker_confidence: float | None = None) -> bool:
    normalized = compact_text(text)
    if not normalized or duration <= 0:
        return False
    words_per_second = len(tokenize(normalized)) / max(duration, 0.01)
    if speaker_confidence is not None and speaker_confidence < 0.55:
        return True
    return words_per_second >= 4.8 or normalized.count("/") >= 2
