from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any


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
    speaker: str = "Speaker 0"
    importance: int = 3
    chaos: bool = False
    words: list[TranscriptWord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "start": sec_to_hms(self.start),
            "end": sec_to_hms(self.end),
            "text": self.text,
            "speaker": normalize_speaker_label(self.speaker),
            "importance": int(self.importance),
            "chaos": bool(self.chaos),
        }
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
        return {
            "segments": [segment.to_dict() for segment in self.segments],
            "metadata": metadata,
        }


def sec_to_hms(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:05.2f}"
    return f"{minutes:02d}:{secs:05.2f}"


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
