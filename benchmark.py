#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import itertools
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from typing import Any

from layout import VALID_LAYOUT_MODES, is_vertical_9_16, normalize_layout_mode
from pipeline_modes import (
    AI_MODE_LOCAL_ONLY,
    VALID_AI_MODES,
    VALID_SUBTITLE_CHECKER_MODES,
    allows_gemini,
    normalize_ai_mode,
    normalize_subtitle_checker_mode,
    subtitle_checker_sample_limit,
    subtitle_checker_uses_ai,
)
from semantic_clip_director import (
    SEMANTIC_DIRECTOR_MODE_OFF,
    SUBTITLE_CORRECTION_MODE_OFF,
    VALID_SEMANTIC_DIRECTOR_MODES,
    VALID_SUBTITLE_CORRECTION_MODES,
)


VALID_CONTENT_TYPES = ("podcast", "gameplay", "tutorial", "commentary", "generic")
VALID_CONTENT_TYPE_MODES = ("auto",) + VALID_CONTENT_TYPES
PROJECT_ROOT = Path(__file__).resolve().parent
BENCHMARK_ROOT = PROJECT_ROOT / "benchmarks"
SPEAKER_MODE_ALIASES = {
    "single_speaker": "single",
    "multi_speaker": "multi",
    "single": "single",
    "multi": "multi",
    "unknown": "unknown",
}
HUMAN_REVIEW_SCORE_FIELDS = (
    "human_relevance_score",
    "human_boundary_score",
    "human_crop_score",
)
HUMAN_REVIEW_KEY_FIELDS = (
    "case_id",
    "scenario_id",
    "clip_index",
    "clip_start",
    "clip_end",
)
DEDUP_OVERLAP_THRESHOLD = 0.67
DEDUP_AUTO_SCORE_DELTA = 2.5
MIN_HUMAN_REVIEW_RECORDS_FOR_TUNING = 10
HUMAN_REVIEW_TARGET_CASE_PRIORITY = {
    "emeritos_gameplay": 0,
    "canva_presentation_tutorial": 1,
    "magenta_team_podcast": 2,
    "ukraine_war_report": 3,
    "putin_parade_commentary": 4,
    "roman_giertych_commentary": 5,
}
HUMAN_REVIEW_NOTE_PATTERNS = {
    "ad / sponsor-like segment": ("reklama", "sponsor", "skin", "skiny", "skrzynie", "promo"),
    "buy menu": ("buy menu",),
    "setup / waiting": ("setup", "czekania", "waiting", "chodzenia", "chodzenie", "czekamy"),
    "smoke / utility": ("smoke", "utility"),
    "weak payoff": ("brak payoffu", "mało wyraźnego payoffu", "payoff nie jest bardzo mocny", "bez payoffu"),
    "missing context": ("bez kontekstu", "brak kontekstu"),
    "cut mid-sentence": ("ucięte zdanie", "w środku zdania", "polowie zdania", "połowie zdania"),
    "bad boundary": ("źle pocięte", "zle pocięte", "start/koniec", "za wczesny", "trochę długi", "ciaśniej"),
    "fatal crop": (
        "fatalny crop",
        "źle wykadrow",
        "nie widać co się dzieje",
        "słaby crop",
        "ucina ekran",
        "ucięty ekran",
    ),
    "too many speakers": ("więcej speakerów niż jest",),
    "unreadable subtitles": ("nieczytelne napisy", "napisy", "subtitles", "czytelność"),
}
HUMAN_REVIEW_CATEGORY_PATTERNS = {
    "scoring": (
        "ad / sponsor-like segment",
        "buy menu",
        "setup / waiting",
        "smoke / utility",
        "weak payoff",
        "missing context",
    ),
    "boundary": ("missing context", "cut mid-sentence", "bad boundary"),
    "crop": ("fatal crop",),
    "diarization": ("too many speakers",),
    "subtitles": ("unreadable subtitles",),
}
ITERATION_CHANGES = [
    "Human review preservation now also recovers complete scored rows from the previous `benchmarks/results.json`, so historical manual scores are archived even if the CSV archive file is missing.",
    "The report now marks small human-review samples explicitly and lists the next clips to review instead of presenting low-N tuning as statistically strong.",
    "Local scoring adds conservative penalties for ad/sponsor-like text, gameplay setup/waiting/smoke/utility, weak payoff, too much preamble and contextless talk-led clips.",
    "Local scoring adds small positive signals for gameplay action/payoff, tutorial instructions, podcast question-response shape and complete commentary thoughts.",
    "Boundary refinement now records `max_duration_clamped` and can trim a low-value gameplay opening when a later action/payoff remains inside the clip.",
]


@dataclass
class BenchmarkCase:
    case_id: str
    label: str
    expected_content_type: str
    source_url: str
    description: str
    video: Path
    audio: Path | None
    heatmap: Path | None
    info_json: Path | None
    transcript_source: Path | None
    expected_speaker_mode: str
    comparison_content_types: list[str]
    include_generic_baseline: bool
    notes: str
    review_batch: str = "old_baseline"


def resolve_path(value: str | None, base_dir: Path) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def relative_to_project_or_abs(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def load_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as file_handle:
            return json.load(file_handle)
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, ensure_ascii=False, indent=2)


def read_text_tail(path: Path, line_count: int = 20) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    return "\n".join(lines[-line_count:])


def ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float((result.stdout or "0").strip() or 0.0)


