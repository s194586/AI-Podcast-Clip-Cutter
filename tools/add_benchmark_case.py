#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS_ROOT = PROJECT_ROOT / "benchmarks"
CASES_PATH = BENCHMARKS_ROOT / "cases.json"
ASSETS_ROOT = BENCHMARKS_ROOT / "assets"
VALID_CONTENT_TYPES = {"auto", "gameplay", "podcast", "tutorial", "commentary", "generic"}
CASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def validate_case_id(case_id: str) -> str:
    normalized = str(case_id or "").strip().lower()
    if not normalized or not CASE_ID_RE.fullmatch(normalized):
        raise ValueError("case_id must use only lowercase letters, digits, underscores, and dashes.")
    return normalized


def validate_content_type(content_type: str) -> str:
    normalized = str(content_type or "").strip().lower()
    if normalized not in VALID_CONTENT_TYPES:
        raise ValueError(
            f"content_type must be one of: {', '.join(sorted(VALID_CONTENT_TYPES))}"
        )
    return normalized


def load_cases(path: Path = CASES_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"cases": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError(f"Invalid benchmark cases file: {path}")
    return payload


def save_cases(payload: dict[str, Any], path: Path = CASES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def case_exists(payload: dict[str, Any], case_id: str) -> bool:
    return any(str(item.get("id") or "").strip() == case_id for item in payload.get("cases", []))


def normalize_video_destination(source_path: Path) -> str:
    suffix = source_path.suffix.lower() or ".mp4"
    if suffix not in {".mp4", ".mov", ".mkv", ".webm"}:
        suffix = ".mp4"
    return f"source{suffix}"


def normalize_path_for_config(path: Path, project_root: Path) -> str:
    try:
        return str(path.relative_to(project_root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def build_case_payload(
    *,
    case_id: str,
    source_url: str,
    content_type: str,
    review_batch: str,
    notes: str,
    has_local_video: bool,
    video_suffix: str = ".mp4",
) -> dict[str, Any]:
    expected_content_type = "generic" if content_type == "auto" else content_type
    video_rel = f"benchmarks/assets/{case_id}/input/source{video_suffix}"
    return {
        "id": case_id,
        "label": case_id.replace("_", " ").replace("-", " ").title(),
        "source_url": source_url,
        "description": notes or f"Local benchmark case for {expected_content_type}.",
        "expected_content_type": expected_content_type,
        "expected_speaker_mode": "unknown",
        "local_video_path": video_rel if has_local_video else "",
        "video": video_rel if has_local_video else "",
        "audio": "",
        "transcript_source": f"benchmarks/assets/{case_id}/transcripts/final_transcript.json",
        "heatmap": f"benchmarks/assets/{case_id}/metadata/heatmap.json",
        "info_json": f"benchmarks/assets/{case_id}/metadata/source.info.json",
        "review_batch": review_batch,
        "comparison_content_types": [],
        "include_generic_baseline": True,
        "notes": notes,
    }


def add_case(
    *,
    case_id: str,
    source_url: str = "",
    video_path: str = "",
    content_type: str,
    review_batch: str,
    notes: str = "",
    force: bool = False,
    cases_path: Path = CASES_PATH,
    assets_root: Path = ASSETS_ROOT,
) -> dict[str, Any]:
    normalized_case_id = validate_case_id(case_id)
    normalized_content_type = validate_content_type(content_type)
    payload = load_cases(cases_path)
    if case_exists(payload, normalized_case_id) and not force:
        raise ValueError(f"Case '{normalized_case_id}' already exists. Use --force to overwrite it.")

    project_root = cases_path.resolve().parents[1]
    case_root = assets_root / normalized_case_id
    input_dir = case_root / "input"
    transcripts_dir = case_root / "transcripts"
    metadata_dir = case_root / "metadata"
    input_dir.mkdir(parents=True, exist_ok=True)
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    source = Path(video_path).expanduser().resolve() if video_path else None
    has_local_video = False
    destination_rel = ""
    video_suffix = ".mp4"
    if source:
        if not source.exists() or not source.is_file():
            raise ValueError(f"video_path does not exist: {source}")
        destination_name = normalize_video_destination(source)
        destination = input_dir / destination_name
        shutil.copy2(source, destination)
        has_local_video = True
        destination_rel = normalize_path_for_config(destination, project_root)
        video_suffix = destination.suffix.lower()

    new_case = build_case_payload(
        case_id=normalized_case_id,
        source_url=str(source_url or "").strip(),
        content_type=normalized_content_type,
        review_batch=str(review_batch or "").strip() or "local_v1",
        notes=str(notes or "").strip(),
        has_local_video=has_local_video,
        video_suffix=video_suffix,
    )
    if has_local_video:
        new_case["video"] = destination_rel
        new_case["local_video_path"] = destination_rel

    filtered_cases = [
        item for item in payload.get("cases", [])
        if str(item.get("id") or "").strip() != normalized_case_id
    ]
    filtered_cases.append(new_case)
    payload["cases"] = sorted(filtered_cases, key=lambda item: str(item.get("id") or ""))
    save_cases(payload, cases_path)

    return {
        "case_id": normalized_case_id,
        "content_type": normalized_content_type,
        "review_batch": new_case["review_batch"],
        "case_root": str(case_root),
        "video_copied": has_local_video,
        "video_path": destination_rel,
        "cases_path": str(cases_path),
        "force": bool(force),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Add or update a local benchmark case.")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--source-url", default="")
    parser.add_argument("--video-path", default="")
    parser.add_argument("--content-type", required=True, choices=sorted(VALID_CONTENT_TYPES))
    parser.add_argument("--review-batch", default="local_v1")
    parser.add_argument("--notes", default="")
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = add_case(
        case_id=args.case_id,
        source_url=args.source_url,
        video_path=args.video_path,
        content_type=args.content_type,
        review_batch=args.review_batch,
        notes=args.notes,
        force=bool(args.force),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
