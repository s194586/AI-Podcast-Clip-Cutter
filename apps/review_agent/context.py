from __future__ import annotations

from pathlib import Path
from typing import Any

from .schemas import BoundaryOption, ClipTranscriptContext, TranscriptSegment
from .tools import load_transcript_segments


DEFAULT_REVIEW_CONTEXT_SECONDS = 20.0


def build_clip_transcript_context(
    transcript_path: Path | str | None,
    clip_start: float,
    clip_end: float,
    context_seconds: float = DEFAULT_REVIEW_CONTEXT_SECONDS,
    *,
    clip_id: str | None = None,
) -> dict[str, Any]:
    """Build the compact transcript-only payload used by the boundary reviewer."""

    segments = _with_stable_ids(load_transcript_segments(transcript_path))
    return build_clip_transcript_context_from_segments(
        segments,
        clip_start,
        clip_end,
        context_seconds=context_seconds,
        clip_id=clip_id,
    )


def build_clip_transcript_context_from_segments(
    segments: list[dict[str, Any]],
    clip_start: float,
    clip_end: float,
    *,
    context_seconds: float = DEFAULT_REVIEW_CONTEXT_SECONDS,
    clip_id: str | None = None,
) -> dict[str, Any]:
    start = float(clip_start)
    end = float(clip_end)
    padding = max(0.0, float(context_seconds))
    context_start = max(0.0, start - padding)
    context_end = end + padding

    normalized = _with_stable_ids(segments)
    before = [
        segment
        for segment in normalized
        if _overlap_seconds(context_start, start, float(segment["start"]), float(segment["end"])) > 0
        and float(segment["end"]) <= start
    ]
    candidate = [
        segment
        for segment in normalized
        if _overlap_seconds(start, end, float(segment["start"]), float(segment["end"])) > 0
    ]
    after = [
        segment
        for segment in normalized
        if _overlap_seconds(end, context_end, float(segment["start"]), float(segment["end"])) > 0
        and float(segment["start"]) >= end
    ]

    start_options = _boundary_options(before + candidate)
    end_options = _boundary_options(candidate + after)
    earliest_allowed_start = start_options[0]["start"] if start_options else round(start, 2)
    latest_allowed_end = end_options[-1]["end"] if end_options else round(end, 2)
    current_aligned_start_option = _nearest_option(start_options, start, boundary="start")
    current_aligned_end_option = _nearest_option(end_options, end, boundary="end")
    current_aligned_start_option_index = (
        int(current_aligned_start_option["option_index"]) if current_aligned_start_option else None
    )
    current_aligned_end_option_index = (
        int(current_aligned_end_option["option_index"]) if current_aligned_end_option else None
    )
    current_aligned_start_segment_id = (
        str(current_aligned_start_option["segment_id"]) if current_aligned_start_option else None
    )
    current_aligned_end_segment_id = str(current_aligned_end_option["segment_id"]) if current_aligned_end_option else None

    context = ClipTranscriptContext(
        clip_id=clip_id,
        candidate_start=round(start, 2),
        candidate_end=round(end, 2),
        context_seconds=round(padding, 2),
        context_before=[_public_segment(segment) for segment in before],
        candidate_segments=[_public_segment(segment) for segment in candidate],
        context_after=[_public_segment(segment) for segment in after],
        earliest_allowed_start=round(float(earliest_allowed_start), 2),
        latest_allowed_end=round(float(latest_allowed_end), 2),
        current_aligned_start_option_index=current_aligned_start_option_index,
        current_aligned_end_option_index=current_aligned_end_option_index,
        current_aligned_start_segment_id=current_aligned_start_segment_id,
        current_aligned_end_segment_id=current_aligned_end_segment_id,
        start_boundary_options=[BoundaryOption(**option) for option in start_options],
        end_boundary_options=[BoundaryOption(**option) for option in end_options],
    )
    return _dump_model(context)


def segment_map(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for key in ("context_before", "candidate_segments", "context_after"):
        segments.extend(dict(segment) for segment in context.get(key) or [])
    return {str(segment["segment_id"]): segment for segment in segments}


def _with_stable_ids(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, segment in enumerate(sorted(segments or [], key=lambda item: float(item.get("start") or 0.0)), start=1):
        start = round(float(segment.get("start") or 0.0), 2)
        end = round(float(segment.get("end") or start), 2)
        if end <= start:
            continue
        normalized.append(
            {
                "segment_id": str(segment.get("segment_id") or _stable_segment_id(index, start, end)),
                "start": start,
                "end": end,
                "text": " ".join(str(segment.get("text") or "").split()),
                "speaker": _optional_speaker(segment),
            }
        )
    return normalized


def _stable_segment_id(index: int, start: float, end: float) -> str:
    start_cs = int(round(start * 100))
    end_cs = int(round(end * 100))
    return f"seg_{index:05d}_{start_cs}_{end_cs}"


def _optional_speaker(segment: dict[str, Any]) -> str | None:
    speaker = segment.get("speaker")
    if speaker in (None, ""):
        speaker = segment.get("speaker_id")
    text = str(speaker).strip() if speaker not in (None, "") else ""
    return text or None


def _public_segment(segment: dict[str, Any]) -> TranscriptSegment:
    return TranscriptSegment(
        segment_id=str(segment["segment_id"]),
        start=round(float(segment["start"]), 2),
        end=round(float(segment["end"]), 2),
        text=str(segment.get("text") or ""),
        speaker=_optional_speaker(segment),
    )


def _boundary_options(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    options: list[dict[str, Any]] = []
    for segment in segments:
        segment_id = str(segment["segment_id"])
        if segment_id in seen:
            continue
        seen.add(segment_id)
        options.append(
            {
                "option_index": len(options) + 1,
                "segment_id": segment_id,
                "start": round(float(segment["start"]), 2),
                "end": round(float(segment["end"]), 2),
                "text": str(segment.get("text") or ""),
            }
        )
    return options


def _nearest_option(options: list[dict[str, Any]], target: float, *, boundary: str) -> dict[str, Any] | None:
    if not options:
        return None
    field = "end" if boundary == "end" else "start"
    return min(options, key=lambda option: abs(float(option[field]) - float(target)))


def _overlap_seconds(start: float, end: float, item_start: float, item_end: float) -> float:
    return max(0.0, min(float(end), float(item_end)) - max(float(start), float(item_start)))


def _dump_model(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
