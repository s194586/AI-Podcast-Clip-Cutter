#!/usr/bin/env python3
"""Read-only inspection of Sprint 1 workspace artifacts.

This command intentionally does not run pipeline stages, network clients, media
tools, renderers, or database code.  It reads only the five Sprint 1 artifact
files and reports their contract status.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from candidate_windows import (  # noqa: E402
    CANDIDATE_ID_SCHEME,
    CANDIDATE_ID_VERSION,
    CANDIDATE_SCHEMA_VERSION,
    CandidateWindowConfig,
    GENERATOR,
    GENERATOR_VERSION,
    SOURCE as CANDIDATE_SOURCE,
    build_candidate_id,
    validate_peak_document,
)
from heatmap_contract import HeatmapUnavailableError, load_heatmap_document  # noqa: E402
from heatmap_peaks import (  # noqa: E402
    ALGORITHM,
    ALGORITHM_VERSION,
    PEAK_SCHEMA_VERSION,
    PeakDetectorConfig,
)
from source_media_contract import SourceMediaManifestError, load_source_media_manifest  # noqa: E402


SEMANTIC_SCORING_FIELDS = frozenset(
    {
        "local_score",
        "local_rank",
        "local_features",
        "selection_reasons",
        "reason",
        "summary",
        "text",
        "title",
        "viral_score",
        "semantic_score",
        "confidence",
        "hook_score",
        "emotion_score",
        "payoff_score",
    }
)
_TIME_TOLERANCE = 1e-6
ADAPTER_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "source",
        "canonical_artifact",
        "candidate_id_scheme",
        "candidate_id_version",
        "top_windows",
    }
)
ADAPTER_WINDOW_FIELDS = frozenset(
    {
        "id",
        "candidate_id",
        "rank",
        "source_peak_rank",
        "peak_time",
        "start",
        "end",
        "duration",
        "boundary_source",
        "selection_source",
        "replay_interest",
    }
)


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number.")
    return result


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _record(report: dict[str, Any], artifact: str, status: str, **details: Any) -> None:
    report["artifacts"][artifact] = {"status": status, **details}


def _safe_error(exc: Exception) -> str:
    """Keep errors useful without echoing payloads, URLs, or full paths."""
    if isinstance(exc, UnicodeDecodeError):
        return "Artifact is not valid UTF-8."
    if isinstance(exc, json.JSONDecodeError):
        return f"Artifact contains invalid JSON at line {exc.lineno}, column {exc.colno}."
    if isinstance(exc, OSError):
        return "Artifact could not be read."
    return str(exc).replace("\n", " ")[:300] or exc.__class__.__name__


def _validated_parameters(parameters: Any, config_type: type[Any], artifact: str) -> dict[str, Any]:
    """Validate an exact producer parameter projection through its config type."""
    if not isinstance(parameters, dict):
        raise ValueError(f"{artifact} parameters must be an object.")
    expected_fields = set(config_type().__dict__)
    actual_fields = set(parameters)
    if actual_fields != expected_fields:
        missing = sorted(expected_fields - actual_fields)
        unexpected = sorted(actual_fields - expected_fields)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected {', '.join(unexpected)}")
        raise ValueError(f"{artifact} parameters must match the producer contract ({'; '.join(details)}).")
    try:
        config_type(**parameters)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{artifact} parameters are invalid: {exc}") from exc
    return parameters


def _candidate_document(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Candidate document must be an object.")
    if payload.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
        raise ValueError("Unsupported or missing candidate schema_version.")
    if payload.get("source") != CANDIDATE_SOURCE:
        raise ValueError("Candidate document source must be youtube_most_replayed.")
    if payload.get("generator") != GENERATOR:
        raise ValueError("Candidate document generator is invalid.")
    if payload.get("generator_version") != GENERATOR_VERSION:
        raise ValueError("Candidate document generator_version is invalid.")
    if payload.get("candidate_id_scheme") != CANDIDATE_ID_SCHEME:
        raise ValueError("Candidate document candidate_id_scheme is invalid.")
    if payload.get("candidate_id_version") != CANDIDATE_ID_VERSION:
        raise ValueError("Candidate document candidate_id_version is invalid.")
    video_id = payload.get("video_id")
    if not isinstance(video_id, str) or not video_id.strip():
        raise ValueError("Candidate document must contain a non-empty video_id.")
    duration = _finite_number(payload.get("duration_seconds"), "duration_seconds")
    if duration <= 0:
        raise ValueError("Candidate document duration_seconds must be positive.")
    _validated_parameters(payload.get("parameters"), CandidateWindowConfig, "Candidate document")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("Candidate document candidates must be a list.")

    candidate_ids: list[str] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError(f"Candidate {index} must be an object.")
        prohibited = sorted(SEMANTIC_SCORING_FIELDS.intersection(candidate))
        if prohibited:
            raise ValueError(
                f"Candidate {index} contains semantic scoring field(s): {', '.join(prohibited)}."
            )
        for field in ("rank", "source_peak_rank", "candidate_id"):
            if field not in candidate:
                raise ValueError(f"Candidate {index} is missing {field}.")
        if (
            isinstance(candidate["rank"], bool)
            or not isinstance(candidate["rank"], int)
            or candidate["rank"] <= 0
        ):
            raise ValueError(f"Candidate {index} rank must be a positive integer.")
        if (
            isinstance(candidate["source_peak_rank"], bool)
            or not isinstance(candidate["source_peak_rank"], int)
            or candidate["source_peak_rank"] <= 0
        ):
            raise ValueError(f"Candidate {index} source_peak_rank must be a positive integer.")
        candidate_id = candidate["candidate_id"]
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError(f"Candidate {index} candidate_id must be a non-empty string.")
        try:
            peak_time = _finite_number(candidate["peak_time"], f"Candidate {index} peak_time")
            start = _finite_number(candidate["start_time"], f"Candidate {index} start_time")
            end = _finite_number(candidate["end_time"], f"Candidate {index} end_time")
            candidate_duration = _finite_number(
                candidate["duration_seconds"], f"Candidate {index} duration_seconds"
            )
        except KeyError as exc:
            raise ValueError(f"Candidate {index} is missing {exc.args[0]}.") from exc
        if not 0 <= start < end <= duration:
            raise ValueError(f"Candidate {index} window is outside the video duration.")
        if not start <= peak_time <= end:
            raise ValueError(f"Candidate {index} peak_time is outside its window.")
        if abs(candidate_duration - (end - start)) > _TIME_TOLERANCE:
            raise ValueError(f"Candidate {index} duration_seconds does not match its window.")
        replay_interest = candidate.get("replay_interest")
        if not isinstance(replay_interest, dict):
            raise ValueError(f"Candidate {index} replay_interest must be an object.")
        for field in ("raw_value", "smoothed_value", "prominence"):
            value = _finite_number(replay_interest.get(field), f"Candidate {index} {field}")
            if not 0 <= value <= 1:
                raise ValueError(f"Candidate {index} {field} must be between zero and one.")
        expected_id = build_candidate_id(
            video_id=video_id,
            generator=GENERATOR,
            generator_version=GENERATOR_VERSION,
            start_time=start,
            end_time=end,
        )
        if candidate_id != expected_id:
            raise ValueError(f"Candidate {index} candidate_id does not match the stable ID scheme.")
        candidate_ids.append(candidate_id)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate_id values must be unique within a candidate document.")
    return {"video_id": video_id, "duration_seconds": duration, "candidate_ids": candidate_ids}


def _adapter_document(payload: Any, canonical_candidates: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Compatibility adapter must be an object.")
    root_fields = set(payload)
    missing_root_fields = sorted(ADAPTER_ROOT_FIELDS - root_fields)
    unexpected_root_fields = sorted(root_fields - ADAPTER_ROOT_FIELDS)
    if missing_root_fields or unexpected_root_fields:
        details = []
        if missing_root_fields:
            details.append(f"missing {', '.join(missing_root_fields)}")
        if unexpected_root_fields:
            details.append(f"unexpected {', '.join(unexpected_root_fields)}")
        raise ValueError(f"Compatibility adapter root fields are invalid ({'; '.join(details)}).")
    if payload.get("schema_version") != 2:
        raise ValueError("Unsupported or missing compatibility adapter schema_version.")
    if payload.get("source") != "candidate_windows_compatibility_adapter":
        raise ValueError("Compatibility adapter source is invalid.")
    if payload.get("canonical_artifact") != "metadata/candidate_windows.json":
        raise ValueError("Compatibility adapter canonical_artifact is invalid.")
    if payload.get("candidate_id_scheme") != CANDIDATE_ID_SCHEME:
        raise ValueError("Compatibility adapter candidate_id_scheme is invalid.")
    if payload.get("candidate_id_version") != CANDIDATE_ID_VERSION:
        raise ValueError("Compatibility adapter candidate_id_version is invalid.")
    windows = payload.get("top_windows")
    if not isinstance(windows, list):
        raise ValueError("Compatibility adapter top_windows must be a list.")
    adapter_ids: list[str] = []
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            raise ValueError(f"Adapter window {index} must be an object.")
        window_fields = set(window)
        missing = sorted(ADAPTER_WINDOW_FIELDS - window_fields)
        unexpected = sorted(window_fields - ADAPTER_WINDOW_FIELDS)
        if missing or unexpected:
            details = []
            if missing:
                details.append(f"missing {', '.join(missing)}")
            if unexpected:
                details.append(f"unexpected {', '.join(unexpected)}")
            raise ValueError(f"Adapter window {index} fields are invalid ({'; '.join(details)}).")
        identifier = window.get("id")
        candidate_id = window.get("candidate_id")
        if not isinstance(identifier, str) or identifier != candidate_id:
            raise ValueError(f"Adapter window {index} id must equal candidate_id.")
        adapter_ids.append(identifier)
    if len(adapter_ids) != len(set(adapter_ids)):
        raise ValueError("Compatibility adapter IDs must be unique.")
    if canonical_candidates is not None and set(adapter_ids) != {
        candidate["candidate_id"] for candidate in canonical_candidates
    }:
        raise ValueError("Compatibility adapter IDs do not match canonical candidate IDs.")
    if canonical_candidates is not None:
        canonical_by_id = {
            candidate["candidate_id"]: candidate for candidate in canonical_candidates
        }
        for index, window in enumerate(windows):
            candidate = canonical_by_id[window["candidate_id"]]
            expected = {
                "id": candidate["candidate_id"],
                "candidate_id": candidate["candidate_id"],
                "rank": candidate["rank"],
                "source_peak_rank": candidate["source_peak_rank"],
                "peak_time": candidate["peak_time"],
                "start": candidate["start_time"],
                "end": candidate["end_time"],
                "duration": candidate["duration_seconds"],
                "boundary_source": "replay_interest_peak",
                "selection_source": "youtube_most_replayed",
                "replay_interest": candidate["replay_interest"],
            }
            for field in ("id", "candidate_id", "rank", "source_peak_rank", "boundary_source", "selection_source"):
                if window[field] != expected[field]:
                    raise ValueError(f"Adapter window {index} {field} does not match canonical projection.")
            for field in ("peak_time", "start", "end", "duration"):
                actual = _finite_number(window[field], f"Adapter window {index} {field}")
                if abs(actual - expected[field]) > _TIME_TOLERANCE:
                    raise ValueError(f"Adapter window {index} {field} does not match canonical projection.")
            if window["replay_interest"] != expected["replay_interest"]:
                raise ValueError(f"Adapter window {index} replay_interest does not match canonical projection.")
    return {"candidate_ids": adapter_ids}


def _peak_document(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Peak document must be an object.")
    if payload.get("schema_version") != PEAK_SCHEMA_VERSION:
        raise ValueError("Unsupported or missing peak document schema_version.")
    if payload.get("algorithm") != ALGORITHM:
        raise ValueError("Peak document algorithm is invalid.")
    if payload.get("algorithm_version") != ALGORITHM_VERSION:
        raise ValueError("Peak document algorithm_version is invalid.")
    _validated_parameters(payload.get("parameters"), PeakDetectorConfig, "Peak document")
    return validate_peak_document(payload)


def inspect_workspace(workspace: str | Path) -> dict[str, Any]:
    """Return a JSON-serialisable inspection report without changing *workspace*."""
    root = Path(workspace).resolve()
    metadata = root / "metadata"
    report: dict[str, Any] = {
        "workspace": root.name,
        "read_only": True,
        "artifacts": {},
        "result": "PASS",
    }
    source_media: dict[str, Any] | None = None
    heatmap: dict[str, Any] | None = None
    peaks: dict[str, Any] | None = None
    candidates: dict[str, Any] | None = None

    source_path = metadata / "source_media.json"
    if not source_path.exists():
        _record(report, "source_media", "missing", message="metadata/source_media.json is missing.")
    else:
        try:
            source_raw = _read_json(source_path)
            media_file = source_raw.get("media_file") if isinstance(source_raw, dict) else None
            if not isinstance(media_file, str):
                raise SourceMediaManifestError("Source media manifest is missing a valid media_file.")
            source_media = load_source_media_manifest(
                source_path, workspace_path=root, existing_video=root / media_file
            )
            _record(
                report,
                "source_media",
                "valid",
                source=source_media["source"],
                video_id=source_media["video_id"],
                media_present=True,
                schema_version=source_media["schema_version"],
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, SourceMediaManifestError) as exc:
            _record(report, "source_media", "invalid", message=_safe_error(exc))

    heatmap_path = metadata / "heatmap.json"
    if not heatmap_path.exists():
        _record(report, "heatmap", "missing", message="metadata/heatmap.json is missing.")
    else:
        try:
            heatmap = load_heatmap_document(heatmap_path)
            _record(
                report,
                "heatmap",
                "valid",
                source=heatmap["source"],
                extractor=heatmap["extractor"],
                extractor_version=heatmap["extractor_version"],
                video_id=heatmap["video_id"],
                point_count=len(heatmap["points"]),
                schema_version=heatmap["schema_version"],
                trusted=heatmap["synthetic"] is False,
            )
        except HeatmapUnavailableError as exc:
            _record(report, "heatmap", "invalid", message=_safe_error(exc))

    peaks_path = metadata / "heatmap_peaks.json"
    if not peaks_path.exists():
        _record(report, "heatmap_peaks", "missing", message="metadata/heatmap_peaks.json is missing.")
    else:
        try:
            peaks_raw = _read_json(peaks_path)
            peak_info = _peak_document(peaks_raw)
            peaks = peaks_raw
            _record(
                report,
                "heatmap_peaks",
                "valid",
                peak_count=len(peak_info["peaks"]),
                algorithm=peaks_raw["algorithm"],
                algorithm_version=peaks_raw["algorithm_version"],
                video_id=peak_info["video_id"],
                schema_version=peaks_raw["schema_version"],
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            _record(report, "heatmap_peaks", "invalid", message=_safe_error(exc))

    candidates_path = metadata / "candidate_windows.json"
    if not candidates_path.exists():
        _record(report, "candidate_windows", "missing", message="metadata/candidate_windows.json is missing.")
    else:
        try:
            candidates_raw = _read_json(candidates_path)
            candidate_info = _candidate_document(candidates_raw)
            candidates = candidates_raw
            _record(
                report,
                "candidate_windows",
                "valid",
                candidate_count=len(candidate_info["candidate_ids"]),
                schema_version=candidates_raw["schema_version"],
                candidate_id_scheme=candidates_raw["candidate_id_scheme"],
                candidate_id_version=candidates_raw["candidate_id_version"],
                candidate_ids_unique=True,
                windows_valid=True,
                semantic_scoring_fields_absent=True,
                video_id=candidate_info["video_id"],
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            _record(report, "candidate_windows", "invalid", message=_safe_error(exc))

    adapter_path = root / "top_windows.json"
    canonical_candidates = None
    if candidates is not None:
        canonical_candidates = candidates["candidates"]
    if not adapter_path.exists():
        _record(report, "top_windows", "missing", message="top_windows.json is missing.")
    else:
        try:
            adapter_info = _adapter_document(_read_json(adapter_path), canonical_candidates)
            _record(
                report,
                "top_windows",
                "valid",
                candidate_count=len(adapter_info["candidate_ids"]),
                id_matches_candidate_id=True,
                ids_match_canonical=canonical_candidates is not None,
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            _record(report, "top_windows", "invalid", message=_safe_error(exc))

    identity_values = [
        value
        for value in (
            source_media and source_media["video_id"],
            heatmap and heatmap["video_id"],
            peaks and peaks["video_id"],
            candidates and candidates["video_id"],
        )
        if value is not None
    ]
    if len(set(identity_values)) > 1:
        report["identity_consistent"] = False
    elif identity_values:
        report["identity_consistent"] = True

    durations = [
        value
        for value in (
            heatmap and heatmap["duration_seconds"],
            peaks and peaks["duration_seconds"],
            candidates and candidates["duration_seconds"],
        )
        if value is not None
    ]
    if len(durations) > 1:
        report["duration_consistent"] = all(
            abs(value - durations[0]) <= _TIME_TOLERANCE for value in durations[1:]
        )

    has_invalid = any(item["status"] == "invalid" for item in report["artifacts"].values())
    has_missing = any(item["status"] == "missing" for item in report["artifacts"].values())
    has_cross_artifact_failure = (
        report.get("identity_consistent") is False
        or report.get("duration_consistent") is False
    )
    if has_invalid or has_cross_artifact_failure:
        report["result"] = "FAIL"
    elif has_missing:
        report["result"] = "INCOMPLETE"
    else:
        report["result"] = "PASS"
    return report


def _print_human(report: dict[str, Any]) -> None:
    print(f"Sprint 1 artifact inspection: {report['result']}")
    print("Read-only: yes")
    for name, item in report["artifacts"].items():
        summary = item.get("message")
        if summary is None:
            visible = [
                f"{key}={value}"
                for key, value in item.items()
                if key != "status"
            ]
            summary = ", ".join(visible)
        print(f"- {name}: {item['status'].upper()}" + (f" ({summary})" if summary else ""))
    if report.get("identity_consistent") is False:
        print("- identity: FAIL (artifact video_id values disagree)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect existing Sprint 1 workspace artifacts without modifying them.")
    parser.add_argument("--workspace", required=True, help="Workspace containing metadata/ and optional top_windows.json.")
    parser.add_argument("--json", action="store_true", help="Print a compact JSON report.")
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Return a nonzero status if an artifact is missing, malformed, or inconsistent.",
    )
    args = parser.parse_args(argv)
    report = inspect_workspace(args.workspace)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        _print_human(report)
    return 1 if args.require_complete and report["result"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
