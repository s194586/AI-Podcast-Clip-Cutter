from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .project_state import DEFAULT_PROJECT_ID, PROJECT_ROOT

WINDOW_MARGIN_SECONDS = 20.0
MIN_EDITED_DURATION_SECONDS = 10.0
MAX_EDITED_DURATION_SECONDS = 90.0


class ClipValidationError(ValueError):
    """Raised when draft clip metadata or edited bounds are invalid."""


def candidate_window_paths(project_root: Path = PROJECT_ROOT) -> list[Path]:
    return [
        project_root / "top_windows.json",
        project_root / "metadata" / "top_windows.json",
        project_root / "metadata" / "cutting_logic.json",
        project_root / "examples" / "top_windows.example.json",
    ]


def _read_json(path: Path) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as file_handle:
            return json.load(file_handle)
    except json.JSONDecodeError as exc:
        raise ClipValidationError(f"Could not parse {path}: {exc}") from exc


def _parse_seconds(value: Any, field_name: str) -> float:
    if value is None or value == "":
        raise ClipValidationError(f"Missing required time field: {field_name}")
    try:
        if isinstance(value, str) and ":" in value:
            parts = [part for part in value.strip().replace(",", ".").split(":") if part]
            if len(parts) == 2:
                seconds = int(parts[0]) * 60 + float(parts[1])
            elif len(parts) == 3:
                seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            else:
                seconds = float(value)
        else:
            seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ClipValidationError(f"Invalid time value for {field_name}: {value}") from exc
    if not math.isfinite(seconds):
        raise ClipValidationError(f"Invalid non-finite time value for {field_name}: {value}")
    return seconds


