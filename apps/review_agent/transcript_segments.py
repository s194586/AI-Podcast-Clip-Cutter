from __future__ import annotations

from typing import Any


def _parse_time(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    parts = [part for part in str(value).strip().replace(",", ".").split(":") if part]
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def normalize_transcript_segments(transcript: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    raw_segments = transcript.get("segments", []) if isinstance(transcript, dict) else transcript
    normalized: list[dict[str, Any]] = []
    for item in raw_segments or []:
        if not isinstance(item, dict):
            continue
        try:
            start = _parse_time(item.get("start", 0.0))
            end = _parse_time(item.get("end", start))
        except Exception:
            continue
        if end <= start:
            continue
        normalized.append(
            {
                "start": start,
                "end": end,
                "text": " ".join(str(item.get("text", "")).split()),
                "speaker": str(item.get("speaker") or item.get("speaker_id") or item.get("speakerId") or ""),
                "importance": int(item.get("importance", 3) or 3),
                "chaos": bool(item.get("chaos", False)),
            }
        )
    return sorted(normalized, key=lambda item: item["start"])