def probe_output_video(path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams") or []
    if not streams:
        raise RuntimeError(f"No video stream found in {path}")
    stream = streams[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    vertical = is_vertical_9_16(width, height)
    return {
        "width": width,
        "height": height,
        "aspect_ratio": "9:16" if vertical else (f"{width}:{height}" if width and height else None),
        "is_vertical_9_16": vertical,
    }


def parse_time(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    parts = [float(part) for part in str(value).strip().replace(",", ".").split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


def format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:05.2f}"
    return f"{minutes:02d}:{secs:05.2f}"


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def recursive_find_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            result = recursive_find_key(value, key)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = recursive_find_key(item, key)
            if result is not None:
                return result
    return None


def create_placeholder_heatmap(duration_seconds: float, interval: float = 0.19) -> list[dict[str, float]]:
    entries = []
    cursor = 0.0
    while cursor < duration_seconds:
        entries.append(
            {
                "start_time": round(cursor, 4),
                "end_time": round(min(duration_seconds, cursor + interval), 4),
                "value": 0.5,
            }
        )
        cursor += interval
    return entries


def interval_overlap_ratio(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_start = float(left["start"])
    left_end = float(left["end"])
    right_start = float(right["start"])
    right_end = float(right["end"])
    overlap = max(0.0, min(left_end, right_end) - max(left_start, right_start))
    if overlap <= 0:
        return 0.0
    left_duration = max(0.01, left_end - left_start)
    right_duration = max(0.01, right_end - right_start)
    return overlap / min(left_duration, right_duration)


def count_overlapping_windows(
    left_windows: list[dict[str, Any]],
    right_windows: list[dict[str, Any]],
    *,
    threshold: float = 0.6,
) -> int:
    if not left_windows or not right_windows:
        return 0
    used_right: set[int] = set()
    matches = 0
    for left in left_windows:
        best_index = None
        best_ratio = 0.0
        for index, right in enumerate(right_windows):
            if index in used_right:
                continue
            ratio = interval_overlap_ratio(left, right)
            if ratio >= threshold and ratio > best_ratio:
                best_ratio = ratio
                best_index = index
        if best_index is not None:
            used_right.add(best_index)
            matches += 1
    return matches


def clip_duplicate_ref(case_id: str, scenario_id: str, clip: dict[str, Any]) -> str:
    return (
        f"{case_id}:{scenario_id}:"
        f"{clip.get('index', '?')}:{clip.get('start_label', '?')}->{clip.get('end_label', '?')}"
    )


def _duplicate_preference_key(case_id: str, scenario_id: str, clip: dict[str, Any]) -> tuple[float, float, float]:
    local_score = float(clip.get("local_score", 0.0) or 0.0)
    auto_bonus = 0.5 if scenario_id == "auto" else 0.0
    earlier_bonus = -float(clip.get("start", 0.0) or 0.0)
    return (round(local_score + auto_bonus, 4), local_score, earlier_bonus)


def _choose_duplicate_winner(
    case_id: str,
    left: tuple[str, dict[str, Any]],
    right: tuple[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    left_scenario, left_clip = left
    right_scenario, right_clip = right
    left_score = float(left_clip.get("local_score", 0.0) or 0.0)
    right_score = float(right_clip.get("local_score", 0.0) or 0.0)
    if abs(left_score - right_score) <= DEDUP_AUTO_SCORE_DELTA:
        if left_scenario == "auto" and right_scenario != "auto":
            return left
        if right_scenario == "auto" and left_scenario != "auto":
            return right
    if _duplicate_preference_key(case_id, left_scenario, left_clip) >= _duplicate_preference_key(case_id, right_scenario, right_clip):
        return left
    return right


def annotate_case_duplicates(
    case_payload: dict[str, Any],
    *,
    overlap_threshold: float = DEDUP_OVERLAP_THRESHOLD,
) -> dict[str, Any]:
    scenarios = [
        scenario
        for scenario in case_payload.get("scenarios", [])
        if scenario.get("status") == "completed"
    ]
    clip_entries: list[dict[str, Any]] = []
    for scenario in scenarios:
        selection = scenario.setdefault("selection", {})
        clips = selection.get("clips") or []
        selection["removed_duplicates_count"] = 0
        selection["kept_clip_count_after_dedup"] = len(clips)
        for clip in clips:
            clip["deduped"] = bool(clip.get("deduped", False))
            clip["duplicate_of"] = str(clip.get("duplicate_of") or "")
            clip["overlap_ratio"] = round(float(clip.get("overlap_ratio", 0.0) or 0.0), 4)
            clip_entries.append(
                {
                    "scenario_id": str(scenario.get("scenario_id") or ""),
                    "clip": clip,
                }
            )

    duplicate_groups = 0
    duplicates_removed = 0
    visited: set[int] = set()
    for start_index, entry in enumerate(clip_entries):
        if start_index in visited:
            continue
        stack = [start_index]
        component: set[int] = set()
        while stack:
            index = stack.pop()
            if index in component:
                continue
            component.add(index)
            for other_index in range(len(clip_entries)):
                if other_index == index:
                    continue
                if interval_overlap_ratio(clip_entries[index]["clip"], clip_entries[other_index]["clip"]) >= overlap_threshold:
                    stack.append(other_index)
        visited.update(component)
        if len(component) <= 1:
            continue
        duplicate_groups += 1
        component_entries = [clip_entries[index] for index in sorted(component)]
        winner = component_entries[0]
        for contender in component_entries[1:]:
            winner_scenario = str(winner["scenario_id"] or "")
            contender_scenario = str(contender["scenario_id"] or "")
            chosen_scenario, chosen_clip = _choose_duplicate_winner(
                str(case_payload.get("case_id") or ""),
                (winner_scenario, winner["clip"]),
                (contender_scenario, contender["clip"]),
            )
            if chosen_clip is contender["clip"]:
                winner = contender

        winner_ref = clip_duplicate_ref(
            str(case_payload.get("case_id") or ""),
            str(winner["scenario_id"] or ""),
            winner["clip"],
        )
        removed_for_winner = 0
        for contender in component_entries:
            scenario_id = str(contender["scenario_id"] or "")
            scenario = next((item for item in scenarios if item.get("scenario_id") == scenario_id), None)
            if contender is winner:
                contender["clip"]["deduped"] = False
                contender["clip"]["duplicate_of"] = ""
                contender["clip"]["overlap_ratio"] = 0.0
                continue
            overlap = interval_overlap_ratio(contender["clip"], winner["clip"])
            contender["clip"]["deduped"] = True
            contender["clip"]["duplicate_of"] = winner_ref
            contender["clip"]["overlap_ratio"] = round(overlap, 4)
            if scenario is not None:
                scenario["selection"]["removed_duplicates_count"] = int(
                    scenario["selection"].get("removed_duplicates_count", 0) or 0
                ) + 1
            duplicates_removed += 1
            removed_for_winner += 1
        winner["clip"]["removed_duplicates_count"] = removed_for_winner

    for scenario in scenarios:
        clips = scenario.get("selection", {}).get("clips") or []
        kept_count = sum(1 for clip in clips if not clip.get("deduped"))
        scenario["selection"]["kept_clip_count_after_dedup"] = kept_count

    summary = {
        "overlap_threshold": overlap_threshold,
        "duplicate_groups": duplicate_groups,
        "duplicates_removed": duplicates_removed,
    }
    case_payload["deduplication"] = summary
    return summary


def summarize_score_distribution(values: list[float]) -> dict[str, float]:
    if not values:
        return {
            "count": 0,
            "min": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "median": 0.0,
            "stdev": 0.0,
        }
    return {
        "count": len(values),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "mean": round(statistics.fmean(values), 4),
        "median": round(statistics.median(values), 4),
        "stdev": round(statistics.pstdev(values) if len(values) > 1 else 0.0, 4),
    }


def summarize_temporal_metrics(
    windows: list[dict[str, Any]],
    *,
    material_duration: float,
) -> dict[str, Any]:
    if not windows:
        return {
            "earliest_start": 0.0,
            "latest_end": 0.0,
            "temporal_span_seconds": 0.0,
            "temporal_coverage_ratio": 0.0,
            "min_gap_seconds": 0.0,
            "mean_gap_seconds": 0.0,
            "redundant_pairs": 0,
        }
    ordered = sorted(windows, key=lambda item: float(item["start"]))
    earliest = float(ordered[0]["start"])
    latest = float(ordered[-1]["end"])
    gaps = [
        max(0.0, float(right["start"]) - float(left["end"]))
        for left, right in zip(ordered, ordered[1:])
    ]
    redundant_pairs = 0
    for left, right in itertools.combinations(ordered, 2):
        if interval_overlap_ratio(left, right) >= 0.2:
            redundant_pairs += 1
    return {
        "earliest_start": round(earliest, 4),
        "latest_end": round(latest, 4),
        "temporal_span_seconds": round(latest - earliest, 4),
        "temporal_coverage_ratio": round((latest - earliest) / max(material_duration, 0.01), 4),
        "min_gap_seconds": round(min(gaps) if gaps else 0.0, 4),
        "mean_gap_seconds": round(statistics.fmean(gaps) if gaps else 0.0, 4),
        "redundant_pairs": redundant_pairs,
    }


def scenario_id_for_content_type(content_type: str, *, expected_type: str) -> str:
    if content_type == "auto":
        return "auto"
    if content_type == expected_type:
        return f"manual_{content_type}"
    return f"compare_{content_type}"


def dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def build_case_scenarios(
    case: BenchmarkCase,
    *,
    include_compare_strategies: bool = False,
) -> list[dict[str, str]]:
    scenario_types = ["auto"]
    if include_compare_strategies:
        scenario_types.extend([case.expected_content_type, *case.comparison_content_types])
        if case.include_generic_baseline:
            scenario_types.append("generic")
    scenarios = []
    for content_type in dedupe_preserve_order(scenario_types):
        scenarios.append(
            {
                "id": scenario_id_for_content_type(content_type, expected_type=case.expected_content_type),
                "content_type": content_type,
                "label": "auto classification" if content_type == "auto" else f"manual {content_type}",
            }
        )
    return scenarios


def load_cases(config_path: Path) -> list[BenchmarkCase]:
    payload = load_json(config_path, default={}) or {}
    raw_cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(raw_cases, list):
        raise ValueError(f"Benchmark config must contain a 'cases' list: {config_path}")

    cases: list[BenchmarkCase] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            continue
        case_id = str(raw_case.get("id") or "").strip()
        expected_content_type = str(raw_case.get("expected_content_type") or "").strip().lower()
        if not case_id:
            raise ValueError("Each benchmark case must define a non-empty 'id'.")
        if expected_content_type not in VALID_CONTENT_TYPES:
            raise ValueError(
                f"Case {case_id}: expected_content_type must be one of {', '.join(VALID_CONTENT_TYPES)}."
            )
        expected_speaker_mode_raw = str(raw_case.get("expected_speaker_mode") or "unknown").strip().lower()
        expected_speaker_mode = SPEAKER_MODE_ALIASES.get(expected_speaker_mode_raw)
        if expected_speaker_mode not in {"single", "multi", "unknown"}:
            raise ValueError(
                f"Case {case_id}: expected_speaker_mode must be single, multi, single_speaker, multi_speaker or unknown."
            )
        comparison_content_types = [
            str(item).strip().lower()
            for item in raw_case.get("comparison_content_types", [])
            if str(item).strip().lower() in VALID_CONTENT_TYPES
        ]
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                label=str(raw_case.get("label") or case_id).strip() or case_id,
                expected_content_type=expected_content_type,
                source_url=str(raw_case.get("source_url") or "").strip(),
                description=str(raw_case.get("description") or "").strip(),
                video=resolve_path(raw_case.get("video"), PROJECT_ROOT) or Path(),
                audio=resolve_path(raw_case.get("audio"), PROJECT_ROOT),
                heatmap=resolve_path(raw_case.get("heatmap"), PROJECT_ROOT),
                info_json=resolve_path(raw_case.get("info_json"), PROJECT_ROOT),
                transcript_source=resolve_path(raw_case.get("transcript_source"), PROJECT_ROOT),
                expected_speaker_mode=expected_speaker_mode,
                comparison_content_types=comparison_content_types,
                include_generic_baseline=bool(raw_case.get("include_generic_baseline", True)),
                notes=str(raw_case.get("notes") or "").strip(),
                review_batch=str(raw_case.get("review_batch") or "old_baseline").strip() or "old_baseline",
            )
        )
    return cases


def discover_available_media(project_root: Path) -> list[str]:
    available = []
    for path in sorted((project_root / "input").glob("*")):
        if path.is_file() and path.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm", ".mp3", ".m4a", ".wav"}:
            available.append(str(path.relative_to(project_root)))
    benchmark_assets_root = project_root / "benchmarks" / "assets"
    if benchmark_assets_root.exists():
        for path in sorted(benchmark_assets_root.rglob("input/source.*")):
            if path.is_file() and path.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm", ".mp3", ".m4a", ".wav"}:
                available.append(str(path.relative_to(project_root)))
    for path in sorted((project_root / "tmp").glob("*")):
        if path.is_file() and path.suffix.lower() in {".mp3", ".wav", ".m4a"}:
            available.append(str(path.relative_to(project_root)))
    return available


def validate_case_inputs(case: BenchmarkCase) -> list[str]:
    issues = []
    if not case.video.exists():
        issues.append(f"Missing video file: {case.video}")
    if case.audio is not None and not case.audio.exists():
        issues.append(f"Missing audio file: {case.audio}")
    if case.heatmap is not None and not case.heatmap.exists():
        issues.append(f"Missing heatmap file: {case.heatmap}")
    if case.info_json is not None and not case.info_json.exists():
        issues.append(f"Missing info_json file: {case.info_json}")
    return issues


def run_command(cmd: list[str], log_path: Path, *, cwd: Path = PROJECT_ROOT) -> tuple[bool, str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as log_file:
        log_file.write("$ " + " ".join(f'"{part}"' if " " in part else part for part in cmd) + "\n\n")
        completed = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    if completed.returncode == 0:
        return True, ""
    return False, read_text_tail(log_path)


def copy_or_symlink(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def prepare_transcript(
    case: BenchmarkCase,
    shared_dir: Path,
    args: argparse.Namespace,
) -> tuple[Path, str]:
    transcript_out = shared_dir / "transcript.json"
    if case.transcript_source is not None and case.transcript_source.exists() and not args.force_transcribe:
        copy_or_symlink(case.transcript_source, transcript_out)
        return transcript_out, "reused_local_transcript"

    def build_transcribe_command(device: str, compute_type: str) -> list[str]:
        input_path = case.audio or case.video
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "transcribe.py"),
            "--file",
            str(input_path),
            "--out",
            str(transcript_out),
            "--backend",
            args.transcription_backend,
            "--whisper-model",
            args.whisper_model,
            "--device",
            device,
            "--compute-type",
            compute_type,
            "--diarization-backend",
            args.diarization_backend,
            "--max-speakers",
            str(args.diarization_max_speakers),
        ]
        if not args.enable_diarization:
            cmd.append("--disable-diarization")
        return cmd

    cmd = build_transcribe_command(args.transcription_device, args.transcription_compute_type)
    success, tail = run_command(cmd, shared_dir / "logs" / "transcribe.log")
    transcription_status = "generated_local_transcript"
    if (
        not success
        and args.transcription_device == "auto"
        and "cublas64_12.dll" in tail
    ):
        fallback_cmd = build_transcribe_command("cpu", "int8")
        success, tail = run_command(fallback_cmd, shared_dir / "logs" / "transcribe_cpu_fallback.log")
        transcription_status = "generated_local_transcript_cpu_fallback"
    if not success:
        raise RuntimeError(f"Local transcription failed:\n{tail}")
    if case.transcript_source is not None:
        copy_or_symlink(transcript_out, case.transcript_source)
        return transcript_out, f"{transcription_status}_cached"
    return transcript_out, transcription_status


def prepare_heatmap(case: BenchmarkCase, shared_dir: Path) -> tuple[Path, str]:
    heatmap_out = shared_dir / "heatmap.json"
    if case.heatmap is not None and case.heatmap.exists():
        copy_or_symlink(case.heatmap, heatmap_out)
        return heatmap_out, "existing_heatmap"

    if case.info_json is not None and case.info_json.exists():
        info_payload = load_json(case.info_json, default={}) or {}
        heatmap = recursive_find_key(info_payload, "heatmap")
        if isinstance(heatmap, list) and heatmap:
            write_json(heatmap_out, heatmap)
            return heatmap_out, "heatmap_from_info_json"
        duration = float(info_payload.get("duration") or 0.0)
        if duration > 0:
            write_json(heatmap_out, create_placeholder_heatmap(duration))
            return heatmap_out, "placeholder_from_info_json_duration"

    duration = ffprobe_duration(case.video if case.video.exists() else (case.audio or case.video))
    write_json(heatmap_out, create_placeholder_heatmap(duration))
    return heatmap_out, "placeholder_from_media_duration"


def run_subtitle_checker_for_case(
    case: BenchmarkCase,
    transcript_path: Path,
    shared_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    report_path = shared_dir / "subtitle_check_report.json"
    if args.subtitle_checker_mode == "off":
        return {
            "status": "skipped",
            "mode": "off",
            "report_path": str(report_path.relative_to(PROJECT_ROOT)),
        }

    checker_mode = normalize_subtitle_checker_mode(args.subtitle_checker_mode)
    if not allows_gemini(args.ai_mode) and subtitle_checker_uses_ai(checker_mode):
        checker_mode = "local_only"

    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "subtitler_checker.py"),
        "--audio",
        str(case.audio or case.video),
        "--transcript",
        str(transcript_path),
        "--report",
        str(report_path),
    ]
    if checker_mode == "local_only":
        cmd.append("--warn-only")

    sample_limit = subtitle_checker_sample_limit(checker_mode, default_full_samples=0)
    if sample_limit <= 0:
        cmd.append("--skip-ai")
    else:
        cmd.extend(["--max-samples", str(sample_limit)])

    success, tail = run_command(cmd, shared_dir / "logs" / "subtitle_checker.log")
    if not success:
        return {
            "status": "error",
            "mode": checker_mode,
            "error": tail,
            "report_path": str(report_path.relative_to(PROJECT_ROOT)),
        }

    report_payload = load_json(report_path, default={}) or {}
    summary = report_payload.get("summary", {}) if isinstance(report_payload, dict) else {}
    issue_codes = Counter()
    for issue in report_payload.get("issues", []) if isinstance(report_payload, dict) else []:
        issue_codes[str(issue.get("code") or "UNKNOWN")] += 1

    return {
        "status": str(summary.get("status") or "unknown"),
        "mode": checker_mode,
        "score": summary.get("score"),
        "segments": summary.get("segments"),
        "audio_duration": summary.get("audio_duration"),
        "issue_counts": summary.get("issue_counts") or {},
        "ai_status": summary.get("ai_status"),
        "top_issue_codes": issue_codes.most_common(5),
        "report_path": str(report_path.relative_to(PROJECT_ROOT)),
    }


def load_transcript_segments(transcript_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = load_json(transcript_path, default={}) or {}
    segments = payload.get("segments", payload) if isinstance(payload, dict) else payload
    metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
    cleaned = []
    if isinstance(segments, list):
        for item in segments:
            if not isinstance(item, dict):
                continue
            try:
                start = parse_time(item["start"])
                end = parse_time(item["end"])
            except Exception:
                continue
            if end <= start:
                continue
            cleaned.append(
                {
                    "start": start,
                    "end": end,
                    "text": str(item.get("text", "")).strip(),
                    "speaker": str(item.get("speaker") or "Speaker 0").strip() or "Speaker 0",
                    "importance": int(item.get("importance", 3) or 3),
                    "chaos": bool(item.get("chaos", False)),
                }
            )
    return cleaned, metadata if isinstance(metadata, dict) else {}


def summarize_transcript_metrics(
    transcript_segments: list[dict[str, Any]],
    transcript_metadata: dict[str, Any],
    *,
    expected_speaker_mode: str,
) -> dict[str, Any]:
    speaker_sequence = [segment["speaker"] for segment in transcript_segments if segment.get("text")]
    speaker_distribution = Counter(speaker_sequence)
    speaker_switches = sum(
        1 for left, right in zip(speaker_sequence, speaker_sequence[1:]) if left != right
    )
    dominant_speaker_ratio = (
        speaker_distribution.most_common(1)[0][1] / len(speaker_sequence)
        if speaker_sequence
        else 0.0
    )
    speaker_count = int(
        transcript_metadata.get("speaker_count")
        or len(speaker_distribution)
    )
    flags = []
    diarization_status = str(transcript_metadata.get("diarization_status") or "unknown")
    if bool(transcript_metadata.get("diarization_used_fallback")):
        flags.append("diarization_fallback_used")
    if expected_speaker_mode == "multi" and speaker_count <= 1:
        flags.append("expected_multi_speaker_but_detected_single")
    if expected_speaker_mode == "single" and speaker_count >= 3:
        flags.append("expected_single_speaker_but_detected_many")
    if expected_speaker_mode == "multi" and dominant_speaker_ratio >= 0.9:
        flags.append("dominant_speaker_ratio_very_high_for_multi_speaker_case")

    metrics = {
        "segment_count": len(transcript_segments),
        "speaker_count": speaker_count,
        "speaker_distribution": dict(sorted(speaker_distribution.items())),
        "speaker_switches": speaker_switches,
        "dominant_speaker_ratio": round(dominant_speaker_ratio, 4),
        "diarization_status": diarization_status,
        "diarization_used_fallback": bool(transcript_metadata.get("diarization_used_fallback")),
        "transcription_backend": transcript_metadata.get("transcription_backend") or transcript_metadata.get("backend"),
        "diarization_backend": transcript_metadata.get("diarization_backend"),
        "pipeline_seconds": transcript_metadata.get("pipeline_seconds"),
        "flags": flags,
    }
    for key in (
        "raw_cluster_count",
        "final_speaker_count",
        "single_speaker_likelihood",
        "multi_speaker_evidence",
        "clusters_merged",
        "tiny_clusters_removed",
        "decision_reason",
        "adjacent_similarity_mean",
        "top_cluster_similarity",
        "stable_cluster_count",
        "alternating_blocks",
        "raw_cluster_distribution",
        "raw_cluster_duration_share",
        "cluster_label_distribution",
    ):
        if key in transcript_metadata:
            metrics[key] = transcript_metadata.get(key)
    return metrics


def summarize_selection_metrics(
    windows: list[dict[str, Any]],
    *,
    material_duration: float,
) -> dict[str, Any]:
    local_scores = [float(window.get("local_score", 0.0) or 0.0) for window in windows]
    durations = [float(window["duration"]) for window in windows] if windows else []
    reasons = Counter()
    clips = []
    for index, window in enumerate(windows, start=1):
        clip_reasons = [str(reason) for reason in window.get("selection_reasons", [])]
        reasons.update(clip_reasons)
        clips.append(
            {
                "index": index,
                "start": round(float(window["start"]), 4),
                "end": round(float(window["end"]), 4),
                "start_label": format_time(float(window["start"])),
                "end_label": format_time(float(window["end"])),
                "duration": round(float(window["duration"]), 4),
                "local_score": round(float(window.get("local_score", 0.0) or 0.0), 4),
                "selection_strategy": window.get("selection_strategy"),
                "selection_source": window.get("selection_source"),
                "selection_reasons": clip_reasons,
                "local_features": window.get("local_features") or {},
                "boundary_metadata": window.get("boundary_metadata") or {},
                "summary": window.get("summary"),
                "deduped": bool(window.get("deduped", False)),
                "duplicate_of": str(window.get("duplicate_of") or ""),
                "overlap_ratio": round(float(window.get("overlap_ratio", 0.0) or 0.0), 4),
                "removed_duplicates_count": int(window.get("removed_duplicates_count", 0) or 0),
                "semantic_director_used": bool(window.get("semantic_director_used", False)),
                "semantic_model": str(window.get("semantic_model") or ""),
                "story_score": round(float(window.get("story_score", 0.0) or 0.0), 4),
                "hook_score": round(float(window.get("hook_score", 0.0) or 0.0), 4),
                "context_score": round(float(window.get("context_score", 0.0) or 0.0), 4),
                "payoff_score": round(float(window.get("payoff_score", 0.0) or 0.0), 4),
                "semantic_reason": str(window.get("semantic_reason") or ""),
                "semantic_boundary_adjusted": bool(window.get("semantic_boundary_adjusted", False)),
                "semantic_fallback_reason": str(window.get("semantic_fallback_reason") or ""),
            }
        )

    return {
        "clip_count": len(windows),
        "score_distribution": summarize_score_distribution(local_scores),
        "duration_distribution": summarize_score_distribution(durations),
        "temporal_metrics": summarize_temporal_metrics(windows, material_duration=material_duration),
        "selection_reason_counts": dict(reasons.most_common()),
        "removed_duplicates_count": 0,
        "kept_clip_count_after_dedup": len(windows),
        "clips": clips,
    }


def summarize_rendering_metrics(
    cutting_log: dict[str, Any],
    raw_dir: Path,
    subtitle_dir: Path,
    expected_clips: int,
) -> dict[str, Any]:
    raw_files = sorted(raw_dir.glob("segment_*.mp4"))
    subtitle_files = sorted(subtitle_dir.glob("segment_*.mp4"))
    adjustments = cutting_log.get("cutter_adjustments", []) if isinstance(cutting_log, dict) else []
    framing_modes = Counter()
    detection_samples = 0
    fallback_samples = 0
    reaction_samples = 0
    zoom_samples = 0
    ignored_faces_count = 0
    crop_modes = Counter()
    crop_priorities = Counter()
    layout_modes = Counter()
    layout_policies = Counter()
    layout_modes_used = Counter()
    tracking_modes = Counter()
    face_tracking_used_count = 0
    center_x_means = []
    center_y_means = []
    output_widths = Counter()
    output_heights = Counter()
    output_aspect_ratios = Counter()
    vertical_output_count = 0
    probed_output_count = 0

    for adjustment in adjustments:
        framing_mode = str(adjustment.get("framing_mode") or "unknown")
        framing_modes[framing_mode] += 1
        face_tracking = adjustment.get("face_tracking") or {}
        detection_samples += int(face_tracking.get("sampled_detections") or 0)
        fallback_samples += int(face_tracking.get("fallback_samples") or 0)
        reaction_samples += int(face_tracking.get("reaction_samples") or 0)
        zoom_samples += int(face_tracking.get("zoom_samples") or 0)
        ignored_faces_count += int(face_tracking.get("ignored_faces_count") or 0)
        if adjustment.get("layout_mode") or face_tracking.get("layout_mode"):
            layout_modes[str(adjustment.get("layout_mode") or face_tracking.get("layout_mode"))] += 1
        if adjustment.get("layout_policy") or face_tracking.get("layout_policy"):
            layout_policies[str(adjustment.get("layout_policy") or face_tracking.get("layout_policy"))] += 1
        if face_tracking.get("layout_mode_used"):
            layout_modes_used[str(face_tracking.get("layout_mode_used"))] += 1
        if face_tracking.get("crop_mode"):
            crop_modes[str(face_tracking.get("crop_mode"))] += 1
        if face_tracking.get("crop_priority"):
            crop_priorities[str(face_tracking.get("crop_priority"))] += 1
        if face_tracking.get("tracking_mode"):
            tracking_modes[str(face_tracking.get("tracking_mode"))] += 1
        if face_tracking.get("face_tracking_used"):
            face_tracking_used_count += 1
        if face_tracking.get("center_x_mean_norm") is not None:
            center_x_means.append(float(face_tracking.get("center_x_mean_norm")))
        if face_tracking.get("center_y_mean_norm") is not None:
            center_y_means.append(float(face_tracking.get("center_y_mean_norm")))

    for clip_path in subtitle_files or raw_files:
        try:
            inspected = probe_output_video(clip_path)
        except Exception:
            continue
        probed_output_count += 1
        vertical_output_count += int(bool(inspected.get("is_vertical_9_16")))
        output_widths[str(inspected.get("width"))] += 1
        output_heights[str(inspected.get("height"))] += 1
        output_aspect_ratios[str(inspected.get("aspect_ratio"))] += 1

    face_tracking_success = framing_modes.get("face_tracking", 0)
    render_success = len(raw_files) == expected_clips and len(subtitle_files) == expected_clips
    all_outputs_vertical = probed_output_count == len(subtitle_files or raw_files) and vertical_output_count >= probed_output_count

    return {
        "expected_clips": expected_clips,
        "raw_clip_count": len(raw_files),
        "subtitled_clip_count": len(subtitle_files),
        "render_success": render_success,
        "framing_modes": dict(framing_modes),
        "face_tracking_success_count": face_tracking_success,
        "center_fallback_count": framing_modes.get("center_fallback", 0),
        "face_tracking_used_count": face_tracking_used_count,
        "sampled_detections": detection_samples,
        "fallback_samples": fallback_samples,
        "reaction_samples": reaction_samples,
        "zoom_samples": zoom_samples,
        "ignored_faces_count": ignored_faces_count,
        "layout_modes": dict(layout_modes),
        "layout_policies": dict(layout_policies),
        "layout_modes_used": dict(layout_modes_used),
        "crop_modes": dict(crop_modes),
        "crop_priorities": dict(crop_priorities),
        "tracking_modes": dict(tracking_modes),
        "center_x_mean_norm": round(sum(center_x_means) / len(center_x_means), 4) if center_x_means else None,
        "center_y_mean_norm": round(sum(center_y_means) / len(center_y_means), 4) if center_y_means else None,
        "output_widths": dict(output_widths),
        "output_heights": dict(output_heights),
        "output_aspect_ratios": dict(output_aspect_ratios),
        "output_width": next(iter(output_widths.keys()), None),
        "output_height": next(iter(output_heights.keys()), None),
        "output_aspect_ratio": next(iter(output_aspect_ratios.keys()), None),
        "is_vertical_9_16": all_outputs_vertical,
        "vertical_output_count": vertical_output_count,
        "probed_output_count": probed_output_count,
        "raw_output_dir": relative_to_project_or_abs(raw_dir),
        "subtitle_output_dir": relative_to_project_or_abs(subtitle_dir),
    }


def summarize_subtitle_styles(
    transcript_path: Path,
    windows: list[dict[str, Any]],
    *,
    content_type_hint: str = "",
    expected_speaker_mode: str = "unknown",
    subtitle_correction_mode: str = SUBTITLE_CORRECTION_MODE_OFF,
    semantic_model: str = "models/gemini-2.5-flash",
) -> dict[str, Any]:
    import subtitler

    transcript = subtitler.load_transcript(transcript_path)
    style_counter = Counter()
    multi_speaker_clips = 0
    empty_event_clips = 0
    speaker_flips_smoothed = 0
    detected_speaker_counts = []
    effective_speaker_counts = []
    speaker_color_map: dict[str, Any] = {}
    clip_summaries = []

    for index, window in enumerate(windows, start=1):
        events, subtitle_debug = subtitler.build_subtitle_events_with_metadata(
            transcript,
            float(window["start"]),
            float(window["duration"]),
            content_type_hint=content_type_hint,
            expected_speaker_mode=expected_speaker_mode,
            subtitle_correction_mode=subtitle_correction_mode,
            semantic_model=semantic_model,
        )
        speaker_names = sorted({event.get("speaker", "Speaker 0") for event in events})
        style_counter.update(speaker_names)
        speaker_flips_smoothed += int(subtitle_debug.get("speaker_flips_smoothed") or 0)
        detected_speaker_counts.append(int(subtitle_debug.get("detected_speaker_count") or 0))
        effective_speaker_counts.append(int(subtitle_debug.get("effective_speaker_count") or 0))
        speaker_color_map.update(subtitle_debug.get("speaker_color_map") or {})
        if len(speaker_names) >= 2:
            multi_speaker_clips += 1
        if not events:
            empty_event_clips += 1
        clip_summaries.append(
            {
                "index": index,
                "event_count": len(events),
                "speakers": speaker_names,
                "speaker_flips_smoothed": int(subtitle_debug.get("speaker_flips_smoothed") or 0),
                "detected_speaker_count": int(subtitle_debug.get("detected_speaker_count") or 0),
                "effective_speaker_count": int(subtitle_debug.get("effective_speaker_count") or 0),
                "speaker_smoothing_enabled": bool(subtitle_debug.get("speaker_smoothing_enabled", False)),
                "speaker_smoothing_window": float(subtitle_debug.get("speaker_smoothing_window") or 0.0),
                "speaker_color_map": subtitle_debug.get("speaker_color_map") or {},
                "merged_low_duration_speakers": subtitle_debug.get("merged_low_duration_speakers") or [],
                "speaker_stability_reason": str(subtitle_debug.get("speaker_stability_reason") or ""),
                "subtitles_corrected": bool(subtitle_debug.get("subtitles_corrected", False)),
                "subtitle_corrector_used": str(subtitle_debug.get("subtitle_corrector_used") or ""),
                "corrected_segments_count": int(subtitle_debug.get("corrected_segments_count") or 0),
                "correction_fallback_reason": str(subtitle_debug.get("correction_fallback_reason") or ""),
            }
        )

    return {
        "speaker_styles_used": dict(style_counter),
        "multi_speaker_clips": multi_speaker_clips,
        "empty_event_clips": empty_event_clips,
        "speaker_flips_smoothed": speaker_flips_smoothed,
        "detected_speaker_count": max(detected_speaker_counts) if detected_speaker_counts else 0,
        "effective_speaker_count": max(effective_speaker_counts) if effective_speaker_counts else 0,
        "speaker_color_map": speaker_color_map,
        "speaker_smoothing_enabled": True,
        "speaker_smoothing_window": float(getattr(subtitler, "DEFAULT_SPEAKER_SMOOTHING_WINDOW", 0.0)),
        "merged_low_duration_speakers": sorted(
            {
                speaker
                for clip_summary in clip_summaries
                for speaker in (clip_summary.get("merged_low_duration_speakers") or [])
            }
        ),
        "speaker_stability_reason": next(
            (
                str(clip_summary.get("speaker_stability_reason") or "")
                for clip_summary in clip_summaries
                if clip_summary.get("speaker_stability_reason")
            ),
            "",
        ),
        "subtitles_corrected": any(bool(clip_summary.get("subtitles_corrected")) for clip_summary in clip_summaries),
        "subtitle_corrector_used": next(
            (
                str(clip_summary.get("subtitle_corrector_used") or "")
                for clip_summary in clip_summaries
                if clip_summary.get("subtitle_corrector_used")
            ),
            "off",
        ),
        "corrected_segments_count": sum(int(clip_summary.get("corrected_segments_count") or 0) for clip_summary in clip_summaries),
        "correction_fallback_reason": next(
            (
                str(clip_summary.get("correction_fallback_reason") or "")
                for clip_summary in clip_summaries
                if clip_summary.get("correction_fallback_reason")
            ),
            "",
        ),
        "clip_event_summary": clip_summaries,
    }


def build_human_review_rows(
    *,
    case_id: str,
    case_label: str,
    expected_content_type: str,
    scenario_id: str,
    scenario_label: str,
    selected_clips: list[dict[str, Any]],
    subtitle_dir: Path,
) -> list[dict[str, Any]]:
    rows = []
    for clip in selected_clips:
        if bool(clip.get("deduped")):
            continue
        clip_index = int(clip["index"])
        matching_files = sorted(subtitle_dir.glob(f"segment_{clip_index}_*.mp4"))
        clip_file = str(matching_files[0].relative_to(PROJECT_ROOT)) if matching_files else ""
        rows.append(
            {
                "case_id": case_id,
                "case_label": case_label,
                "expected_content_type": expected_content_type,
                "scenario_id": scenario_id,
                "scenario_label": scenario_label,
                "clip_index": clip_index,
                "clip_start": clip["start_label"],
                "clip_end": clip["end_label"],
                "local_score": clip["local_score"],
                "clip_file": clip_file,
                "human_relevance_score": "",
                "human_boundary_score": "",
                "human_crop_score": "",
                "notes": "",
            }
        )
    return rows


def write_human_review_template(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "case_id",
        "case_label",
        "expected_content_type",
        "scenario_id",
        "scenario_label",
        "clip_index",
        "clip_start",
        "clip_end",
        "local_score",
        "clip_file",
        "human_relevance_score",
        "human_boundary_score",
        "human_crop_score",
        "notes",
    ]
    with open(path, "w", encoding="utf-8", newline="") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_human_review_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8", newline="") as file_handle:
            return list(csv.DictReader(file_handle))
    except Exception:
        return []


def human_review_row_key(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(row.get(field, "") or "").strip() for field in HUMAN_REVIEW_KEY_FIELDS)


def row_has_human_input(row: dict[str, Any]) -> bool:
    if any(str(row.get(field, "") or "").strip() for field in HUMAN_REVIEW_SCORE_FIELDS):
        return True
    return bool(str(row.get("notes", "") or "").strip())


def row_has_complete_human_scores(row: dict[str, Any]) -> bool:
    return all(parse_optional_review_score(row.get(field)) is not None for field in HUMAN_REVIEW_SCORE_FIELDS)


def extract_human_review_rows_from_results(results_payload: dict[str, Any]) -> list[dict[str, Any]]:
    human_review = results_payload.get("human_review") if isinstance(results_payload, dict) else None
    scored_rows = human_review.get("scored_rows", []) if isinstance(human_review, dict) else []
    rows: list[dict[str, Any]] = []
    if not isinstance(scored_rows, list):
        return rows

    fieldnames = [
        "case_id",
        "case_label",
        "expected_content_type",
        "scenario_id",
        "scenario_label",
        "clip_index",
        "clip_start",
        "clip_end",
        "local_score",
        "clip_file",
        "human_relevance_score",
        "human_boundary_score",
        "human_crop_score",
        "notes",
    ]
    for row in scored_rows:
        if not isinstance(row, dict):
            continue
        preserved = {field: row.get(field, "") for field in fieldnames}
        if row_has_human_input(preserved):
            rows.append(preserved)
    return rows


def merge_human_review_rows(
    generated_rows: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    existing_index: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in existing_rows:
        key = human_review_row_key(row)
        previous = existing_index.get(key)
        if previous is None:
            existing_index[key] = row
            continue
        if row_has_human_input(row) and not row_has_human_input(previous):
            existing_index[key] = row
            continue
        if row_has_human_input(row) == row_has_human_input(previous):
            previous_note_len = len(str(previous.get("notes", "") or "").strip())
            current_note_len = len(str(row.get("notes", "") or "").strip())
            if current_note_len > previous_note_len:
                existing_index[key] = row
    merged_rows = []
    archived_rows = []
    generated_keys = set()

    for row in generated_rows:
        key = human_review_row_key(row)
        generated_keys.add(key)
        merged = dict(row)
        existing = existing_index.get(key)
        if existing:
            for field in HUMAN_REVIEW_SCORE_FIELDS + ("notes",):
                value = str(existing.get(field, "") or "").strip()
                if value:
                    merged[field] = value
        merged_rows.append(merged)

    for row in existing_rows:
        key = human_review_row_key(row)
        if key in generated_keys or not row_has_human_input(row):
            continue
        preserved = dict(row)
        for field in (
            "case_id",
            "case_label",
            "expected_content_type",
            "scenario_id",
            "scenario_label",
            "clip_index",
            "clip_start",
            "clip_end",
            "local_score",
            "clip_file",
            "human_relevance_score",
            "human_boundary_score",
            "human_crop_score",
            "notes",
        ):
            preserved.setdefault(field, "")
        archived_rows.append(preserved)

    archived_rows.sort(
        key=lambda row: (
            str(row.get("case_id", "")),
            str(row.get("scenario_id", "")),
            int(row.get("clip_index", 0) or 0),
            str(row.get("clip_start", "")),
            str(row.get("clip_end", "")),
        )
    )
    return merged_rows, archived_rows


def parse_optional_review_score(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def clip_file_run_id(clip_file: str) -> str | None:
    parts = Path(str(clip_file or "")).parts
    for index, part in enumerate(parts[:-1]):
        if part == "runs" and index + 1 < len(parts):
            return parts[index + 1]
    return None


def build_review_clip_lookup(case_payloads: list[dict[str, Any]]) -> dict[tuple[str, ...], dict[str, Any]]:
    lookup: dict[tuple[str, ...], dict[str, Any]] = {}
    for case in case_payloads:
        if case.get("status") != "completed":
            continue
        for scenario in case.get("scenarios", []):
            if scenario.get("status") != "completed":
                continue
            subtitle_dir = scenario.get("artifacts", {}).get("subtitle_dir") or ""
            for clip in scenario.get("selection", {}).get("clips", []):
                if bool(clip.get("deduped")):
                    continue
                clip_index = int(clip.get("index") or 0)
                clip_file = ""
                if subtitle_dir:
                    clip_file = str(
                        Path(subtitle_dir) / f"segment_{clip_index}_{clip['start_label'].replace(':', '-').replace('.', '_')}_{clip['end_label'].replace(':', '-').replace('.', '_')}.mp4"
                    )
                row = {
                    "case_id": case.get("case_id"),
                    "case_label": case.get("label"),
                    "expected_content_type": case.get("expected_content_type"),
                    "scenario_id": scenario.get("scenario_id"),
                    "scenario_label": scenario.get("scenario_label"),
                    "clip_index": clip_index,
                    "clip_start": clip.get("start_label"),
                    "clip_end": clip.get("end_label"),
                    "local_score": clip.get("local_score"),
                    "clip_file": clip_file,
                    "local_features": clip.get("local_features") or {},
                    "selection_reasons": clip.get("selection_reasons") or [],
                    "selection_strategy": clip.get("selection_strategy"),
                    "selection_source": clip.get("selection_source"),
                    "boundary_metadata": clip.get("boundary_metadata") or {},
                    "deduped": bool(clip.get("deduped", False)),
                    "duplicate_of": clip.get("duplicate_of"),
                    "overlap_ratio": clip.get("overlap_ratio"),
                    "strategy_render_hints": scenario.get("classification", {}).get("strategy_render_hints") or {},
                }
                lookup.setdefault(human_review_row_key(row), row)
    return lookup


def _note_issue_counts(scored_rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter()
    for row in scored_rows:
        note_text = str(row.get("notes", "") or "").lower()
        if not note_text:
            continue
        for label, patterns in HUMAN_REVIEW_NOTE_PATTERNS.items():
            if any(pattern in note_text for pattern in patterns):
                counts[label] += 1
    return dict(counts.most_common())


def _category_issue_counts(note_issue_counts: dict[str, int]) -> dict[str, int]:
    totals = {}
    for category, labels in HUMAN_REVIEW_CATEGORY_PATTERNS.items():
        totals[category] = sum(int(note_issue_counts.get(label, 0)) for label in labels)
    return totals


def _average_triplet(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {"relevance": 0.0, "boundary": 0.0, "crop": 0.0}
    return {
        "relevance": round(sum(float(row["human_relevance_score"]) for row in rows) / len(rows), 4),
        "boundary": round(sum(float(row["human_boundary_score"]) for row in rows) / len(rows), 4),
        "crop": round(sum(float(row["human_crop_score"]) for row in rows) / len(rows), 4),
    }


def _scenario_review_priority(row: dict[str, Any]) -> int:
    scenario_id = str(row.get("scenario_id", "") or "")
    expected_type = str(row.get("expected_content_type", "") or "")
    if scenario_id == "auto":
        return 0
    if scenario_id == f"manual_{expected_type}":
        return 1
    if scenario_id.startswith("compare_"):
        return 2
    return 3


def build_next_human_review_targets(review_rows: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    candidates = [row for row in review_rows if not row_has_complete_human_scores(row)]
    candidates.sort(
        key=lambda row: (
            _scenario_review_priority(row),
            HUMAN_REVIEW_TARGET_CASE_PRIORITY.get(str(row.get("case_id", "") or ""), 99),
            str(row.get("case_id", "")),
            -float(parse_optional_review_score(row.get("local_score")) or 0.0),
            int(row.get("clip_index", 0) or 0),
        )
    )

    ordered_rows = []
    used_keys = set()
    for case_id in HUMAN_REVIEW_TARGET_CASE_PRIORITY:
        row = next(
            (
                candidate
                for candidate in candidates
                if str(candidate.get("case_id", "") or "") == case_id
                and str(candidate.get("scenario_id", "") or "") == "auto"
            ),
            None,
        )
        if row is None:
            continue
        key = human_review_row_key(row)
        used_keys.add(key)
        ordered_rows.append(row)

    for row in candidates:
        key = human_review_row_key(row)
        if key in used_keys:
            continue
        used_keys.add(key)
        ordered_rows.append(row)
        if len(ordered_rows) >= limit:
            break

    targets = []
    for row in ordered_rows[:limit]:
        targets.append(
            {
                "case_id": row.get("case_id", ""),
                "expected_content_type": row.get("expected_content_type", ""),
                "scenario_id": row.get("scenario_id", ""),
                "clip_index": row.get("clip_index", ""),
                "clip_start": row.get("clip_start", ""),
                "clip_end": row.get("clip_end", ""),
                "local_score": row.get("local_score", ""),
                "clip_file": row.get("clip_file", ""),
            }
        )
    return targets


def summarize_human_review(
    review_rows: list[dict[str, Any]],
    current_cases: list[dict[str, Any]],
    historical_cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    historical_cases = historical_cases or []
    lookup = build_review_clip_lookup(current_cases + historical_cases)
    scored_rows = []

    for row in review_rows:
        relevance = parse_optional_review_score(row.get("human_relevance_score"))
        boundary = parse_optional_review_score(row.get("human_boundary_score"))
        crop = parse_optional_review_score(row.get("human_crop_score"))
        if relevance is None or boundary is None or crop is None:
            continue

        key = human_review_row_key(row)
        clip_info = lookup.get(key, {})
        enriched = {
            **{field: row.get(field, "") for field in (
                "case_id",
                "case_label",
                "expected_content_type",
                "scenario_id",
                "scenario_label",
                "clip_index",
                "clip_start",
                "clip_end",
                "local_score",
                "clip_file",
                "notes",
            )},
            "local_features": clip_info.get("local_features") or {},
            "selection_reasons": clip_info.get("selection_reasons") or [],
            "selection_strategy": clip_info.get("selection_strategy"),
            "selection_source": clip_info.get("selection_source"),
            "boundary_metadata": clip_info.get("boundary_metadata") or {},
            "strategy_render_hints": clip_info.get("strategy_render_hints") or {},
            "human_relevance_score": relevance,
            "human_boundary_score": boundary,
            "human_crop_score": crop,
            "review_run_id": clip_file_run_id(str(row.get("clip_file", "") or "")),
            "matched_clip_metadata": bool(clip_info),
        }
        scored_rows.append(enriched)

    if not scored_rows:
        return {
            "scored_record_count": 0,
            "auto_scored_record_count": 0,
            "minimum_records_for_tuning": MIN_HUMAN_REVIEW_RECORDS_FOR_TUNING,
            "insufficient_for_weight_tuning": True,
            "scored_record_count_by_content_type": {},
            "scored_record_count_by_scenario": {},
            "scored_record_count_by_expected_content_type": {},
            "averages": {
                "overall": {"relevance": 0.0, "boundary": 0.0, "crop": 0.0},
                "by_content_type": {},
                "by_scenario": {},
            },
            "scored_rows": [],
            "note_issue_counts": {},
            "category_issue_counts": {},
            "top_quality_issues": [],
            "largest_remaining_issue": None,
            "review_run_ids": [],
            "matched_clip_metadata_count": 0,
            "next_review_targets": build_next_human_review_targets(review_rows),
        }

    grouped_by_content_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    grouped_by_scenario: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scored_rows:
        grouped_by_content_type[row["expected_content_type"]].append(row)
        grouped_by_scenario[row["scenario_id"]].append(row)
    averages_by_content_type = {
        key: _average_triplet(items)
        for key, items in sorted(grouped_by_content_type.items())
    }
    averages_by_scenario = {
        key: _average_triplet(items)
        for key, items in sorted(grouped_by_scenario.items())
    }
    scored_counts_by_content_type = {
        key: len(items)
        for key, items in sorted(grouped_by_content_type.items())
    }
    scored_counts_by_scenario = {
        key: len(items)
        for key, items in sorted(grouped_by_scenario.items())
    }

    note_issue_counts = _note_issue_counts(scored_rows)
    category_issue_counts = _category_issue_counts(note_issue_counts)
    overall_avg = _average_triplet(scored_rows)

    high_local_low_relevance = [
        row for row in scored_rows
        if parse_optional_review_score(row.get("local_score")) is not None
        and float(row["local_score"]) >= 80.0
        and row["human_relevance_score"] <= 2.0
    ]
    high_relevance_low_boundary = [
        row for row in scored_rows
        if row["human_relevance_score"] >= 4.0 and row["human_boundary_score"] <= 2.0
    ]
    high_relevance_low_crop = [
        row for row in scored_rows
        if row["human_relevance_score"] >= 4.0 and row["human_crop_score"] <= 2.0
    ]

    quality_issue_candidates = [
        {
            "key": "scoring",
            "label": "Scoring chooses setup / ad-like / weak-payoff clips too often.",
            "score": round((5.0 - overall_avg["relevance"]) + category_issue_counts.get("scoring", 0), 4),
        },
        {
            "key": "boundary",
            "label": "Clip boundaries still start too early or end too weakly.",
            "score": round((5.0 - overall_avg["boundary"]) + category_issue_counts.get("boundary", 0), 4),
        },
        {
            "key": "crop",
            "label": "Crop / framing still hurts readability on reviewed clips.",
            "score": round((5.0 - overall_avg["crop"]) + category_issue_counts.get("crop", 0), 4),
        },
        {
            "key": "diarization",
            "label": "Diarization mismatches are visible in human notes.",
            "score": round(float(category_issue_counts.get("diarization", 0)), 4),
        },
        {
            "key": "subtitles",
            "label": "Subtitle readability appears in human notes.",
            "score": round(float(category_issue_counts.get("subtitles", 0)), 4),
        },
    ]
    top_quality_issues = [item for item in sorted(quality_issue_candidates, key=lambda item: item["score"], reverse=True) if item["score"] > 0][:3]

    return {
        "scored_record_count": len(scored_rows),
        "auto_scored_record_count": sum(1 for row in scored_rows if row["scenario_id"] == "auto"),
        "minimum_records_for_tuning": MIN_HUMAN_REVIEW_RECORDS_FOR_TUNING,
        "insufficient_for_weight_tuning": len(scored_rows) < MIN_HUMAN_REVIEW_RECORDS_FOR_TUNING,
        "scored_record_count_by_content_type": scored_counts_by_content_type,
        "scored_record_count_by_scenario": scored_counts_by_scenario,
        "scored_record_count_by_expected_content_type": scored_counts_by_content_type,
        "averages": {
            "overall": overall_avg,
            "by_content_type": averages_by_content_type,
            "by_scenario": averages_by_scenario,
        },
        "scored_rows": scored_rows,
        "note_issue_counts": note_issue_counts,
        "category_issue_counts": category_issue_counts,
        "high_local_low_relevance": high_local_low_relevance,
        "high_relevance_low_boundary": high_relevance_low_boundary,
        "high_relevance_low_crop": high_relevance_low_crop,
        "top_quality_issues": top_quality_issues,
        "largest_remaining_issue": top_quality_issues[0] if top_quality_issues else None,
        "review_run_ids": sorted({run_id for run_id in (row["review_run_id"] for row in scored_rows) if run_id}),
        "matched_clip_metadata_count": sum(1 for row in scored_rows if row["matched_clip_metadata"]),
        "next_review_targets": build_next_human_review_targets(review_rows),
    }


def prepare_case_directories(run_dir: Path, case_id: str) -> dict[str, Path]:
    case_dir = run_dir / case_id
    shared_dir = case_dir / "shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    (shared_dir / "logs").mkdir(parents=True, exist_ok=True)
    return {
        "case_dir": case_dir,
        "shared_dir": shared_dir,
    }


def run_selection_scenario(
    case: BenchmarkCase,
    scenario: dict[str, str],
    transcript_path: Path,
    heatmap_path: Path,
    case_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    scenario_dir = case_dir / scenario["id"]
    raw_dir = scenario_dir / "cuts_raw"
    subtitle_dir = scenario_dir / "cuts_subtitles"
    logs_dir = scenario_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    windows_path = scenario_dir / "top_windows.json"
    cutting_log_path = scenario_dir / "cutting_logic.json"
    content_profile_path = scenario_dir / "content_profile.json"

    analyze_cmd = [
        sys.executable,
        str(PROJECT_ROOT / "analyze_virals.py"),
        "--transcript",
        str(transcript_path),
        "--heatmap",
        str(heatmap_path),
        "--video",
        str(case.video),
        "--save-json",
        str(windows_path),
        "--cutting-log",
        str(cutting_log_path),
        "--content-profile",
        str(content_profile_path),
        "--content-type",
        scenario["content_type"],
        "--layout-mode",
        args.layout_mode,
        "--ai-mode",
        args.ai_mode,
        "--semantic-director-mode",
        args.semantic_director_mode,
        "--semantic-model",
        args.semantic_model,
        "--top",
        str(args.top),
    ]
    analyze_ok, analyze_tail = run_command(analyze_cmd, logs_dir / "analyze.log")
    if not analyze_ok:
        return {
            "scenario_id": scenario["id"],
            "scenario_label": scenario["label"],
            "content_type_arg": scenario["content_type"],
            "status": "error",
            "error_stage": "analyze_virals",
            "error": analyze_tail,
        }

    windows = load_json(windows_path, default=[]) or []
    cutting_log = load_json(cutting_log_path, default={}) or {}
    content_routing = cutting_log.get("content_routing", {}) if isinstance(cutting_log, dict) else {}

    render_status = {
        "expected_clips": len(windows),
        "raw_clip_count": 0,
        "subtitled_clip_count": 0,
        "render_success": False,
        "framing_modes": {},
        "face_tracking_success_count": 0,
        "center_fallback_count": 0,
        "face_tracking_used_count": 0,
        "sampled_detections": 0,
        "fallback_samples": 0,
        "reaction_samples": 0,
        "zoom_samples": 0,
        "ignored_faces_count": 0,
        "layout_modes": {},
        "crop_modes": {},
        "crop_priorities": {},
        "tracking_modes": {},
        "center_x_mean_norm": None,
        "center_y_mean_norm": None,
        "output_widths": {},
        "output_heights": {},
        "output_aspect_ratios": {},
        "output_width": None,
        "output_height": None,
        "output_aspect_ratio": None,
        "is_vertical_9_16": False,
        "vertical_output_count": 0,
        "probed_output_count": 0,
        "raw_output_dir": str(raw_dir.relative_to(PROJECT_ROOT)),
        "subtitle_output_dir": str(subtitle_dir.relative_to(PROJECT_ROOT)),
        "skipped": bool(args.skip_render),
    }

    if not args.skip_render:
        cut_cmd = [
            sys.executable,
            str(PROJECT_ROOT / "cutter.py"),
            "--video",
            str(case.video),
            "--windows",
            str(windows_path),
            "--transcript",
            str(transcript_path),
            "--output-dir",
            str(raw_dir),
            "--cutting-log",
            str(cutting_log_path),
            "--layout-mode",
            args.layout_mode,
        ]
        cut_ok, cut_tail = run_command(cut_cmd, logs_dir / "cutter.log")
        if not cut_ok:
            return {
                "scenario_id": scenario["id"],
                "scenario_label": scenario["label"],
                "content_type_arg": scenario["content_type"],
                "status": "error",
                "error_stage": "cutter",
                "error": cut_tail,
                "classification": content_routing,
                "selection": summarize_selection_metrics(
                    windows,
                    material_duration=ffprobe_duration(case.video),
                ),
            }

        subs_cmd = [
            sys.executable,
            str(PROJECT_ROOT / "subtitler.py"),
            "--transcript",
            str(transcript_path),
            "--input-dir",
            str(raw_dir),
            "--output-raw",
            str(raw_dir),
            "--output-subs",
            str(subtitle_dir),
            "--content-type",
            str(content_routing.get("content_type") or case.expected_content_type or "generic"),
            "--expected-speaker-mode",
            str(case.expected_speaker_mode or "unknown"),
            "--subtitle-correction-mode",
            args.subtitle_correction_mode,
            "--semantic-model",
            args.semantic_model,
        ]
        subs_ok, subs_tail = run_command(subs_cmd, logs_dir / "subtitler.log")
        if not subs_ok:
            return {
                "scenario_id": scenario["id"],
                "scenario_label": scenario["label"],
                "content_type_arg": scenario["content_type"],
                "status": "error",
                "error_stage": "subtitler",
                "error": subs_tail,
                "classification": content_routing,
                "selection": summarize_selection_metrics(
                    windows,
                    material_duration=ffprobe_duration(case.video),
                ),
            }

        cutting_log = load_json(cutting_log_path, default=cutting_log) or cutting_log
        render_status = summarize_rendering_metrics(cutting_log, raw_dir, subtitle_dir, len(windows))
        render_status["skipped"] = False

    selection_metrics = summarize_selection_metrics(
        windows,
        material_duration=ffprobe_duration(case.video),
    )
    subtitle_style_metrics = summarize_subtitle_styles(
        transcript_path,
        windows,
        content_type_hint=str(content_routing.get("content_type") or case.expected_content_type or "generic"),
        expected_speaker_mode=str(case.expected_speaker_mode or "unknown"),
        subtitle_correction_mode=args.subtitle_correction_mode,
        semantic_model=args.semantic_model,
    )

    manual_override_applied = False
    if scenario["content_type"] != "auto":
        manual_override_applied = (
            content_routing.get("forced_content_type") == scenario["content_type"]
            and content_routing.get("content_type") == scenario["content_type"]
        )

    return {
        "scenario_id": scenario["id"],
        "scenario_label": scenario["label"],
        "content_type_arg": scenario["content_type"],
        "status": "completed",
        "classification": {
            "detected_content_type": content_routing.get("content_type"),
            "confidence": content_routing.get("confidence"),
            "reasons": content_routing.get("reasons") or [],
            "scores": content_routing.get("scores") or {},
            "features": content_routing.get("features") or {},
            "source": content_routing.get("source"),
            "classifier_source": content_routing.get("classifier_source"),
            "forced_content_type": content_routing.get("forced_content_type"),
            "loaded_from_profile": bool(content_routing.get("loaded_from_profile")),
            "strategy": (content_routing.get("strategy") or {}).get("name"),
            "strategy_weights": (content_routing.get("strategy") or {}).get("score_weights") or {},
            "strategy_render_hints": (content_routing.get("strategy") or {}).get("render_hints") or {},
            "strategy_layout": (content_routing.get("strategy") or {}).get("layout") or {},
            "layout_mode": content_routing.get("layout_mode"),
            "requested_layout_mode": content_routing.get("requested_layout_mode"),
            "manual_override_applied": manual_override_applied,
        },
        "selection": selection_metrics,
        "rendering": render_status,
        "subtitles": subtitle_style_metrics,
        "artifacts": {
            "windows": str(windows_path.relative_to(PROJECT_ROOT)),
            "cutting_log": str(cutting_log_path.relative_to(PROJECT_ROOT)),
            "content_profile": str(content_profile_path.relative_to(PROJECT_ROOT)),
            "raw_dir": str(raw_dir.relative_to(PROJECT_ROOT)),
            "subtitle_dir": str(subtitle_dir.relative_to(PROJECT_ROOT)),
        },
    }


def compare_scenarios(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    comparisons = []
    completed = [scenario for scenario in scenarios if scenario.get("status") == "completed"]
    for left, right in itertools.combinations(completed, 2):
        left_windows = left.get("selection", {}).get("clips", [])
        right_windows = right.get("selection", {}).get("clips", [])
        overlap = count_overlapping_windows(left_windows, right_windows)
        comparisons.append(
            {
                "left": left["scenario_id"],
                "right": right["scenario_id"],
                "top_n": min(len(left_windows), len(right_windows)),
                "overlap_count": overlap,
                "overlap_ratio": round(
                    overlap / max(1, min(len(left_windows), len(right_windows))),
                    4,
                ),
            }
        )
    return comparisons


def summarize_case_findings(
    case: BenchmarkCase,
    transcript_metrics: dict[str, Any],
    subtitle_checker: dict[str, Any],
    scenarios: list[dict[str, Any]],
    heatmap_source: str,
) -> list[str]:
    findings = []
    auto_scenario = next((item for item in scenarios if item["scenario_id"] == "auto"), None)
    if auto_scenario and auto_scenario.get("status") == "completed":
        auto_classification = auto_scenario["classification"]
        if auto_classification.get("detected_content_type") == case.expected_content_type:
            findings.append(
                f"Auto classification matched the expected type ({case.expected_content_type}) "
                f"with confidence {auto_classification.get('confidence')}."
            )
        else:
            findings.append(
                f"Auto classification missed the expected type: "
                f"{auto_classification.get('detected_content_type')} vs {case.expected_content_type}."
            )
    if heatmap_source.startswith("placeholder"):
        findings.append("The case used a placeholder heatmap, so selection results are less representative.")
    if subtitle_checker.get("status") == "warning":
        findings.append(
            f"Subtitle checker reported warnings "
            f"({subtitle_checker.get('issue_counts', {}).get('warnings', 0)}), but no hard failure."
        )
    if transcript_metrics.get("flags"):
        findings.append(
            "Transcript/diarization diagnostics raised flags: "
            + ", ".join(transcript_metrics["flags"])
        )
    completed_scenarios = [item for item in scenarios if item.get("status") == "completed"]
    auto_render = next(
        (
            item.get("rendering", {})
            for item in completed_scenarios
            if item.get("scenario_id") == "auto"
        ),
        {},
    )
    detection_samples = int(auto_render.get("sampled_detections") or 0)
    fallback_samples = int(auto_render.get("fallback_samples") or 0)
    total_samples = detection_samples + fallback_samples
    if total_samples > 0:
        detection_ratio = detection_samples / total_samples
        if detection_ratio < 0.15:
            findings.append(
                "Face-aware rendering completed, but actual face detections were sparse "
                f"({detection_samples}/{total_samples} sampled checks), so this benchmark does not strongly validate facecam tracking quality."
            )
    if completed_scenarios and all(item.get("rendering", {}).get("render_success") for item in completed_scenarios):
        findings.append("All rendered benchmark scenarios produced the requested subtitled clips.")
    return findings


def determine_recommendation(report_payload: dict[str, Any]) -> dict[str, str]:
    cases = report_payload.get("cases", [])
    human_review = report_payload.get("human_review") or {}
    tested_expected_types = {
        case.get("expected_content_type")
        for case in cases
        if case.get("status") == "completed"
    }
    missing_core_types = [
        content_type for content_type in ("podcast", "tutorial") if content_type not in tested_expected_types
    ]
    if len(tested_expected_types) < 2 or missing_core_types:
        return {
            "next_step": "expand_benchmark_corpus",
            "title": "Collect missing podcast and tutorial benchmark materials before tuning algorithms",
            "reason": (
                "The benchmark still lacks representative coverage for "
                + ", ".join(missing_core_types)
                + ". The current corpus is useful for gameplay and generic/commentary-like material, "
                "but it still cannot validate whether the cutter is truly universal across all target types."
            ),
        }

    auto_results = []
    render_failures = 0
    diarization_flags = 0
    for case in cases:
        auto = next((scenario for scenario in case.get("scenarios", []) if scenario.get("scenario_id") == "auto"), None)
        if auto and auto.get("status") == "completed":
            auto_results.append(
                auto.get("classification", {}).get("detected_content_type") == case.get("expected_content_type")
            )
        for scenario in case.get("scenarios", []):
            if scenario.get("status") == "completed" and not scenario.get("rendering", {}).get("render_success", True):
                render_failures += 1
        if case.get("transcript_metrics", {}).get("flags"):
            diarization_flags += 1

    accuracy = sum(1 for item in auto_results if item) / max(1, len(auto_results))
    if accuracy < 0.75:
        return {
            "next_step": "improve_classifier",
            "title": "Improve the heuristic content classifier",
            "reason": (
                f"Auto classification accuracy is only {accuracy:.0%} across the tested materials, "
                "so routing errors are likely to dominate downstream quality."
            ),
        }
    if render_failures > 0:
        return {
            "next_step": "improve_smart_crop",
            "title": "Improve smart crop and render robustness",
            "reason": "Selection looks usable, but rendering did not complete reliably for every benchmark scenario.",
        }
    if diarization_flags > 0:
        return {
            "next_step": "improve_diarization",
            "title": "Improve diarization quality on multi-speaker material",
            "reason": "Transcript diagnostics still show suspicious speaker attribution patterns in benchmark cases.",
        }
    if human_review.get("insufficient_for_weight_tuning"):
        return {
            "next_step": "more_human_review",
            "title": "Score more benchmark clips before stronger tuning",
            "reason": (
                f"Only {human_review.get('scored_record_count', 0)} complete human-review records are available; "
                f"at least {human_review.get('minimum_records_for_tuning', MIN_HUMAN_REVIEW_RECORDS_FOR_TUNING)} are recommended before larger scoring changes."
            ),
        }
    if human_review.get("scored_record_count", 0) > 0:
        largest_issue = (human_review.get("largest_remaining_issue") or {}).get("key")
        if largest_issue == "crop":
            return {
                "next_step": "smart_crop",
                "title": "Keep improving content-aware crop behavior",
                "reason": "Human review still points to framing as the main quality risk on reviewed clips.",
            }
        if largest_issue == "boundary":
            return {
                "next_step": "boundary_refinement",
                "title": "Tighten clip boundaries around complete spoken beats",
                "reason": "Human review still shows that starts and endings need more context and cleaner completion.",
            }
        if largest_issue == "scoring":
            return {
                "next_step": "scoring",
                "title": "Keep tuning local scoring against low-payoff selections",
                "reason": "Human review still shows more relevance problems than routing or rendering problems.",
            }
    return {
        "next_step": "tune_selection_weights",
        "title": "Tune local scoring weights per content type",
        "reason": (
            "Routing and rendering look stable enough, so the highest leverage next iteration is "
            "fine-tuning clip scoring and strategy weights with human review data."
        ),
    }


def build_markdown_report(report_payload: dict[str, Any]) -> str:
    primary_assets = [item for item in report_payload["available_media"] if item.lower().startswith("input\\")]
    benchmark_assets = [item for item in report_payload["available_media"] if item.lower().startswith("benchmarks\\assets\\")]
    auxiliary_assets = [
        item for item in report_payload["available_media"] if item not in primary_assets and item not in benchmark_assets
    ]
    configured_case_count = len(report_payload["cases"])
    tested_expected_types = set(report_payload["tested_expected_types"])
    missing_core_types = [content_type for content_type in ("podcast", "tutorial") if content_type not in tested_expected_types]
    lines = []
    lines.append("# AI-Virtual-Cutter Benchmark Report")
    lines.append("")
    lines.append(f"- Generated at: `{report_payload['generated_at']}`")
    lines.append(f"- Run id: `{report_payload['run_id']}`")
    lines.append(f"- AI mode: `{report_payload['ai_mode']}`")
    lines.append(f"- Subtitle checker mode: `{report_payload['subtitle_checker_mode']}`")
    lines.append(f"- Legacy media assets in `input/`: `{len(primary_assets)}`")
    lines.append(f"- Benchmark corpus assets in `benchmarks/assets/`: `{len(benchmark_assets)}`")
    lines.append(f"- Auxiliary smoke assets: `{len(auxiliary_assets)}`")
    lines.append("")
    lines.append("## Scope")
    lines.append("")
    for item in primary_assets:
        lines.append(f"- Legacy input asset: `{item}`")
    for item in benchmark_assets:
        lines.append(f"- Benchmark corpus asset: `{item}`")
    for item in auxiliary_assets:
        lines.append(f"- Auxiliary smoke asset (not used to claim universality): `{item}`")
    lines.append(f"- Configured benchmark cases: `{configured_case_count}`")
    lines.append(f"- Distinct expected content types tested: `{', '.join(sorted(report_payload['tested_expected_types'])) or 'none'}`")
    if configured_case_count == 4:
        lines.append("- This iteration expands the real benchmark corpus from `1` to `4` configured materials.")
    elif configured_case_count > 4:
        lines.append(
            f"- This iteration expands the real benchmark corpus to `{configured_case_count}` configured materials."
        )
    if "generic" in tested_expected_types and "podcast" not in tested_expected_types and "tutorial" not in tested_expected_types:
        lines.append(
            "- The new additions broaden coverage for `generic` / commentary-like material, but they do not replace missing true `podcast` and `tutorial` benchmarks."
        )
    if missing_core_types:
        lines.append(
            "- Coverage gap: the corpus still does not include a true "
            + " and ".join(f"`{content_type}`" for content_type in missing_core_types)
            + " benchmark case, so universality is still not empirically proven."
        )
    lines.append("")
    lines.append("## Classifier Results")
    lines.append("")
    lines.append("| Material | Expected | Auto detected | Confidence | Correct | Reasons |")
    lines.append("| --- | --- | --- | ---: | --- | --- |")
    for case in report_payload["cases"]:
        auto = next((scenario for scenario in case.get("scenarios", []) if scenario.get("scenario_id") == "auto"), None)
        if auto and auto.get("status") == "completed":
            reasons = "; ".join(auto["classification"].get("reasons", [])[:3])
            correct = "yes" if auto["classification"].get("detected_content_type") == case["expected_content_type"] else "no"
            lines.append(
                f"| {case['label']} | `{case['expected_content_type']}` | "
                f"`{auto['classification'].get('detected_content_type')}` | "
                f"{auto['classification'].get('confidence', 0):.2f} | {correct} | {reasons} |"
            )
        else:
            lines.append(f"| {case['label']} | `{case['expected_content_type']}` | n/a | n/a | no run | n/a |")
    lines.append("")

    completed_cases = [case for case in report_payload["cases"] if case.get("status") == "completed"]
    commentary_cases = [case for case in completed_cases if case.get("expected_content_type") == "commentary"]
    generic_cases = [case for case in completed_cases if case.get("expected_content_type") == "generic"]
    podcast_cases = [case for case in completed_cases if case.get("expected_content_type") == "podcast"]
    tutorial_cases = [case for case in completed_cases if case.get("expected_content_type") == "tutorial"]
    commentary_as_podcast = 0
    commentary_correct = 0
    podcast_correct = 0
    tutorial_correct = 0
    single_speaker_oversegmented = 0
    multi_speaker_flattened = 0
    commentary_like_cases = commentary_cases or generic_cases
    for case in commentary_like_cases:
        auto = next((scenario for scenario in case.get("scenarios", []) if scenario.get("scenario_id") == "auto"), None)
        if auto and auto.get("status") == "completed":
            if auto.get("classification", {}).get("detected_content_type") == "podcast":
                commentary_as_podcast += 1
    for case in commentary_cases:
        auto = next((scenario for scenario in case.get("scenarios", []) if scenario.get("scenario_id") == "auto"), None)
        if auto and auto.get("status") == "completed":
            if auto.get("classification", {}).get("detected_content_type") == "commentary":
                commentary_correct += 1
    for case in podcast_cases:
        auto = next((scenario for scenario in case.get("scenarios", []) if scenario.get("scenario_id") == "auto"), None)
        if auto and auto.get("status") == "completed":
            if auto.get("classification", {}).get("detected_content_type") == "podcast":
                podcast_correct += 1
    for case in tutorial_cases:
        auto = next((scenario for scenario in case.get("scenarios", []) if scenario.get("scenario_id") == "auto"), None)
        if auto and auto.get("status") == "completed":
            if auto.get("classification", {}).get("detected_content_type") == "tutorial":
                tutorial_correct += 1
    for case in completed_cases:
        flags = case.get("transcript_metrics", {}).get("flags") or []
        if case.get("expected_speaker_mode") == "single":
            if "expected_single_speaker_but_detected_many" in flags:
                single_speaker_oversegmented += 1
        if case.get("expected_speaker_mode") == "multi":
            if "expected_multi_speaker_but_detected_single" in flags:
                multi_speaker_flattened += 1

    lines.append("## Key Observations")
    lines.append("")
    if commentary_like_cases:
        lines.append(
            f"- Commentary-like cases routed to `podcast` in `{commentary_as_podcast}/{len(commentary_like_cases)}` cases."
        )
    if commentary_cases:
        lines.append(
            f"- True commentary cases classified correctly as `commentary`: `{commentary_correct}/{len(commentary_cases)}`."
        )
    if podcast_cases:
        lines.append(
            f"- True podcast cases classified correctly as `podcast`: `{podcast_correct}/{len(podcast_cases)}`."
        )
    if tutorial_cases:
        lines.append(
            f"- True tutorial cases classified correctly as `tutorial`: `{tutorial_correct}/{len(tutorial_cases)}`."
        )
    lines.append(
        f"- Expected single-speaker materials flagged as over-segmented by diarization: `{single_speaker_oversegmented}`."
    )
    lines.append(
        f"- Expected multi-speaker materials flattened to a single speaker: `{multi_speaker_flattened}`."
    )
    lines.append("")

    for case in report_payload["cases"]:
        lines.append(f"## {case['label']}")
        lines.append("")
        lines.append(f"- Expected content type: `{case['expected_content_type']}`")
        lines.append(f"- Expected speaker mode: `{case['expected_speaker_mode']}`")
        lines.append(f"- Status: `{case['status']}`")
        if case.get("source_url"):
            lines.append(f"- Source URL: {case['source_url']}")
        if case.get("description"):
            lines.append(f"- Description: {case['description']}")
        lines.append(f"- Notes: {case.get('notes') or 'none'}")
        lines.append(f"- Transcript preparation: `{case.get('transcript_preparation')}`")
        lines.append(f"- Heatmap source: `{case.get('heatmap_source')}`")
        lines.append("")
        lines.append("### Transcript / Diarization")
        lines.append("")
        transcript_metrics = case.get("transcript_metrics", {})
        lines.append(f"- Segments: `{transcript_metrics.get('segment_count')}`")
        lines.append(f"- Speakers: `{transcript_metrics.get('speaker_count')}`")
        lines.append(f"- Speaker switches: `{transcript_metrics.get('speaker_switches')}`")
        lines.append(f"- Dominant speaker ratio: `{transcript_metrics.get('dominant_speaker_ratio')}`")
        lines.append(f"- Diarization status: `{transcript_metrics.get('diarization_status')}`")
        lines.append(f"- Fallback used: `{transcript_metrics.get('diarization_used_fallback')}`")
        if transcript_metrics.get("raw_cluster_count") is not None:
            lines.append(f"- Raw cluster count: `{transcript_metrics.get('raw_cluster_count')}`")
        if transcript_metrics.get("final_speaker_count") is not None:
            lines.append(f"- Final speaker count: `{transcript_metrics.get('final_speaker_count')}`")
        if transcript_metrics.get("single_speaker_likelihood") is not None:
            lines.append(
                f"- Single-speaker likelihood: `{transcript_metrics.get('single_speaker_likelihood')}`"
            )
        if transcript_metrics.get("multi_speaker_evidence") is not None:
            lines.append(
                f"- Multi-speaker evidence: `{transcript_metrics.get('multi_speaker_evidence')}`"
            )
        if transcript_metrics.get("clusters_merged") is not None:
            lines.append(f"- Clusters merged: `{transcript_metrics.get('clusters_merged')}`")
        if transcript_metrics.get("tiny_clusters_removed") is not None:
            lines.append(f"- Tiny clusters removed: `{transcript_metrics.get('tiny_clusters_removed')}`")
        if transcript_metrics.get("decision_reason"):
            lines.append(f"- Decision reason: `{transcript_metrics.get('decision_reason')}`")
        if transcript_metrics.get("flags"):
            lines.append(f"- Diagnostic flags: `{', '.join(transcript_metrics['flags'])}`")
        lines.append("")
        lines.append("### Subtitle Checker")
        lines.append("")
        checker = case.get("subtitle_checker", {})
        lines.append(f"- Mode: `{checker.get('mode')}`")
        lines.append(f"- Status: `{checker.get('status')}`")
        if checker.get("score") is not None:
            lines.append(f"- Score: `{checker.get('score')}`")
        if checker.get("issue_counts"):
            issue_counts = checker["issue_counts"]
            lines.append(
                f"- Issues: `{issue_counts.get('errors', 0)}` errors, "
                f"`{issue_counts.get('warnings', 0)}` warnings"
            )
        if checker.get("top_issue_codes"):
            lines.append(
                "- Top issue codes: "
                + ", ".join(f"`{code}` x{count}" for code, count in checker["top_issue_codes"])
            )
        lines.append("")
        lines.append("### Strategy Scenarios")
        lines.append("")
        lines.append("| Scenario | Arg | Detected | Confidence | Layout | Render success | Top-5 overlap note |")
        lines.append("| --- | --- | --- | ---: | --- | --- | --- |")
        auto_id = next((scenario["scenario_id"] for scenario in case.get("scenarios", []) if scenario["scenario_id"] == "auto"), None)
        for scenario in case.get("scenarios", []):
            if scenario.get("status") != "completed":
                lines.append(f"| {scenario['scenario_id']} | `{scenario['content_type_arg']}` | error | n/a | n/a | n/a | {scenario.get('error_stage') or 'failed'} |")
                continue
            comparison_note = ""
            if scenario["scenario_id"] != "auto":
                pair = next(
                    (
                        item for item in case.get("scenario_comparisons", [])
                        if {item["left"], item["right"]} == {"auto", scenario["scenario_id"]}
                    ),
                    None,
                )
                if pair:
                    comparison_note = f"{pair['overlap_count']}/{pair['top_n']} overlap vs auto"
            lines.append(
                f"| {scenario['scenario_id']} | `{scenario['content_type_arg']}` | "
                f"`{scenario['classification'].get('detected_content_type')}` | "
                f"{scenario['classification'].get('confidence', 0):.2f} | "
                f"`{scenario['rendering'].get('layout_modes') or scenario['classification'].get('layout_mode')}` | "
                f"{scenario['rendering'].get('render_success')} | {comparison_note or '-'} |"
            )
        lines.append("")
        if case.get("scenario_comparisons"):
            lines.append("### Pairwise Overlap")
            lines.append("")
            for comparison in case["scenario_comparisons"]:
                lines.append(
                    f"- `{comparison['left']}` vs `{comparison['right']}`: "
                    f"`{comparison['overlap_count']}/{comparison['top_n']}` overlapping clips "
                    f"(`{comparison['overlap_ratio']:.2f}`)"
                )
            lines.append("")
        lines.append("### Top Clips")
        lines.append("")
        for scenario in case.get("scenarios", []):
            if scenario.get("status") != "completed":
                continue
            lines.append(f"#### {scenario['scenario_id']}")
            lines.append("")
            for clip in scenario["selection"].get("clips", []):
                reasons = ", ".join(clip.get("selection_reasons", [])[:3])
                lines.append(
                    f"- `{clip['start_label']} - {clip['end_label']}` | score `{clip['local_score']}` | "
                    f"reasons: {reasons or 'n/a'}"
                )
            lines.append("")
        lines.append("### Rendering")
        lines.append("")
        for scenario in case.get("scenarios", []):
            if scenario.get("status") != "completed":
                continue
            rendering = scenario.get("rendering", {})
            lines.append(
                f"- `{scenario['scenario_id']}`: render_success=`{rendering.get('render_success')}`, "
                f"face_tracking_success=`{rendering.get('face_tracking_success_count')}`, "
                f"center_fallback=`{rendering.get('center_fallback_count')}`, "
                f"zoom_samples=`{rendering.get('zoom_samples')}`, "
                f"layout_modes=`{rendering.get('layout_modes')}`, "
                f"crop_modes=`{rendering.get('crop_modes')}`, "
                f"output=`{rendering.get('output_width')}x{rendering.get('output_height')}` "
                f"aspect=`{rendering.get('output_aspect_ratio')}` "
                f"vertical_9_16=`{rendering.get('is_vertical_9_16')}`"
            )
        lines.append("")
        lines.append("### Layout / 9:16 Rendering")
        lines.append("")
        for scenario in case.get("scenarios", []):
            if scenario.get("status") != "completed":
                continue
            rendering = scenario.get("rendering", {})
            classification = scenario.get("classification", {})
            lines.append(
                f"- `{scenario['scenario_id']}`: layout=`{rendering.get('layout_modes') or classification.get('layout_mode')}`, "
                f"output=`{rendering.get('output_width')}x{rendering.get('output_height')}`, "
                f"aspect=`{rendering.get('output_aspect_ratio')}`, "
                f"vertical_9_16=`{rendering.get('is_vertical_9_16')}`, "
                f"crop_priorities=`{rendering.get('crop_priorities')}`, "
                f"tracking_modes=`{rendering.get('tracking_modes')}`"
            )
        lines.append("")
        lines.append("### Findings")
        lines.append("")
        for finding in case.get("findings", []):
            lines.append(f"- {finding}")
        lines.append("")

    lines.append("## Layout / 9:16 Rendering")
    lines.append("")
    lines.append(f"- Requested layout mode: `{report_payload.get('layout_mode', 'auto')}`")
    for case in report_payload.get("cases", []):
        for scenario in case.get("scenarios", []):
            if scenario.get("status") != "completed":
                continue
            rendering = scenario.get("rendering", {})
            lines.append(
                f"- `{case['case_id']}/{scenario['scenario_id']}` -> layout `{rendering.get('layout_modes') or scenario.get('classification', {}).get('layout_mode')}`, "
                f"output `{rendering.get('output_width')}x{rendering.get('output_height')}`, "
                f"aspect `{rendering.get('output_aspect_ratio')}`, "
                f"vertical_9_16 `{rendering.get('is_vertical_9_16')}`"
            )
    lines.append("")

    lines.append("## Human Review / Selection Quality")
    lines.append("")
    human_review = report_payload.get("human_review") or {}
    scored_record_count = int(human_review.get("scored_record_count", 0) or 0)
    minimum_records = int(human_review.get("minimum_records_for_tuning", MIN_HUMAN_REVIEW_RECORDS_FOR_TUNING) or MIN_HUMAN_REVIEW_RECORDS_FOR_TUNING)
    if scored_record_count <= 0:
        lines.append(
            f"- Fill in `{report_payload['human_review_template']}` with `human_relevance_score`, "
            "`human_boundary_score`, `human_crop_score` and notes for each rendered clip."
        )
        lines.append(f"- Minimum recommended complete records before weight tuning: `{minimum_records}`")
        targets = human_review.get("next_review_targets") or []
        if targets:
            lines.append("- Suggested next clips to review:")
            for target in targets[:10]:
                lines.append(
                    f"  - `{target['case_id']}/{target['scenario_id']}` clip `{target['clip_index']}` "
                    f"({target['clip_start']} - {target['clip_end']}) | local `{target['local_score']}`"
                )
        lines.append("")
    else:
        overall = human_review.get("averages", {}).get("overall", {})
        lines.append(f"- Records with complete human scores: `{scored_record_count}`")
        lines.append(f"- Minimum recommended complete records before weight tuning: `{minimum_records}`")
        if human_review.get("insufficient_for_weight_tuning"):
            lines.append("- Human-review signal is still small, so this iteration uses conservative rules and diagnostics rather than aggressive weight tuning.")
        lines.append(f"- Auto records with complete human scores: `{human_review.get('auto_scored_record_count', 0)}`")
        lines.append(
            f"- Current template complete records: `{human_review.get('template_complete_record_count', 0)}` / "
            f"`{human_review.get('template_record_count', 0)}`; archive complete records: "
            f"`{human_review.get('archive_complete_record_count', 0)}` / `{human_review.get('archive_record_count', 0)}`"
        )
        lines.append(
            f"- Average human scores: relevance `{overall.get('relevance', 0.0):.2f}`, "
            f"boundary `{overall.get('boundary', 0.0):.2f}`, crop `{overall.get('crop', 0.0):.2f}`"
        )
        review_run_ids = human_review.get("review_run_ids") or []
        if review_run_ids:
            lines.append(f"- Reviewed rows currently come from run ids: `{', '.join(review_run_ids)}`")
        lines.append("")
        lines.append("### Reviewed Averages")
        lines.append("")
        for content_type, averages in (human_review.get("averages", {}).get("by_content_type") or {}).items():
            count = (human_review.get("scored_record_count_by_content_type") or {}).get(content_type, 0)
            lines.append(
                f"- Content type `{content_type}` (`n={count}`): relevance `{averages['relevance']:.2f}`, "
                f"boundary `{averages['boundary']:.2f}`, crop `{averages['crop']:.2f}`"
            )
        for scenario_id, averages in (human_review.get("averages", {}).get("by_scenario") or {}).items():
            count = (human_review.get("scored_record_count_by_scenario") or {}).get(scenario_id, 0)
            lines.append(
                f"- Scenario `{scenario_id}` (`n={count}`): relevance `{averages['relevance']:.2f}`, "
                f"boundary `{averages['boundary']:.2f}`, crop `{averages['crop']:.2f}`"
            )
        lines.append("")
        lines.append("### Note Issues")
        lines.append("")
        for label, count in list((human_review.get("note_issue_counts") or {}).items())[:5]:
            lines.append(f"- `{label}`: `{count}`")
        lines.append("")
        lines.append("### Data-Backed Top Problems")
        lines.append("")
        for issue in human_review.get("top_quality_issues") or []:
            lines.append(f"- {issue['label']} (`score={issue['score']:.2f}`)")
        lines.append("")
        if human_review.get("high_local_low_relevance"):
            lines.append("### High Score / Low Relevance")
            lines.append("")
            for row in human_review["high_local_low_relevance"][:5]:
                lines.append(
                    f"- `{row['case_id']}/{row['scenario_id']}` clip `{row['clip_index']}` "
                    f"({row['clip_start']} - {row['clip_end']}) | local `{row['local_score']}` vs human relevance `{row['human_relevance_score']:.0f}`"
                )
            lines.append("")
        if human_review.get("high_relevance_low_boundary"):
            lines.append("### High Relevance / Low Boundary")
            lines.append("")
            for row in human_review["high_relevance_low_boundary"][:5]:
                lines.append(
                    f"- `{row['case_id']}/{row['scenario_id']}` clip `{row['clip_index']}` "
                    f"({row['clip_start']} - {row['clip_end']}) | relevance `{row['human_relevance_score']:.0f}`, boundary `{row['human_boundary_score']:.0f}`"
                )
            lines.append("")
        if human_review.get("high_relevance_low_crop"):
            lines.append("### High Relevance / Low Crop")
            lines.append("")
            for row in human_review["high_relevance_low_crop"][:5]:
                lines.append(
                    f"- `{row['case_id']}/{row['scenario_id']}` clip `{row['clip_index']}` "
                    f"({row['clip_start']} - {row['clip_end']}) | relevance `{row['human_relevance_score']:.0f}`, crop `{row['human_crop_score']:.0f}`"
                )
            lines.append("")
        lines.append("### Iteration Changes")
        lines.append("")
        for change in report_payload.get("iteration_changes") or []:
            lines.append(f"- {change}")
        lines.append("")
        lines.append("### Ranking Movement")
        lines.append("")
        if human_review.get("insufficient_for_weight_tuning"):
            lines.append(
                "- Not enough complete human review exists to claim a statistically strong ranking improvement. "
                "Use the next review pass to verify whether the new penalties demote setup/ad-like/weak-payoff clips."
            )
        elif human_review.get("high_local_low_relevance"):
            lines.append("- Some high-local-score clips still have low human relevance, so ranking quality remains the main tuning target.")
        else:
            lines.append("- Reviewed high-score clips no longer show a high-local/low-relevance pattern in the available scored sample.")
        lines.append("")
        targets = human_review.get("next_review_targets") or []
        if targets:
            lines.append("### Next Human Review Targets")
            lines.append("")
            for target in targets[:12]:
                lines.append(
                    f"- `{target['case_id']}/{target['scenario_id']}` clip `{target['clip_index']}` "
                    f"({target['clip_start']} - {target['clip_end']}) | expected `{target['expected_content_type']}` | "
                    f"local `{target['local_score']}`"
                )
            lines.append("")
        largest_issue = human_review.get("largest_remaining_issue") or {}
        if largest_issue:
            lines.append(
                f"- Biggest remaining problem after this iteration: `{largest_issue.get('key')}` "
                f"({largest_issue.get('label')})"
            )
            lines.append("")

    lines.append("## Selection Quality Tuning")
    lines.append("")
    lines.append(f"- Complete human-review records available: `{scored_record_count}`")
    if scored_record_count < minimum_records:
        lines.append(
            "- Tuning basis: defensive heuristics plus the existing human-review notes; "
            "the scored sample is too small for aggressive weight tuning."
        )
    else:
        lines.append("- Tuning basis: complete human-review rows plus defensive heuristics.")
    lines.append(
        "- Scoring changes: ad/sponsor, buy-menu, setup/waiting, smoke/utility, weak-payoff, "
        "long-preamble and contextless-fragment penalties; small boosts for gameplay action/payoff, "
        "tutorial instruction language, podcast dialogue shape and complete commentary thoughts."
    )
    lines.append(
        "- Boundary metadata: `original_start`, `original_end`, `refined_start`, `refined_end`, "
        "`boundary_adjustment_reason`, `sentence_boundary_used`, `speaker_turn_boundary_used`, "
        "`max_duration_clamped`."
    )
    lines.append(
        "- Boundary behavior: talk-led clips are aligned to transcript sentence/segment boundaries; "
        "gameplay clips can trim low-value setup while keeping a short pre-roll before action."
    )
    targets = human_review.get("next_review_targets") or []
    if targets:
        lines.append("- Fresh human review should prioritize:")
        for target in targets[:8]:
            lines.append(
                f"  - `{target['case_id']}/{target['scenario_id']}` clip `{target['clip_index']}` "
                f"({target['clip_start']} - {target['clip_end']}) | expected `{target['expected_content_type']}` | "
                f"local `{target['local_score']}`"
            )
    else:
        lines.append("- Fresh human review target list will be populated after the next benchmark run.")
    lines.append("")
    lines.append("## Recommendation")
    lines.append("")
    recommendation = report_payload["recommendation"]
    lines.append(f"- Next step: `{recommendation['next_step']}`")
    lines.append(f"- Title: {recommendation['title']}")
    lines.append(f"- Why: {recommendation['reason']}")
    lines.append("")
    return "\n".join(lines)


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    config_path = (PROJECT_ROOT / args.config).resolve() if not Path(args.config).is_absolute() else Path(args.config)
    cases = load_cases(config_path)
    if args.case:
        requested = set(args.case)
        cases = [case for case in cases if case.case_id in requested]
    if str(args.review_batch or "").strip():
        requested_batch = str(args.review_batch).strip()
        cases = [case for case in cases if case.review_batch == requested_batch]

    latest_results_path = (PROJECT_ROOT / args.output_dir / "results.json").resolve()
    latest_report_path = (PROJECT_ROOT / args.output_dir / "report.md").resolve()
    latest_human_review_path = (PROJECT_ROOT / args.output_dir / "human_review_template.csv").resolve()
    latest_human_review_archive_path = (PROJECT_ROOT / args.output_dir / "human_review_archive.csv").resolve()
    existing_results_payload = load_json(latest_results_path, default={}) or {}
    existing_human_review_rows = load_human_review_rows(latest_human_review_path)
    existing_human_review_archive_rows = load_human_review_rows(latest_human_review_archive_path)
    existing_results_human_review_rows = extract_human_review_rows_from_results(existing_results_payload)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = (PROJECT_ROOT / args.output_dir / "runs" / run_id).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    report_cases = []
    human_review_rows = []
    available_media = discover_available_media(PROJECT_ROOT)

    for case in cases:
        print(f"[benchmark] Case: {case.case_id}")
        case_paths = prepare_case_directories(run_dir, case.case_id)
        issues = validate_case_inputs(case)
        if issues:
            report_cases.append(
                {
                    "case_id": case.case_id,
                    "label": case.label,
                    "expected_content_type": case.expected_content_type,
                    "expected_speaker_mode": case.expected_speaker_mode,
                    "source_url": case.source_url,
                    "description": case.description,
                    "status": "skipped_missing_inputs",
                    "notes": case.notes,
                    "issues": issues,
                }
            )
            continue

        shared_dir = case_paths["shared_dir"]
        try:
            transcript_path, transcript_preparation = prepare_transcript(case, shared_dir, args)
        except Exception as exc:
            report_cases.append(
                {
                    "case_id": case.case_id,
                    "label": case.label,
                    "expected_content_type": case.expected_content_type,
                    "expected_speaker_mode": case.expected_speaker_mode,
                    "source_url": case.source_url,
                    "description": case.description,
                    "status": "transcription_failed",
                    "notes": case.notes,
                    "issues": [str(exc)],
                }
            )
            continue

        heatmap_path, heatmap_source = prepare_heatmap(case, shared_dir)
        subtitle_checker = run_subtitle_checker_for_case(case, transcript_path, shared_dir, args)
        transcript_segments, transcript_metadata = load_transcript_segments(transcript_path)
        transcript_metrics = summarize_transcript_metrics(
            transcript_segments,
            transcript_metadata,
            expected_speaker_mode=case.expected_speaker_mode,
        )

        scenarios = []
        for scenario in build_case_scenarios(case, include_compare_strategies=bool(args.include_compare_strategies)):
            print(f"[benchmark]   Scenario: {scenario['id']}")
            scenario_result = run_selection_scenario(
                case,
                scenario,
                transcript_path,
                heatmap_path,
                case_paths["case_dir"],
                args,
            )
            scenarios.append(scenario_result)

        case_payload = {
            "case_id": case.case_id,
            "label": case.label,
            "expected_content_type": case.expected_content_type,
            "expected_speaker_mode": case.expected_speaker_mode,
            "source_url": case.source_url,
            "description": case.description,
            "status": "completed",
            "notes": case.notes,
            "video": str(case.video.relative_to(PROJECT_ROOT)),
            "audio": str(case.audio.relative_to(PROJECT_ROOT)) if case.audio else None,
            "info_json": str(case.info_json.relative_to(PROJECT_ROOT)) if case.info_json else None,
            "heatmap": str(case.heatmap.relative_to(PROJECT_ROOT)) if case.heatmap else None,
            "transcript_source": str(case.transcript_source.relative_to(PROJECT_ROOT)) if case.transcript_source else None,
            "transcript_preparation": transcript_preparation,
            "heatmap_source": heatmap_source,
            "subtitle_checker": subtitle_checker,
            "transcript_metrics": transcript_metrics,
            "scenarios": scenarios,
            "scenario_comparisons": compare_scenarios(scenarios),
            "artifacts": {
                "case_dir": str(case_paths["case_dir"].relative_to(PROJECT_ROOT)),
                "shared_transcript": str(transcript_path.relative_to(PROJECT_ROOT)),
                "shared_heatmap": str(heatmap_path.relative_to(PROJECT_ROOT)),
            },
        }
        annotate_case_duplicates(case_payload)
        for scenario_result in scenarios:
            if scenario_result.get("status") == "completed":
                human_review_rows.extend(
                    build_human_review_rows(
                        case_id=case.case_id,
                        case_label=case.label,
                        expected_content_type=case.expected_content_type,
                        scenario_id=scenario_result["scenario_id"],
                        scenario_label=scenario_result["scenario_label"],
                        selected_clips=scenario_result["selection"].get("clips", []),
                        subtitle_dir=Path(PROJECT_ROOT / scenario_result["artifacts"]["subtitle_dir"]),
                    )
                )
        case_payload["findings"] = summarize_case_findings(
            case,
            transcript_metrics,
            subtitle_checker,
            scenarios,
            heatmap_source,
        )
        report_cases.append(case_payload)

    tested_expected_types = {
        case["expected_content_type"]
        for case in report_cases
        if case.get("status") == "completed"
    }
    report_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "ai_mode": args.ai_mode,
        "subtitle_checker_mode": args.subtitle_checker_mode,
        "semantic_director_mode": args.semantic_director_mode,
        "subtitle_correction_mode": args.subtitle_correction_mode,
        "semantic_model": args.semantic_model,
        "layout_mode": args.layout_mode,
        "available_media": available_media,
        "cases": report_cases,
        "tested_expected_types": sorted(tested_expected_types),
    }
    merged_human_review_rows, archived_human_review_rows = merge_human_review_rows(
        human_review_rows,
        existing_human_review_rows + existing_human_review_archive_rows + existing_results_human_review_rows,
    )
    report_payload["human_review_template"] = str(latest_human_review_path.relative_to(PROJECT_ROOT))
    report_payload["human_review_archive"] = str(latest_human_review_archive_path.relative_to(PROJECT_ROOT))
    report_payload["human_review"] = summarize_human_review(
        merged_human_review_rows + archived_human_review_rows,
        report_cases,
        historical_cases=existing_results_payload.get("cases") or [],
    )
    report_payload["human_review"]["template_record_count"] = len(merged_human_review_rows)
    report_payload["human_review"]["archive_record_count"] = len(archived_human_review_rows)
    report_payload["human_review"]["template_complete_record_count"] = sum(
        1 for row in merged_human_review_rows if row_has_complete_human_scores(row)
    )
    report_payload["human_review"]["archive_complete_record_count"] = sum(
        1 for row in archived_human_review_rows if row_has_complete_human_scores(row)
    )
    report_payload["iteration_changes"] = list(ITERATION_CHANGES)
    report_payload["recommendation"] = determine_recommendation(report_payload)
    write_json(run_dir / "results.json", report_payload)
    write_json(latest_results_path, report_payload)
    latest_report_path.parent.mkdir(parents=True, exist_ok=True)
    latest_report_path.write_text(build_markdown_report(report_payload), encoding="utf-8")
    write_human_review_template(latest_human_review_path, merged_human_review_rows)
    write_human_review_template(latest_human_review_archive_path, archived_human_review_rows)
    return report_payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local-only benchmark runner for AI-virtual-cutter.")
    parser.add_argument("--config", default="benchmarks/cases.json", help="Benchmark case config JSON")
    parser.add_argument("--output-dir", default="benchmarks", help="Benchmark output directory")
    parser.add_argument("--case", action="append", default=[], help="Run only selected case id(s)")
    parser.add_argument("--review-batch", default="", help="Run only cases tagged with the given review_batch")
    parser.add_argument("--top", type=int, default=5, help="How many clips to select per scenario")
    parser.add_argument(
        "--include-compare-strategies",
        action="store_true",
        help="Include manual/compare benchmark scenarios for diagnostics; default review output stays auto-only.",
    )
    parser.add_argument(
        "--ai-mode",
        default=AI_MODE_LOCAL_ONLY,
        choices=VALID_AI_MODES,
        help="Benchmark selection mode. local_only is recommended.",
    )
    parser.add_argument(
        "--subtitle-checker-mode",
        default="local_only",
        choices=VALID_SUBTITLE_CHECKER_MODES,
        help="Subtitle checker mode for benchmark runs.",
    )
    parser.add_argument(
        "--layout-mode",
        default="auto",
        choices=VALID_LAYOUT_MODES,
        help="Layout override for final 9:16 rendering.",
    )
    parser.add_argument(
        "--semantic-director-mode",
        default=SEMANTIC_DIRECTOR_MODE_OFF,
        choices=VALID_SEMANTIC_DIRECTOR_MODES,
        help="Experimental semantic story reviewer prototype. Disabled by default and not part of the recommended production flow.",
    )
    parser.add_argument(
        "--subtitle-correction-mode",
        default=SUBTITLE_CORRECTION_MODE_OFF,
        choices=VALID_SUBTITLE_CORRECTION_MODES,
        help="Experimental subtitle correction prototype. Disabled by default and not part of the recommended production flow.",
    )
    parser.add_argument("--semantic-model", default="models/gemini-2.5-flash", help="Experimental Gemini model for the prototype semantic/subtitle flow")
    parser.add_argument("--skip-render", action="store_true", help="Skip cutter/subtitler rendering stages")
    parser.add_argument("--force-transcribe", action="store_true", help="Force fresh local transcription per case")
    parser.add_argument("--transcription-backend", default="faster_whisper", help="Transcription backend")
    parser.add_argument("--whisper-model", default="small", help="Whisper model for forced transcription")
    parser.add_argument("--transcription-device", default="auto", help="cpu, cuda or auto")
    parser.add_argument("--transcription-compute-type", default="auto", help="int8, float16 or auto")
    parser.set_defaults(enable_diarization=True)
    parser.add_argument("--enable-diarization", dest="enable_diarization", action="store_true")
    parser.add_argument("--disable-diarization", dest="enable_diarization", action="store_false")
    parser.add_argument("--diarization-backend", default="heuristic_cluster", help="Diarization backend")
    parser.add_argument("--diarization-max-speakers", type=int, default=4, help="Max diarization speakers")
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    args.ai_mode = normalize_ai_mode(args.ai_mode)
    args.subtitle_checker_mode = normalize_subtitle_checker_mode(args.subtitle_checker_mode)
    args.layout_mode = normalize_layout_mode(args.layout_mode)
    if not allows_gemini(args.ai_mode) and subtitle_checker_uses_ai(args.subtitle_checker_mode):
        args.subtitle_checker_mode = "local_only"

    report_payload = run_benchmark(args)
    print(
        f"Benchmark finished. Cases: {len(report_payload['cases'])}. "
        f"Report: {Path(args.output_dir) / 'report.md'}"
    )


if __name__ == "__main__":
    main()
