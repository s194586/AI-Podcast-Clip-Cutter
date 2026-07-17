from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


EVENT_MARKER = "@@PIPELINE_EVENT@@"
EVENT_TYPES = {
    "stage_started",
    "stage_progress",
    "stage_completed",
    "stage_failed",
    "stage_retrying",
    "pipeline_completed",
    "pipeline_cancelled",
    "review_clip_started",
    "review_clip_completed",
    "review_clip_manual",
    "review_clip_failed",
}

STAGE_PROGRESS = {
    "waiting": 0.0,
    "downloading": 10.0,
    "transcribing": 30.0,
    "validating_transcript": 45.0,
    "generating_candidates": 60.0,
    "importing_candidates": 75.0,
    "reviewing_with_ai": 85.0,
    "ready": 100.0,
}

STAGE_MESSAGES = {
    "waiting": "Waiting to start",
    "downloading": "Downloading source media",
    "transcribing": "Transcribing podcast",
    "validating_transcript": "Validating transcript",
    "generating_candidates": "Generating candidate clips",
    "importing_candidates": "Importing candidate clips",
    "reviewing_with_ai": "Reviewing boundaries with AI",
    "ready": "Ready for review",
    "failed": "Failed",
    "cancelled": "Cancelled",
}

SECRET_KEY_RE = re.compile(r"(?i)(api[_-]?key|password|secret|token|authorization)")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(gemini_api_key|google_api_key|api[_-]?key|password|secret|token)\s*=\s*([^\s,;]+)"
)
SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|key|token|access_token|signature|sig|authorization)=)([^&\s\"']+)"
)
BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s,;]+)")


@dataclass(frozen=True)
class PipelineEvent:
    event: str
    stage: str | None
    message: str
    progress_percent: float | None = None
    success: bool | None = None
    produced_artifacts: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    error_category: str | None = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )

    def __post_init__(self) -> None:
        if self.event not in EVENT_TYPES:
            raise ValueError(f"Unsupported pipeline event type: {self.event}")
        object.__setattr__(self, "message", redact_text(self.message))
        object.__setattr__(
            self,
            "produced_artifacts",
            tuple(redact_text(str(item)) for item in self.produced_artifacts),
        )
        object.__setattr__(self, "metadata", sanitize_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": 1,
            "event": self.event,
            "stage": self.stage,
            "message": redact_text(self.message),
            "timestamp": self.timestamp,
        }
        if self.progress_percent is not None:
            payload["progress_percent"] = max(0.0, min(100.0, float(self.progress_percent)))
        if self.success is not None:
            payload["success"] = bool(self.success)
        if self.produced_artifacts:
            payload["produced_artifacts"] = [redact_text(str(item)) for item in self.produced_artifacts]
        if self.metadata:
            payload["metadata"] = sanitize_metadata(self.metadata)
        if self.error_category:
            payload["error_category"] = str(self.error_category)
        return payload

    def to_marker(self) -> str:
        return f"{EVENT_MARKER} {json.dumps(self.to_dict(), ensure_ascii=True, sort_keys=True)}"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PipelineEvent":
        return cls(
            event=str(payload["event"]),
            stage=str(payload["stage"]) if payload.get("stage") is not None else None,
            message=str(payload.get("message") or ""),
            progress_percent=(
                float(payload["progress_percent"])
                if payload.get("progress_percent") is not None
                else None
            ),
            success=bool(payload["success"]) if payload.get("success") is not None else None,
            produced_artifacts=tuple(str(item) for item in payload.get("produced_artifacts") or []),
            metadata=sanitize_metadata(dict(payload.get("metadata") or {})),
            error_category=(
                str(payload["error_category"])
                if payload.get("error_category") is not None
                else None
            ),
            timestamp=str(payload.get("timestamp") or datetime.now(timezone.utc).isoformat()),
        )


def parse_pipeline_event(line: str) -> PipelineEvent | None:
    text = str(line or "").strip()
    if not text.startswith(EVENT_MARKER):
        return None
    raw_payload = text[len(EVENT_MARKER) :].strip()
    try:
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            return None
        return PipelineEvent.from_dict(payload)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def progress_for_stage(stage: str) -> float | None:
    return STAGE_PROGRESS.get(str(stage))


def message_for_stage(stage: str) -> str:
    return STAGE_MESSAGES.get(stage, str(stage).replace("_", " ").title())


def redact_text(value: str) -> str:
    redacted = SECRET_ASSIGNMENT_RE.sub(r"\1=<redacted>", str(value))
    redacted = SECRET_QUERY_RE.sub(r"\1<redacted>", redacted)
    return BEARER_RE.sub(r"\1<redacted>", redacted)


def sanitize_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): ("<redacted>" if SECRET_KEY_RE.search(str(key)) else sanitize_metadata(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_metadata(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)
