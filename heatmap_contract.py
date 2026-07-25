from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any


SCHEMA_VERSION = 1
SOURCE = "youtube_most_replayed"
END_TIME_TOLERANCE_SECONDS = 1.0


class HeatmapUnavailableError(RuntimeError):
    """The current video has no valid, trusted YouTube Most Replayed data."""


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HeatmapUnavailableError(f"Heatmap point {field} must be a number.")
    result = float(value)
    if not math.isfinite(result):
        raise HeatmapUnavailableError(f"Heatmap point {field} must be finite.")
    return result


def validate_points(points: Any, duration_seconds: Any) -> list[dict[str, float]]:
    duration = _finite_number(duration_seconds, "duration_seconds")
    if duration <= 0:
        raise HeatmapUnavailableError("Video duration must be a positive finite number.")
    if not isinstance(points, list) or not points:
        raise HeatmapUnavailableError("YouTube Most Replayed heatmap is unavailable or empty.")

    validated: list[dict[str, float]] = []
    previous_start = -1.0
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise HeatmapUnavailableError(f"Heatmap point {index} must be an object.")
        try:
            start = _finite_number(point["start_time"], "start_time")
            end = _finite_number(point["end_time"], "end_time")
            value = _finite_number(point["value"], "value")
        except KeyError as exc:
            raise HeatmapUnavailableError(
                f"Heatmap point {index} is missing {exc.args[0]}."
            ) from exc

        if start < 0:
            raise HeatmapUnavailableError(f"Heatmap point {index} starts before zero.")
        if end <= start:
            raise HeatmapUnavailableError(
                f"Heatmap point {index} must end after it starts."
            )
        if not 0 <= value <= 1:
            raise HeatmapUnavailableError(
                f"Heatmap point {index} value must be between 0 and 1."
            )
        if end > duration + END_TIME_TOLERANCE_SECONDS:
            raise HeatmapUnavailableError(
                f"Heatmap point {index} exceeds the video duration."
            )
        if start < previous_start:
            raise HeatmapUnavailableError("Heatmap points must be ordered by start_time.")
        previous_start = start
        validated.append({"start_time": start, "end_time": end, "value": value})
    return validated


def build_youtube_heatmap(
    info: Any,
    *,
    extractor_version: str,
) -> dict[str, Any]:
    video_id = validate_youtube_metadata_identity(info)
    duration = _finite_number(info.get("duration"), "duration_seconds")
    points = validate_points(info.get("heatmap"), duration)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "synthetic": False,
        "video_id": video_id,
        "extractor": "youtube",
        "extractor_version": str(extractor_version),
        "duration_seconds": duration,
        "points": points,
    }


def validate_youtube_metadata_identity(info: Any) -> str:
    if not isinstance(info, dict):
        raise HeatmapUnavailableError("yt-dlp returned invalid video metadata.")
    video_id = info.get("id")
    if not isinstance(video_id, str) or not video_id.strip():
        raise HeatmapUnavailableError("yt-dlp metadata does not contain a video_id.")
    if info.get("extractor") != "youtube":
        raise HeatmapUnavailableError(
            "YouTube Most Replayed data requires the youtube extractor."
        )
    return video_id


def validate_heatmap_document(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HeatmapUnavailableError(
            "Heatmap file is not a trusted provenance-bearing document."
        )
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise HeatmapUnavailableError("Unsupported or missing heatmap schema_version.")
    if payload.get("source") != SOURCE or payload.get("synthetic") is not False:
        raise HeatmapUnavailableError("Heatmap provenance is not trusted.")
    video_id = payload.get("video_id")
    if not isinstance(video_id, str) or not video_id.strip():
        raise HeatmapUnavailableError("Heatmap document is missing video_id.")
    if payload.get("extractor") != "youtube":
        raise HeatmapUnavailableError("Heatmap document has an invalid extractor.")
    extractor_version = payload.get("extractor_version")
    if not isinstance(extractor_version, str) or not extractor_version.strip():
        raise HeatmapUnavailableError(
            "Heatmap document is missing a valid extractor_version."
        )

    duration = _finite_number(payload.get("duration_seconds"), "duration_seconds")
    points = validate_points(payload.get("points"), duration)
    result = dict(payload)
    result["duration_seconds"] = duration
    result["points"] = points
    return result


def load_heatmap_document(path: str | Path) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as file_handle:
            payload = json.load(file_handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HeatmapUnavailableError(
            "Heatmap file is missing, unreadable, or contains invalid JSON."
        ) from exc
    return validate_heatmap_document(payload)


def load_heatmap_points(path: str | Path) -> list[dict[str, float]]:
    return load_heatmap_document(path)["points"]


def atomic_write_json(path: str | Path, payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as file_handle:
            temporary_path = file_handle.name
            json.dump(payload, file_handle, ensure_ascii=False, indent=2, allow_nan=False)
            file_handle.write("\n")
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