def _first_present(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _round_seconds(value: float) -> float:
    return round(float(value), 2)


def _optional_seconds(value: Any, field_name: str) -> float | None:
    if value in (None, ""):
        return None
    return _round_seconds(_parse_seconds(value, field_name))


def _relative_source(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _decision_to_window(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "start": _first_present(decision, ("final_start", "start", "heatmap_start")),
        "end": _first_present(decision, ("final_end", "end", "heatmap_end")),
        "duration": _first_present(decision, ("final_duration", "duration")),
        "summary": _first_present(
            decision,
            ("summary_after", "llm_story_summary", "reason", "summary_before", "context_excerpt"),
        ),
        "text": _first_present(decision, ("context_excerpt", "summary_after", "summary_before")),
        "candidate_id": decision.get("candidate_id"),
        "selection_source": decision.get("selection_source"),
        "boundary_metadata": decision.get("boundary_metadata"),
    }


def _adjustment_to_window(adjustment: dict[str, Any]) -> dict[str, Any]:
    source_window = adjustment.get("source_window") if isinstance(adjustment.get("source_window"), dict) else {}
    return {
        "start": _first_present(adjustment, ("final_start", "start")) or source_window.get("start"),
        "end": _first_present(adjustment, ("final_end", "end")) or source_window.get("end"),
        "duration": _first_present(adjustment, ("final_duration", "duration")),
        "summary": source_window.get("summary"),
        "text": source_window.get("text") or source_window.get("summary"),
        "candidate_id": source_window.get("candidate_id"),
        "selection_source": "cutter_adjustment",
        "boundary_metadata": adjustment.get("boundary_metadata"),
    }


def extract_windows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        raise ClipValidationError("Window file must contain a JSON list or object.")

    for key in ("top_windows", "windows", "selected_windows", "selected_clips", "clips"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    decisions = payload.get("decisions")
    if isinstance(decisions, list):
        windows = [_decision_to_window(item) for item in decisions if isinstance(item, dict)]
        windows = [item for item in windows if item.get("start") is not None and item.get("end") is not None]
        if windows:
            return windows

    adjustments = payload.get("cutter_adjustments")
    if isinstance(adjustments, list):
        windows = [_adjustment_to_window(item) for item in adjustments if isinstance(item, dict)]
        return [item for item in windows if item.get("start") is not None and item.get("end") is not None]

    raise ClipValidationError("No clip windows were found in the selected JSON file.")


def _normalize_window(window: dict[str, Any], index: int, source: str) -> dict[str, Any]:
    ai_start = _parse_seconds(_first_present(window, ("ai_start", "start", "final_start")), "start")
    ai_end = _parse_seconds(_first_present(window, ("ai_end", "end", "final_end")), "end")
    if ai_end <= ai_start:
        raise ClipValidationError(f"Clip {index} has end <= start.")

    summary = str(_first_present(window, ("summary", "llm_story_summary", "ai_reason")) or "").strip()
    text = str(_first_present(window, ("text", "context_excerpt", "summary")) or "").strip()
    clip_id = str(window.get("id") or window.get("clip_id") or f"clip_{index:03d}").strip()
    duration = ai_end - ai_start

    return {
        "id": clip_id,
        "index": index,
        "ai_start": _round_seconds(ai_start),
        "ai_end": _round_seconds(ai_end),
        "reviewed_start": _optional_seconds(window.get("reviewed_start"), "reviewed_start"),
        "reviewed_end": _optional_seconds(window.get("reviewed_end"), "reviewed_end"),
        "edited_start": _round_seconds(ai_start),
        "edited_end": _round_seconds(ai_end),
        "boundary_source": str(window.get("boundary_source") or "heuristic"),
        "min_start": _round_seconds(max(0.0, ai_start - WINDOW_MARGIN_SECONDS)),
        "max_start": _round_seconds(ai_start + WINDOW_MARGIN_SECONDS),
        "min_end": _round_seconds(max(0.0, ai_end - WINDOW_MARGIN_SECONDS)),
        "max_end": _round_seconds(ai_end + WINDOW_MARGIN_SECONDS),
        "duration": _round_seconds(duration),
        "summary": summary,
        "text": text,
        "source": source,
        "status": str(window.get("status") or "draft"),
        "candidate_id": window.get("candidate_id"),
        "selection_source": window.get("selection_source"),
        "local_score": window.get("local_score"),
        "local_rank": window.get("local_rank"),
        "selection_reasons": window.get("selection_reasons") or [],
        "local_features": window.get("local_features") or {},
        "render_status": str(window.get("render_status") or "not_rendered"),
        "raw_outputs": list(window.get("raw_outputs") or []),
        "subtitled_outputs": list(window.get("subtitled_outputs") or []),
    }


def _normalize_project_clip(clip: dict[str, Any], index: int) -> dict[str, Any]:
    normalized = dict(clip)
    ai_start = _parse_seconds(_first_present(normalized, ("ai_start", "start")), "ai_start")
    ai_end = _parse_seconds(_first_present(normalized, ("ai_end", "end")), "ai_end")
    if ai_end <= ai_start:
        raise ClipValidationError(f"Clip {index} has ai_end <= ai_start.")

    edited_start = _parse_seconds(
        _first_present(normalized, ("edited_start", "start", "ai_start")),
        "edited_start",
    )
    edited_end = _parse_seconds(
        _first_present(normalized, ("edited_end", "end", "ai_end")),
        "edited_end",
    )
    if edited_end <= edited_start:
        edited_start, edited_end = ai_start, ai_end

    normalized.update(
        {
            "id": str(normalized.get("id") or f"clip_{index:03d}"),
            "index": int(normalized.get("index") or index),
            "ai_start": _round_seconds(ai_start),
            "ai_end": _round_seconds(ai_end),
            "reviewed_start": _optional_seconds(normalized.get("reviewed_start"), "reviewed_start"),
            "reviewed_end": _optional_seconds(normalized.get("reviewed_end"), "reviewed_end"),
            "edited_start": _round_seconds(edited_start),
            "edited_end": _round_seconds(edited_end),
            "boundary_source": str(normalized.get("boundary_source") or "heuristic"),
            "min_start": _round_seconds(float(normalized.get("min_start", max(0.0, ai_start - WINDOW_MARGIN_SECONDS)))),
            "max_start": _round_seconds(float(normalized.get("max_start", ai_start + WINDOW_MARGIN_SECONDS))),
            "min_end": _round_seconds(float(normalized.get("min_end", max(0.0, ai_end - WINDOW_MARGIN_SECONDS)))),
            "max_end": _round_seconds(float(normalized.get("max_end", ai_end + WINDOW_MARGIN_SECONDS))),
            "duration": _round_seconds(edited_end - edited_start),
            "summary": str(normalized.get("summary") or "").strip(),
            "text": str(normalized.get("text") or "").strip(),
            "status": str(normalized.get("status") or "draft"),
            "render_status": str(normalized.get("render_status") or "not_rendered"),
            "raw_outputs": list(normalized.get("raw_outputs") or []),
            "subtitled_outputs": list(normalized.get("subtitled_outputs") or []),
            "selection_reasons": list(normalized.get("selection_reasons") or []),
            "local_features": dict(normalized.get("local_features") or {}),
        }
    )
    return normalized


def _load_clips_from_candidate_files(project_root: Path) -> tuple[list[dict[str, Any]], str]:
    errors: list[str] = []
    for path in candidate_window_paths(project_root):
        if not path.exists():
            continue
        payload = _read_json(path)
        try:
            windows = extract_windows(payload)
            source = _relative_source(path, project_root)
            clips = [_normalize_window(window, index, source) for index, window in enumerate(windows, start=1)]
            clips = [clip for clip in clips if clip["duration"] > 0]
            if clips:
                return clips, source
            errors.append(f"{source}: no valid clips")
        except ClipValidationError as exc:
            errors.append(f"{_relative_source(path, project_root)}: {exc}")

    detail = "; ".join(errors) if errors else "No top_windows.json or example clip file was found."
    raise ClipValidationError(detail)


def load_clips(
    project_root: Path = PROJECT_ROOT,
    project_id: str = DEFAULT_PROJECT_ID,
) -> list[dict[str, Any]]:
    from .clip_service import load_clips as load_persisted_clips

    return load_persisted_clips(project_id=project_id, project_root=project_root)


def find_clip(clips: list[dict[str, Any]], clip_id: str) -> dict[str, Any]:
    for clip in clips:
        if clip["id"] == clip_id:
            return clip
    raise ClipValidationError(f"Unknown clip_id: {clip_id}")


def validate_adjusted_bounds(clip: dict[str, Any], start: Any, end: Any) -> tuple[float, float, float]:
    edited_start = _parse_seconds(start, "start")
    edited_end = _parse_seconds(end, "end")
    errors: list[str] = []

    if edited_start < float(clip["min_start"]) or edited_start > float(clip["max_start"]):
        errors.append(
            f"Start must stay within +/-{int(WINDOW_MARGIN_SECONDS)}s of AI start "
            f"({clip['min_start']}s to {clip['max_start']}s)."
        )
    if edited_end < float(clip["min_end"]) or edited_end > float(clip["max_end"]):
        errors.append(
            f"End must stay within +/-{int(WINDOW_MARGIN_SECONDS)}s of AI end "
            f"({clip['min_end']}s to {clip['max_end']}s)."
        )
    if edited_end <= edited_start:
        errors.append("End must be greater than start.")

    duration = edited_end - edited_start
    if duration < MIN_EDITED_DURATION_SECONDS:
        errors.append(f"Adjusted duration must be at least {int(MIN_EDITED_DURATION_SECONDS)} seconds.")
    if duration > MAX_EDITED_DURATION_SECONDS:
        errors.append(f"Adjusted duration must not exceed {int(MAX_EDITED_DURATION_SECONDS)} seconds.")
    if errors:
        raise ClipValidationError(" ".join(errors))

    return _round_seconds(edited_start), _round_seconds(edited_end), _round_seconds(duration)


def update_clip_bounds(
    clip_id: str,
    start: Any,
    end: Any,
    *,
    project_root: Path = PROJECT_ROOT,
    project_id: str = DEFAULT_PROJECT_ID,
) -> dict[str, Any]:
    from .clip_service import update_bounds

    return update_bounds(clip_id, start, end, project_id=project_id, project_root=project_root)


def set_clip_status(
    clip_id: str,
    status: str,
    *,
    project_root: Path = PROJECT_ROOT,
    project_id: str = DEFAULT_PROJECT_ID,
) -> dict[str, Any]:
    from .clip_service import set_status

    return set_status(clip_id, status, project_id=project_id, project_root=project_root)


def record_render_result(
    clip_id: str,
    render_result: dict[str, Any],
    *,
    project_root: Path = PROJECT_ROOT,
    project_id: str = DEFAULT_PROJECT_ID,
) -> dict[str, Any]:
    from .clip_service import record_render_result as record_persisted_render_result

    return record_persisted_render_result(clip_id, render_result, project_id=project_id, project_root=project_root)
