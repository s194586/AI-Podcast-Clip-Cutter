from __future__ import annotations

from pathlib import Path
from typing import Any

from .schemas import BoundaryOption, BoundaryOptionPair, ClipTranscriptContext, TranscriptSegment
from .tools import load_transcript_segments


DEFAULT_REVIEW_CONTEXT_SECONDS = 20.0
DEFAULT_MIN_REVIEW_DURATION_SECONDS = 10.0
DEFAULT_MAX_REVIEW_DURATION_SECONDS = 90.0


def build_clip_transcript_context(
    transcript_path: Path | str | None,
    clip_start: float,
    clip_end: float,
    context_seconds: float = DEFAULT_REVIEW_CONTEXT_SECONDS,
    *,
    clip_id: str | None = None,
    allowed_start_min: float | None = None,
    allowed_start_max: float | None = None,
    allowed_end_min: float | None = None,
    allowed_end_max: float | None = None,
    min_duration_seconds: float = DEFAULT_MIN_REVIEW_DURATION_SECONDS,
    max_duration_seconds: float = DEFAULT_MAX_REVIEW_DURATION_SECONDS,
) -> dict[str, Any]:
    """Build the compact transcript-only payload used by the boundary reviewer."""

    segments = _with_stable_ids(load_transcript_segments(transcript_path))
    return build_clip_transcript_context_from_segments(
        segments,
        clip_start,
        clip_end,
        context_seconds=context_seconds,
        clip_id=clip_id,
        allowed_start_min=allowed_start_min,
        allowed_start_max=allowed_start_max,
        allowed_end_min=allowed_end_min,
        allowed_end_max=allowed_end_max,
        min_duration_seconds=min_duration_seconds,
        max_duration_seconds=max_duration_seconds,
    )


def build_clip_transcript_context_from_segments(
    segments: list[dict[str, Any]],
    clip_start: float,
    clip_end: float,
    *,
    context_seconds: float = DEFAULT_REVIEW_CONTEXT_SECONDS,
    clip_id: str | None = None,
    allowed_start_min: float | None = None,
    allowed_start_max: float | None = None,
    allowed_end_min: float | None = None,
    allowed_end_max: float | None = None,
    min_duration_seconds: float = DEFAULT_MIN_REVIEW_DURATION_SECONDS,
    max_duration_seconds: float = DEFAULT_MAX_REVIEW_DURATION_SECONDS,
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

    start_min = float(allowed_start_min) if allowed_start_min is not None else context_start
    start_max = float(allowed_start_max) if allowed_start_max is not None else start + padding
    end_min = (
        float(allowed_end_min)
        if allowed_end_min is not None
        else max(start + float(min_duration_seconds), end - padding)
    )
    end_max = float(allowed_end_max) if allowed_end_max is not None else context_end

    start_options = _filter_boundary_options(
        _boundary_options(before + candidate),
        field="start",
        minimum=start_min,
        maximum=start_max,
    )
    end_options = _filter_boundary_options(
        _boundary_options(candidate + after),
        field="end",
        minimum=end_min,
        maximum=end_max,
    )
    allowed_pairs = _allowed_boundary_pairs(
        start_options,
        end_options,
        min_duration_seconds=min_duration_seconds,
        max_duration_seconds=max_duration_seconds,
    )
    start_options, end_options = _remove_unpaired_options(
        start_options,
        end_options,
        allowed_pairs,
    )
    earliest_allowed_start = start_options[0]["start"] if start_options else round(start, 2)
    latest_allowed_end = end_options[-1]["end"] if end_options else round(end, 2)
    current_pair = _nearest_pair(
        allowed_pairs,
        start_options,
        end_options,
        target_start=start,
        target_end=end,
    )
    current_aligned_start_option = _option_for_pair(
        start_options,
        current_pair,
        pair_field="start_option_index",
    ) or _nearest_option(start_options, start, boundary="start")
    current_aligned_end_option = _option_for_pair(
        end_options,
        current_pair,
        pair_field="end_option_index",
    ) or _nearest_option(end_options, end, boundary="end")
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
        allowed_boundary_pairs=[BoundaryOptionPair(**pair) for pair in allowed_pairs],
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


def _filter_boundary_options(
    options: list[dict[str, Any]],
    *,
    field: str,
    minimum: float,
    maximum: float,
) -> list[dict[str, Any]]:
    return [
        option
        for option in options
        if float(minimum) <= float(option[field]) <= float(maximum)
    ]


def _allowed_boundary_pairs(
    start_options: list[dict[str, Any]],
    end_options: list[dict[str, Any]],
    *,
    min_duration_seconds: float,
    max_duration_seconds: float,
) -> list[dict[str, int]]:
    minimum = float(min_duration_seconds)
    maximum = float(max_duration_seconds)
    pairs: list[dict[str, int]] = []
    for start_option in start_options:
        for end_option in end_options:
            duration = float(end_option["end"]) - float(start_option["start"])
            if duration <= 0 or duration < minimum or duration > maximum:
                continue
            pairs.append(
                {
                    "start_option_index": int(start_option["option_index"]),
                    "end_option_index": int(end_option["option_index"]),
                }
            )
    return pairs


def _remove_unpaired_options(
    start_options: list[dict[str, Any]],
    end_options: list[dict[str, Any]],
    allowed_pairs: list[dict[str, int]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    paired_starts = {int(pair["start_option_index"]) for pair in allowed_pairs}
    paired_ends = {int(pair["end_option_index"]) for pair in allowed_pairs}
    return (
        [option for option in start_options if int(option["option_index"]) in paired_starts],
        [option for option in end_options if int(option["option_index"]) in paired_ends],
    )


def _nearest_pair(
    allowed_pairs: list[dict[str, int]],
    start_options: list[dict[str, Any]],
    end_options: list[dict[str, Any]],
    *,
    target_start: float,
    target_end: float,
) -> dict[str, int] | None:
    if not allowed_pairs:
        return None
    starts = {int(option["option_index"]): option for option in start_options}
    ends = {int(option["option_index"]): option for option in end_options}
    return min(
        allowed_pairs,
        key=lambda pair: (
            abs(float(starts[int(pair["start_option_index"])]["start"]) - float(target_start))
            + abs(float(ends[int(pair["end_option_index"])]["end"]) - float(target_end)),
            abs(float(starts[int(pair["start_option_index"])]["start"]) - float(target_start)),
            abs(float(ends[int(pair["end_option_index"])]["end"]) - float(target_end)),
        ),
    )


def _option_for_pair(
    options: list[dict[str, Any]],
    pair: dict[str, int] | None,
    *,
    pair_field: str,
) -> dict[str, Any] | None:
    if pair is None:
        return None
    option_index = int(pair[pair_field])
    return next(
        (option for option in options if int(option["option_index"]) == option_index),
        None,
    )


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
