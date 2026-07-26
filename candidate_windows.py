"""Deterministic, semantically neutral windows around replay-interest peaks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any


CANDIDATE_SCHEMA_VERSION = 1
PEAK_SCHEMA_VERSION = 1
SOURCE = "youtube_most_replayed"
GENERATOR = "peak_centered_candidate_windows"
GENERATOR_VERSION = 1
_WINDOW_TOLERANCE_SECONDS = 1e-6


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number.")
    return result


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return value


@dataclass(frozen=True)
class CandidateWindowConfig:
    """Explicit MVP heuristics for technical candidate-window generation."""

    min_duration_seconds: float = 30.0
    target_duration_seconds: float = 60.0
    max_duration_seconds: float = 90.0
    max_overlap_ratio: float = 0.65
    max_candidates: int = 12

    def __post_init__(self) -> None:
        minimum = _finite_number(self.min_duration_seconds, "min_duration_seconds")
        target = _finite_number(self.target_duration_seconds, "target_duration_seconds")
        maximum = _finite_number(self.max_duration_seconds, "max_duration_seconds")
        overlap = _finite_number(self.max_overlap_ratio, "max_overlap_ratio")
        max_candidates = _positive_integer(self.max_candidates, "max_candidates")

        if minimum < 30.0:
            raise ValueError("min_duration_seconds must be at least 30.0.")
        if minimum > target:
            raise ValueError("min_duration_seconds must not exceed target_duration_seconds.")
        if target > maximum:
            raise ValueError("target_duration_seconds must not exceed max_duration_seconds.")
        if maximum > 90.0:
            raise ValueError("max_duration_seconds must not exceed 90.0.")
        if not 0.0 <= overlap <= 1.0:
            raise ValueError("max_overlap_ratio must be between zero and one.")

        object.__setattr__(self, "min_duration_seconds", minimum)
        object.__setattr__(self, "target_duration_seconds", target)
        object.__setattr__(self, "max_duration_seconds", maximum)
        object.__setattr__(self, "max_overlap_ratio", overlap)
        object.__setattr__(self, "max_candidates", max_candidates)


def validate_peak_document(payload: Any) -> dict[str, Any]:
    """Validate a versioned document produced by ``detect_heatmap_peaks``."""
    if not isinstance(payload, dict):
        raise ValueError("Peak document must be an object.")
    schema_version = payload.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != PEAK_SCHEMA_VERSION
    ):
        raise ValueError("Unsupported or missing peak document schema_version.")
    if payload.get("source") != SOURCE:
        raise ValueError("Peak document source must be youtube_most_replayed.")
    algorithm = payload.get("algorithm")
    if not isinstance(algorithm, str) or not algorithm.strip():
        raise ValueError("Peak document must contain an algorithm.")
    _positive_integer(payload.get("algorithm_version"), "algorithm_version")
    video_id = payload.get("video_id")
    if not isinstance(video_id, str) or not video_id.strip():
        raise ValueError("Peak document must contain a non-empty video_id.")
    duration = _finite_number(payload.get("duration_seconds"), "duration_seconds")
    if duration <= 0.0:
        raise ValueError("duration_seconds must be positive.")
    raw_peaks = payload.get("peaks")
    if not isinstance(raw_peaks, list):
        raise ValueError("peaks must be a list.")

    peaks: list[dict[str, float | int]] = []
    ranks: set[int] = set()
    for index, raw_peak in enumerate(raw_peaks):
        if not isinstance(raw_peak, dict):
            raise ValueError(f"Peak {index} must be an object.")
        rank = _positive_integer(raw_peak.get("rank"), f"Peak {index} rank")
        if rank in ranks:
            raise ValueError("Peak ranks must be unique.")
        ranks.add(rank)
        try:
            peak_time = _finite_number(raw_peak["peak_time"], f"Peak {index} peak_time")
            start_time = _finite_number(raw_peak["start_time"], f"Peak {index} start_time")
            end_time = _finite_number(raw_peak["end_time"], f"Peak {index} end_time")
            raw_value = _finite_number(raw_peak["raw_value"], f"Peak {index} raw_value")
            smoothed_value = _finite_number(
                raw_peak["smoothed_value"], f"Peak {index} smoothed_value"
            )
            prominence = _finite_number(raw_peak["prominence"], f"Peak {index} prominence")
        except KeyError as exc:
            raise ValueError(f"Peak {index} is missing {exc.args[0]}.") from exc
        if not (0.0 <= start_time < end_time <= duration):
            raise ValueError(f"Peak {index} interval is outside the video duration.")
        if not start_time <= peak_time <= end_time:
            raise ValueError(f"Peak {index} peak_time is outside its interval.")
        if not all(0.0 <= value <= 1.0 for value in (raw_value, smoothed_value, prominence)):
            raise ValueError(f"Peak {index} replay-interest values must be between zero and one.")
        peaks.append(
            {
                "rank": rank,
                "peak_time": peak_time,
                "start_time": start_time,
                "end_time": end_time,
                "raw_value": raw_value,
                "smoothed_value": smoothed_value,
                "prominence": prominence,
            }
        )

    return {
        "video_id": video_id,
        "duration_seconds": duration,
        "peaks": peaks,
    }


def _window_for_peak(peak_time: float, duration: float, target: float) -> tuple[float, float]:
    length = min(target, duration)
    start = peak_time - length / 2.0
    start = min(max(0.0, start), duration - length)
    return start, start + length


def _same_window(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        abs(left["start_time"] - right["start_time"]) <= _WINDOW_TOLERANCE_SECONDS
        and abs(left["end_time"] - right["end_time"]) <= _WINDOW_TOLERANCE_SECONDS
    )


def _overlap_ratio(candidate: dict[str, Any], accepted: dict[str, Any]) -> float:
    intersection = max(
        0.0,
        min(candidate["end_time"], accepted["end_time"])
        - max(candidate["start_time"], accepted["start_time"]),
    )
    return intersection / min(
        candidate["duration_seconds"], accepted["duration_seconds"]
    )


def generate_candidate_windows(
    peak_document: Any, config: CandidateWindowConfig = CandidateWindowConfig()
) -> dict[str, Any]:
    """Generate technical candidate windows without making content-quality claims."""
    if not isinstance(config, CandidateWindowConfig):
        raise TypeError("config must be a CandidateWindowConfig instance.")

    document = validate_peak_document(peak_document)
    result: dict[str, Any] = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "source": SOURCE,
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "video_id": document["video_id"],
        "duration_seconds": document["duration_seconds"],
        "parameters": asdict(config),
        "candidates": [],
    }
    if document["duration_seconds"] < config.min_duration_seconds:
        return result

    peaks = sorted(
        document["peaks"],
        key=lambda peak: (
            peak["rank"],
            -peak["prominence"],
            -peak["smoothed_value"],
            -peak["raw_value"],
            peak["peak_time"],
        ),
    )
    unique: list[dict[str, Any]] = []
    for peak in peaks:
        start, end = _window_for_peak(
            peak["peak_time"], document["duration_seconds"], config.target_duration_seconds
        )
        candidate = {
            "source_peak_rank": peak["rank"],
            "peak_time": peak["peak_time"],
            "start_time": start,
            "end_time": end,
            "duration_seconds": end - start,
            "replay_interest": {
                "raw_value": peak["raw_value"],
                "smoothed_value": peak["smoothed_value"],
                "prominence": peak["prominence"],
            },
        }
        if not any(_same_window(candidate, prior) for prior in unique):
            unique.append(candidate)

    accepted: list[dict[str, Any]] = []
    for candidate in unique:
        if all(
            _overlap_ratio(candidate, prior) <= config.max_overlap_ratio
            for prior in accepted
        ):
            accepted.append(candidate)
            if len(accepted) == config.max_candidates:
                break

    result["candidates"] = [
        {"rank": rank, **candidate} for rank, candidate in enumerate(accepted, start=1)
    ]
    return result
