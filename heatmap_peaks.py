"""Deterministic peak detection for trusted YouTube Most Replayed heatmaps."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

from heatmap_contract import validate_heatmap_document


PEAK_SCHEMA_VERSION = 1
ALGORITHM = "time_weighted_local_prominence"
ALGORITHM_VERSION = 2
_PLATEAU_TOLERANCE = 1e-9


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number.")
    return result


@dataclass(frozen=True)
class PeakDetectorConfig:
    """Explicit MVP heuristics for technical heatmap peak detection."""

    smoothing_radius_seconds: float = 5.0
    prominence_window_seconds: float = 30.0
    min_prominence: float = 0.05
    min_distance_seconds: float = 30.0
    max_peaks: int = 20

    def __post_init__(self) -> None:
        smoothing_radius = _finite_number(
            self.smoothing_radius_seconds, "smoothing_radius_seconds"
        )
        prominence_window = _finite_number(
            self.prominence_window_seconds, "prominence_window_seconds"
        )
        min_prominence = _finite_number(self.min_prominence, "min_prominence")
        min_distance = _finite_number(
            self.min_distance_seconds, "min_distance_seconds"
        )
        if smoothing_radius < 0:
            raise ValueError("smoothing_radius_seconds must be at least zero.")
        if prominence_window < 0:
            raise ValueError("prominence_window_seconds must be at least zero.")
        if not 0 <= min_prominence <= 1:
            raise ValueError("min_prominence must be between zero and one.")
        if min_distance < 0:
            raise ValueError("min_distance_seconds must be at least zero.")
        if isinstance(self.max_peaks, bool) or not isinstance(self.max_peaks, int):
            raise ValueError("max_peaks must be a positive integer.")
        if self.max_peaks <= 0:
            raise ValueError("max_peaks must be a positive integer.")

        object.__setattr__(self, "smoothing_radius_seconds", smoothing_radius)
        object.__setattr__(self, "prominence_window_seconds", prominence_window)
        object.__setattr__(self, "min_prominence", min_prominence)
        object.__setattr__(self, "min_distance_seconds", min_distance)


def _intersection_length(
    start: float, end: float, window_start: float, window_end: float
) -> float:
    return max(0.0, min(end, window_end) - max(start, window_start))


def _smoothed_values(
    points: list[dict[str, float]], midpoints: list[float], radius: float
) -> list[float]:
    if radius == 0:
        return [point["value"] for point in points]

    smoothed: list[float] = []
    for midpoint in midpoints:
        window_start = midpoint - radius
        window_end = midpoint + radius
        weighted_sum = 0.0
        total_weight = 0.0
        for point in points:
            weight = _intersection_length(
                point["start_time"], point["end_time"], window_start, window_end
            )
            weighted_sum += point["value"] * weight
            total_weight += weight
        # The interval containing its midpoint always supplies positive coverage.
        smoothed.append(weighted_sum / total_weight)
    return smoothed


def _same_level(left: float, right: float) -> bool:
    return abs(left - right) <= _PLATEAU_TOLERANCE


def _local_peak_groups(values: list[float]) -> list[tuple[int, int]]:
    """Return inclusive plateau bounds that are strict local maxima."""
    groups: list[tuple[int, int]] = []
    index = 0
    while index < len(values):
        end = index
        while end + 1 < len(values) and _same_level(values[index], values[end + 1]):
            end += 1
        if (
            index > 0
            and end < len(values) - 1
            and values[index] > values[index - 1] + _PLATEAU_TOLERANCE
            and values[end] > values[end + 1] + _PLATEAU_TOLERANCE
        ):
            groups.append((index, end))
        index = end + 1
    return groups


def _plateau_representative(
    start: int, end: int, midpoints: list[float]
) -> int:
    centre = (midpoints[start] + midpoints[end]) / 2.0
    return min(range(start, end + 1), key=lambda index: (abs(midpoints[index] - centre), index))


def _local_prominence(
    values: list[float],
    midpoints: list[float],
    plateau_start: int,
    plateau_end: int,
    peak_time: float,
    window_seconds: float,
) -> float | None:
    left_values = [
        values[index]
        for index in range(plateau_start)
        if midpoints[index] >= peak_time - window_seconds
    ]
    right_values = [
        values[index]
        for index in range(plateau_end + 1, len(values))
        if midpoints[index] <= peak_time + window_seconds
    ]
    # Sparse source heatmaps can have adjacent midpoint gaps larger than the
    # configured window.  Keep in-window samples as the baseline, but retain
    # the nearest point beyond an empty side so an internal local maximum is
    # still comparable to both of its neighbours.
    if not left_values and plateau_start > 0:
        left_values = [values[plateau_start - 1]]
    if not right_values and plateau_end + 1 < len(values):
        right_values = [values[plateau_end + 1]]
    if not left_values or not right_values:
        return None
    prominence = values[plateau_start] - max(min(left_values), min(right_values))
    return min(1.0, max(0.0, prominence))


def detect_heatmap_peaks(
    heatmap_document: Any, config: PeakDetectorConfig = PeakDetectorConfig()
) -> dict[str, Any]:
    """Detect locally elevated replay-interest peaks from a trusted heatmap document."""
    if not isinstance(config, PeakDetectorConfig):
        raise TypeError("config must be a PeakDetectorConfig instance.")

    document = validate_heatmap_document(heatmap_document)
    points = document["points"]
    result: dict[str, Any] = {
        "schema_version": PEAK_SCHEMA_VERSION,
        "source": document["source"],
        "algorithm": ALGORITHM,
        "algorithm_version": ALGORITHM_VERSION,
        "video_id": document["video_id"],
        "duration_seconds": document["duration_seconds"],
        "parameters": asdict(config),
        "peaks": [],
    }
    if len(points) < 3:
        return result

    midpoints = [
        (point["start_time"] + point["end_time"]) / 2.0 for point in points
    ]
    smoothed = _smoothed_values(points, midpoints, config.smoothing_radius_seconds)
    candidates: list[dict[str, float]] = []
    for plateau_start, plateau_end in _local_peak_groups(smoothed):
        index = _plateau_representative(plateau_start, plateau_end, midpoints)
        prominence = _local_prominence(
            smoothed,
            midpoints,
            plateau_start,
            plateau_end,
            midpoints[index],
            config.prominence_window_seconds,
        )
        if prominence is None or prominence < config.min_prominence:
            continue
        point = points[index]
        candidates.append(
            {
                "peak_time": midpoints[index],
                "start_time": point["start_time"],
                "end_time": point["end_time"],
                "raw_value": point["value"],
                "smoothed_value": smoothed[index],
                "prominence": prominence,
            }
        )

    candidates.sort(
        key=lambda peak: (
            -peak["prominence"],
            -peak["smoothed_value"],
            -peak["raw_value"],
            peak["peak_time"],
        )
    )
    accepted: list[dict[str, float]] = []
    for candidate in candidates:
        if all(
            abs(candidate["peak_time"] - prior["peak_time"])
            >= config.min_distance_seconds
            for prior in accepted
        ):
            accepted.append(candidate)
            if len(accepted) == config.max_peaks:
                break

    result["peaks"] = [
        {"rank": rank, **peak} for rank, peak in enumerate(accepted, start=1)
    ]
    return result
