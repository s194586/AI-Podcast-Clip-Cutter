from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .schemas import BoundaryOption, BoundaryOptionPair, ClipTranscriptContext, TranscriptSegment
from .transcript_segments import normalize_transcript_segments
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
    candidate_id: str | None = None,
    allowed_start_min: float | None = None,
    allowed_start_max: float | None = None,
    allowed_end_min: float | None = None,
    allowed_end_max: float | None = None,
    min_duration_seconds: float = DEFAULT_MIN_REVIEW_DURATION_SECONDS,
    max_duration_seconds: float = DEFAULT_MAX_REVIEW_DURATION_SECONDS,
) -> dict[str, Any]:
    """Build the internal transcript context used by the boundary reviewer."""

    segments = _with_canonical_ids(load_transcript_segments(transcript_path))
    return build_clip_transcript_context_from_segments(
        segments,
        clip_start,
        clip_end,
        context_seconds=context_seconds,
        clip_id=clip_id,
        candidate_id=candidate_id,
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
    candidate_id: str | None = None,
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

    normalized = _with_canonical_ids(segments)
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
        candidate_id=candidate_id,
        candidate_start=round(start, 2),
        candidate_end=round(end, 2),
        minimum_duration_seconds=round(float(min_duration_seconds), 2),
        maximum_duration_seconds=round(float(max_duration_seconds), 2),
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
    """Return the canonical transcript segments in a review context by ID.

    Boundary options are derived compatibility data.  They must never become a
    competing source of timestamps or segment identity.
    """

    mapped: dict[str, dict[str, Any]] = {}
    for key in ("context_before", "candidate_segments", "context_after"):
        items = context.get(key, [])
        if items is None:
            items = []
        if not isinstance(items, list):
            raise ValueError(f"{key} must be a list of canonical transcript segments.")
        for position, segment in enumerate(items):
            if not isinstance(segment, Mapping):
                raise ValueError(f"{key}[{position}] must be a canonical transcript segment.")
            segment_id = _required_context_segment_id(
                segment.get("segment_id"), location=f"{key}[{position}]"
            )
            _context_timestamp(segment.get("start"), location=f"{key}[{position}].start")
            _context_timestamp(segment.get("end"), location=f"{key}[{position}].end")
            if segment_id in mapped:
                raise ValueError(f"Duplicate segment_id in review context: {segment_id}")
            mapped[segment_id] = dict(segment)
    return mapped


def boundary_options_by_segment_id(
    context: dict[str, Any],
    *,
    option_list_name: str,
    canonical_segments: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Validate boundary-option compatibility data against canonical segments."""

    canonical_segments = canonical_segments if canonical_segments is not None else segment_map(context)
    options = context.get(option_list_name, [])
    if options is None:
        options = []
    if not isinstance(options, list):
        raise ValueError(f"{option_list_name} must be a list of boundary options.")

    by_id: dict[str, dict[str, Any]] = {}
    indexes: set[int] = set()
    for position, option in enumerate(options):
        location = f"{option_list_name}[{position}]"
        if not isinstance(option, Mapping):
            raise ValueError(f"{location} must be a boundary option.")
        segment_id = _required_context_segment_id(option.get("segment_id"), location=location)
        if segment_id in by_id:
            raise ValueError(f"Duplicate segment_id in {option_list_name}: {segment_id}")
        canonical = canonical_segments.get(segment_id)
        if canonical is None:
            raise ValueError(
                f"{option_list_name} references segment_id not present in canonical transcript segments: {segment_id}"
            )
        option_index = option.get("option_index")
        if type(option_index) is not int or option_index <= 0:
            raise ValueError(f"{location}.option_index must be a positive integer.")
        if option_index in indexes:
            raise ValueError(f"Duplicate option_index in {option_list_name}: {option_index}")
        indexes.add(option_index)
        for field in ("start", "end"):
            option_value = _context_timestamp(option.get(field), location=f"{location}.{field}")
            canonical_value = _context_timestamp(
                canonical.get(field), location=f"canonical segment {segment_id}.{field}"
            )
            if option_value != canonical_value:
                raise ValueError(
                    f"{location}.{field} does not match canonical transcript segment {segment_id}.{field}."
                )
        if option.get("text") != canonical.get("text"):
            raise ValueError(f"{location}.text does not match canonical transcript segment {segment_id}.text.")
        by_id[segment_id] = dict(option)
    return by_id


def allowed_boundary_pair_indexes(
    context: dict[str, Any],
    *,
    start_options: dict[str, dict[str, Any]],
    end_options: dict[str, dict[str, Any]],
) -> set[tuple[int, int]]:
    """Validate backend-only allowed pairs without silently discarding bad data."""

    pairs = context.get("allowed_boundary_pairs", [])
    if pairs is None:
        pairs = []
    if not isinstance(pairs, list):
        raise ValueError("allowed_boundary_pairs must be a list of boundary option pairs.")
    start_indexes = {option["option_index"] for option in start_options.values()}
    end_indexes = {option["option_index"] for option in end_options.values()}
    indexes: set[tuple[int, int]] = set()
    for position, pair in enumerate(pairs):
        location = f"allowed_boundary_pairs[{position}]"
        if not isinstance(pair, Mapping):
            raise ValueError(f"{location} must be a boundary option pair.")
        start_index = _required_option_index(pair.get("start_option_index"), location=location)
        end_index = _required_option_index(pair.get("end_option_index"), location=location)
        if start_index not in start_indexes or end_index not in end_indexes:
            raise ValueError(f"{location} references an unknown boundary option index.")
        selected_pair = (start_index, end_index)
        if selected_pair in indexes:
            raise ValueError(f"Duplicate allowed boundary option pair: {selected_pair}")
        indexes.add(selected_pair)
    return indexes


def current_aligned_boundary_options(
    context: dict[str, Any],
    *,
    start_options: dict[str, dict[str, Any]],
    end_options: dict[str, dict[str, Any]],
    allowed_pairs: set[tuple[int, int]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Resolve current alignment only when its IDs and compatibility indexes agree."""

    start = _current_aligned_boundary_option(
        context, boundary="start", options=start_options
    )
    end = _current_aligned_boundary_option(context, boundary="end", options=end_options)
    if (start is None) != (end is None):
        raise ValueError("Current aligned start and end boundaries must both be present or both be null.")
    if start is None and allowed_pairs:
        raise ValueError("Current aligned boundaries are required when allowed_boundary_pairs are present.")
    if start is not None and (start["option_index"], end["option_index"]) not in allowed_pairs:
        raise ValueError("Current aligned boundary pair is not present in allowed_boundary_pairs.")
    return start, end


def _current_aligned_boundary_option(
    context: dict[str, Any],
    *,
    boundary: str,
    options: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    segment_id_value = context.get(f"current_aligned_{boundary}_segment_id")
    option_index_value = context.get(f"current_aligned_{boundary}_option_index")
    if segment_id_value is None and option_index_value is None:
        return None
    if segment_id_value is None or option_index_value is None:
        raise ValueError(
            f"current_aligned_{boundary}_segment_id and current_aligned_{boundary}_option_index must agree."
        )
    segment_id = _required_context_segment_id(
        segment_id_value, location=f"current_aligned_{boundary}_segment_id"
    )
    option_index = _required_option_index(
        option_index_value, location=f"current_aligned_{boundary}_option_index"
    )
    option = options.get(segment_id)
    if option is None:
        raise ValueError(f"current_aligned_{boundary}_segment_id is not an eligible boundary segment: {segment_id}")
    if option["option_index"] != option_index:
        raise ValueError(
            f"current_aligned_{boundary}_option_index does not match current_aligned_{boundary}_segment_id."
        )
    return option


def _required_context_segment_id(value: Any, *, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}.segment_id must be a non-empty string.")
    return value


def _required_option_index(value: Any, *, location: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{location} must be a positive integer.")
    return value


def _context_timestamp(value: Any, *, location: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{location} must be a finite timestamp.")
    try:
        timestamp = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{location} must be a finite timestamp.") from exc
    if not math.isfinite(timestamp):
        raise ValueError(f"{location} must be a finite timestamp.")
    return timestamp


def _with_canonical_ids(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "segment_id": str(segment["segment_id"]),
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "text": str(segment.get("text") or ""),
            "speaker": _optional_speaker(segment),
        }
        for segment in normalize_transcript_segments(segments)
    ]


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
            raise ValueError(f"Duplicate segment_id in boundary options: {segment_id}")
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
