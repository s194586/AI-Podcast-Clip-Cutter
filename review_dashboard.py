#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import quote


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_RESULTS_PATH = PROJECT_ROOT / "benchmarks" / "results.json"
DEFAULT_TEMPLATE_PATH = PROJECT_ROOT / "benchmarks" / "human_review_template.csv"
DEFAULT_REVIEWS_PATH = PROJECT_ROOT / "benchmarks" / "human_reviews.jsonl"
DEFAULT_HTML_PATH = PROJECT_ROOT / "benchmarks" / "review_dashboard.html"
CLIP_ID_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
REVIEW_FIELDS = (
    "rating",
    "good_clip",
    "boundary_issue",
    "boring_setup",
    "no_payoff",
    "too_context_dependent",
    "notes",
)
HUMAN_TEMPLATE_SCORE_FIELDS = (
    "human_relevance_score",
    "human_boundary_score",
    "human_crop_score",
)
REVIEW_NOTE_KEYWORDS = {
    "subtitles": ("subtitle", "subtitles", "napisy", "transcript"),
    "speaker": ("speaker", "speakers", "mówca", "mowca", "kolor", "colors"),
    "context": ("context", "kontekst", "bez kontekstu", "too_context"),
    "cut too short": ("cut too short", "za wcześnie", "za wczes", "ucięte", "urwane", "połowie zdania", "polowie zdania"),
    "boring": ("boring", "nudne", "nudny", "setup", "slow"),
    "no payoff": ("no payoff", "brak payoffu", "bez payoffu", "weak payoff"),
    "advertisement": ("advertisement", "ad ", "ad-like", "reklama", "sponsor", "intro", "czołówka", "czolowka"),
    "language mistakes": ("language", "język", "jezyk", "grammar", "grammat", "spelling", "ortograf", "błędy", "bledy"),
}


@dataclass
class ReviewClip:
    clip_id: str
    case_id: str
    scenario_id: str
    content_type: str = ""
    expected_content_type: str = ""
    clip_index: int | str = ""
    start: float | None = None
    end: float | None = None
    start_label: str = ""
    end_label: str = ""
    duration: float | None = None
    local_score: float | str | None = None
    final_score: float | str | None = None
    selection_reasons: list[str] = field(default_factory=list)
    local_features: dict[str, Any] = field(default_factory=dict)
    transcript_excerpt: str = ""
    video_path: str = ""
    human_template: dict[str, str] = field(default_factory=dict)
    latest_review: dict[str, Any] | None = None
    review_count: int = 0


def load_json(path: Path, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as file_handle:
            return json.load(file_handle)
    except Exception:
        return default


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", ""}:
        return False
    raise argparse.ArgumentTypeError(f"Expected true/false, got: {value}")


def format_time_value(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str) and ":" in value:
        return value.strip()
    try:
        seconds = max(0.0, float(value))
    except Exception:
        return str(value)
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes:02d}:{secs:05.2f}"


def normalize_project_path(path_text: str) -> str:
    return str(path_text or "").replace("\\", "/")


def resolve_video_path(video_path: str) -> Path:
    path = Path(normalize_project_path(video_path))
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def make_video_src(video_path: str, html_output_path: Path) -> str:
    """Return a browser-friendly video src relative to the generated HTML file."""
    if not video_path:
        return ""
    resolved = resolve_video_path(video_path)
    if not resolved.exists():
        return ""
    output_dir = html_output_path.parent.resolve()
    relative = os.path.relpath(resolved.resolve(), output_dir)
    normalized = normalize_project_path(relative)
    return quote(normalized, safe="/:._-~")


def video_mime_type(video_path: str) -> str:
    suffix = Path(normalize_project_path(video_path)).suffix.lower()
    if suffix == ".mp4":
        return "video/mp4"
    if suffix == ".webm":
        return "video/webm"
    if suffix == ".mov":
        return "video/quicktime"
    if suffix == ".mkv":
        return "video/x-matroska"
    return "video/mp4"


def stable_clip_id(case_id: str, scenario_id: str, start: Any, end: Any) -> str:
    start_label = format_time_value(start)
    end_label = format_time_value(end)
    base = f"{case_id}|{scenario_id}|{start_label}|{end_label}"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
    prefix = CLIP_ID_RE.sub("-", f"{case_id}-{scenario_id}").strip("-").lower()
    return f"{prefix}-{digest}" if prefix else digest


def review_key_from_row(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("case_id", "") or "").strip(),
        str(row.get("scenario_id", "") or "").strip(),
        str(row.get("clip_start", "") or "").strip(),
        str(row.get("clip_end", "") or "").strip(),
    )


def load_human_review_template(path: Path = DEFAULT_TEMPLATE_PATH) -> dict[tuple[str, str, str, str], dict[str, str]]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8", newline="") as file_handle:
        rows = list(csv.DictReader(file_handle))
    return {review_key_from_row(row): row for row in rows}


def load_jsonl_reviews(path: Path = DEFAULT_REVIEWS_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    reviews = []
    with open(path, "r", encoding="utf-8") as file_handle:
        for line in file_handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                reviews.append(payload)
    return reviews


def summarize_reviews_by_clip(reviews: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for review in reviews:
        clip_id = str(review.get("clip_id", "") or "").strip()
        if not clip_id:
            continue
        current = grouped.setdefault(clip_id, {"count": 0, "latest": None})
        current["count"] += 1
        current["latest"] = review
    return grouped


def summarize_review_flags(reviews: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "boundary_issue": 0,
        "boring_setup": 0,
        "no_payoff": 0,
        "too_context_dependent": 0,
    }
    for review in reviews:
        for key in summary:
            if bool(review.get(key)):
                summary[key] += 1
    return summary


def summarize_review_note_keywords(reviews: list[dict[str, Any]]) -> dict[str, int]:
    summary = {key: 0 for key in REVIEW_NOTE_KEYWORDS}
    for review in reviews:
        notes = str(review.get("notes") or "").strip().lower()
        if not notes:
            continue
        for key, patterns in REVIEW_NOTE_KEYWORDS.items():
            if any(pattern in notes for pattern in patterns):
                summary[key] += 1
    return summary


def template_review_payload(row: dict[str, str] | None) -> dict[str, Any] | None:
    if not row:
        return None
    payload: dict[str, Any] = {"source": "human_review_template.csv"}
    for field_name in HUMAN_TEMPLATE_SCORE_FIELDS:
        value = str(row.get(field_name, "") or "").strip()
        if value:
            payload[field_name] = value
    notes = str(row.get("notes", "") or "").strip()
    if notes:
        payload["notes"] = notes
    return payload if len(payload) > 1 else None


def _clip_video_path(
    clip: dict[str, Any],
    scenario: dict[str, Any],
    template_row: dict[str, str] | None,
) -> str:
    if template_row and template_row.get("clip_file"):
        return normalize_project_path(str(template_row.get("clip_file") or ""))
    subtitle_dir = normalize_project_path(((scenario.get("artifacts") or {}).get("subtitle_dir") or "").strip())
    clip_index = int(clip.get("index") or 0)
    if not subtitle_dir or not clip_index:
        return ""
    pattern = f"segment_{clip_index}_*.mp4"
    matches = sorted((PROJECT_ROOT / subtitle_dir).glob(pattern))
    if matches:
        try:
            return str(matches[0].relative_to(PROJECT_ROOT))
        except ValueError:
            return str(matches[0])
    return ""


def _extract_clip_text(clip: dict[str, Any]) -> str:
    for key in ("summary", "text", "transcript_excerpt"):
        value = str(clip.get(key, "") or "").strip()
        if value:
            return value
    return ""


def collect_clips(
    results_path: Path = DEFAULT_RESULTS_PATH,
    template_path: Path = DEFAULT_TEMPLATE_PATH,
    reviews_path: Path = DEFAULT_REVIEWS_PATH,
) -> list[ReviewClip]:
    results = load_json(results_path, default={}) or {}
    template_lookup = load_human_review_template(template_path)
    reviews_by_clip = summarize_reviews_by_clip(load_jsonl_reviews(reviews_path))
    clips: list[ReviewClip] = []

    case_payloads = results.get("cases", []) if isinstance(results, dict) else []
    for case in case_payloads:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", "") or "").strip()
        expected_type = str(case.get("expected_content_type", "") or "").strip()
        for scenario in case.get("scenarios", []) or []:
            if not isinstance(scenario, dict) or scenario.get("status") != "completed":
                continue
            scenario_id = str(scenario.get("scenario_id", "") or "").strip()
            classification = scenario.get("classification") or {}
            content_type = str(
                classification.get("detected_content_type")
                or scenario.get("content_type_arg")
                or expected_type
                or ""
            )
            for clip in (scenario.get("selection") or {}).get("clips", []) or []:
                if not isinstance(clip, dict):
                    continue
                if bool(clip.get("deduped")):
                    continue
                start_label = str(clip.get("start_label") or format_time_value(clip.get("start")))
                end_label = str(clip.get("end_label") or format_time_value(clip.get("end")))
                clip_id = stable_clip_id(case_id, scenario_id, start_label, end_label)
                template_row = template_lookup.get((case_id, scenario_id, start_label, end_label))
                review_summary = reviews_by_clip.get(clip_id) or {"count": 0, "latest": None}
                template_review = template_review_payload(template_row)
                clips.append(
                    ReviewClip(
                        clip_id=clip_id,
                        case_id=case_id,
                        scenario_id=scenario_id,
                        content_type=content_type,
                        expected_content_type=expected_type,
                        clip_index=clip.get("index", ""),
                        start=clip.get("start"),
                        end=clip.get("end"),
                        start_label=start_label,
                        end_label=end_label,
                        duration=clip.get("duration"),
                        local_score=clip.get("local_score"),
                        final_score=clip.get("final_score") or clip.get("local_score"),
                        selection_reasons=[str(item) for item in clip.get("selection_reasons", [])],
                        local_features=clip.get("local_features") or {},
                        transcript_excerpt=_extract_clip_text(clip),
                        video_path=_clip_video_path(clip, scenario, template_row),
                        human_template=template_row or {},
                        latest_review=review_summary.get("latest") or template_review,
                        review_count=int(review_summary.get("count") or 0) + (1 if template_review else 0),
                    )
                )
    return clips


def append_review(path: Path, review: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as file_handle:
        file_handle.write(json.dumps(review, ensure_ascii=False, sort_keys=True) + "\n")


def build_review_payload(args: argparse.Namespace) -> dict[str, Any]:
    if not 1 <= int(args.rating) <= 5:
        raise ValueError("rating must be between 1 and 5")
    payload = {
        "clip_id": args.clip_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rating": int(args.rating),
        "good_clip": bool(args.good_clip),
        "boundary_issue": bool(args.boundary_issue),
        "boring_setup": bool(args.boring_setup),
        "no_payoff": bool(args.no_payoff),
        "too_context_dependent": bool(args.too_context_dependent),
        "notes": str(args.notes or ""),
    }
    return payload


def _penalty_summary(features: dict[str, Any]) -> dict[str, Any]:
    wanted = (
        "ad_like_penalty",
        "low_payoff_penalty",
        "contextless_penalty",
        "preamble_penalty",
        "repetition_penalty",
        "boundary_completeness_score",
        "boundary_start_penalty",
        "boundary_end_penalty",
        "hook_score",
        "payoff_score",
        "podcast_dialogue_payoff_score",
        "speaker_turn_score",
    )
    return {key: features[key] for key in wanted if key in features}


def print_clip_list(clips: list[ReviewClip], *, limit: int | None = None) -> None:
    shown = clips[:limit] if limit else clips
    for clip in shown:
        status = "reviewed" if clip.latest_review else "unreviewed"
        score = clip.final_score if clip.final_score is not None else ""
        print(
            f"{clip.clip_id}\t{status}\t{clip.case_id}/{clip.scenario_id}\t"
            f"{clip.start_label}-{clip.end_label}\tscore={score}\tvideo={clip.video_path or '-'}"
        )
    print(f"clips_shown={len(shown)} total_clips={len(clips)}")


def render_html(clips: list[ReviewClip], output_path: Path) -> str:
    total_count = len(clips)
    reviewed_count = sum(1 for clip in clips if clip.latest_review)
    missing_video_count = sum(
        1 for clip in clips
        if not clip.video_path or not resolve_video_path(clip.video_path).exists()
    )
    case_counts: dict[str, int] = {}
    scenario_counts: dict[str, int] = {}
    for clip in clips:
        case_counts[clip.case_id] = case_counts.get(clip.case_id, 0) + 1
        scenario_label = f"{clip.scenario_id} / {clip.content_type or '-'}"
        scenario_counts[scenario_label] = scenario_counts.get(scenario_label, 0) + 1

    case_options = "\n".join(
        f'<option value="{html.escape(case_id)}">{html.escape(case_id)} ({count})</option>'
        for case_id, count in sorted(case_counts.items())
    )
    scenario_options = "\n".join(
        f'<option value="{html.escape(label)}">{html.escape(label)} ({count})</option>'
        for label, count in sorted(scenario_counts.items())
    )
    case_summary = ", ".join(f"{case_id}: {count}" for case_id, count in sorted(case_counts.items()))
    scenario_summary = ", ".join(f"{label}: {count}" for label, count in sorted(scenario_counts.items()))

    rows = []
    for clip in clips:
        latest = clip.latest_review or {}
        penalties = _penalty_summary(clip.local_features)
        resolved_video_path = resolve_video_path(clip.video_path) if clip.video_path else None
        video_exists = bool(resolved_video_path and resolved_video_path.exists())
        video_src = make_video_src(clip.video_path, output_path)
        mime_type = video_mime_type(clip.video_path)
        video_path_display = clip.video_path or "-"
        open_video_link = (
            f'<a href="{html.escape(video_src)}">open video file</a>'
            if video_src
            else ""
        )
        if video_src:
            video_html = (
                '<video controls preload="metadata">'
                f'<source src="{html.escape(video_src)}" type="{html.escape(mime_type)}">'
                "Your browser cannot play this video format."
                "</video>"
            )
        else:
            missing_detail = clip.video_path or "No video path was recorded for this clip."
            video_html = (
                '<div class="missing-video">'
                "<strong>missing video file</strong>"
                f"<span>Expected: {html.escape(missing_detail)}</span>"
                "</div>"
            )

        search_text = " ".join(
            [
                clip.clip_id,
                clip.case_id,
                clip.scenario_id,
                clip.content_type,
                clip.expected_content_type,
                clip.transcript_excerpt,
                " ".join(clip.selection_reasons),
                clip.video_path,
            ]
        )
        scenario_label = f"{clip.scenario_id} / {clip.content_type or '-'}"
        score_value = float(clip.final_score or 0.0)
        start_value = float(clip.start or 0.0)
        duration_value = float(clip.duration or 0.0)
        review_status = "reviewed" if clip.latest_review else "unreviewed"
        rows.append(
            f"""
            <article class="clip"
              data-clip-id="{html.escape(clip.clip_id)}"
              data-case="{html.escape(clip.case_id)}"
              data-scenario-label="{html.escape(scenario_label)}"
              data-status="{review_status}"
              data-score="{score_value:.6f}"
              data-start="{start_value:.6f}"
              data-duration="{duration_value:.6f}"
              data-search="{html.escape(search_text.lower())}">
              <div class="clip-header">
                <div>
                  <code>{html.escape(clip.clip_id)}</code>
                  <button type="button" class="copy-btn" data-copy="{html.escape(clip.clip_id)}">copy id</button>
                </div>
                <span>{html.escape(clip.case_id)}</span>
                <span>{html.escape(scenario_label)}</span>
                <span>{html.escape(clip.start_label)} - {html.escape(clip.end_label)} ({duration_value:.1f}s)</span>
                <span>score: {html.escape(str(clip.final_score or ""))}</span>
                <span class="status">{review_status}</span>
              </div>
              <div class="clip-body">
                <section class="preview">
                  {video_html}
                  <div class="video-path">
                    <span>{html.escape(video_path_display)}</span>
                    <button type="button" class="copy-btn" data-copy="{html.escape(video_path_display)}">copy path</button>
                    {open_video_link}
                  </div>
                </section>
                <section class="details">
                  <p class="excerpt">{html.escape(clip.transcript_excerpt or '')}</p>
                  <p><strong>Reasons:</strong> {html.escape(', '.join(clip.selection_reasons) or '-')}</p>
                  <details>
                    <summary>features / penalties</summary>
                    <pre>{html.escape(json.dumps(penalties, ensure_ascii=False, indent=2))}</pre>
                  </details>
                  <details {'open' if latest else ''}>
                    <summary>latest review ({clip.review_count})</summary>
                    <pre>{html.escape(json.dumps(latest, ensure_ascii=False, indent=2) if latest else 'No JSONL review yet.')}</pre>
                  </details>
                  <section class="review-form" data-clip-id="{html.escape(clip.clip_id)}">
                    <h3>Review</h3>
                    <label>rating
                      <select class="review-rating" aria-label="rating">
                        <option value="1">1</option>
                        <option value="2">2</option>
                        <option value="3" selected>3</option>
                        <option value="4">4</option>
                        <option value="5">5</option>
                      </select>
                    </label>
                    <label><input type="checkbox" class="review-good-clip"> good_clip</label>
                    <label><input type="checkbox" class="review-boundary-issue"> boundary_issue</label>
                    <label><input type="checkbox" class="review-boring-setup"> missing_context</label>
                    <label><input type="checkbox" class="review-no-payoff"> no_payoff</label>
                    <label><input type="checkbox" class="review-too-context-dependent"> too_context_dependent</label>
                    <label>notes
                      <textarea class="review-notes" rows="3" placeholder="What worked or failed?"></textarea>
                    </label>
                    <label>command preview
                      <textarea class="review-command" rows="4" readonly></textarea>
                    </label>
                    <button type="button" class="copy-review-command">Copy review command</button>
                  </section>
                </section>
              </div>
            </article>
            """
        )
    body = "\n".join(rows)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Podcast Cutter Review Dashboard</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; background: #f6f7f9; color: #1f2933; }}
    header, .toolbar, .summary {{ max-width: 1240px; margin: 0 auto 16px; }}
    .toolbar, .summary {{ background: white; border: 1px solid #d8dee8; border-radius: 8px; padding: 14px; }}
    .toolbar-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }}
    label {{ display: grid; gap: 4px; font-size: 0.92rem; }}
    input[type="search"], select, textarea {{ font: inherit; padding: 7px; border: 1px solid #cbd5e1; border-radius: 6px; }}
    button {{ font: inherit; padding: 6px 9px; border: 1px solid #b8c2d1; border-radius: 6px; background: #f8fafc; cursor: pointer; }}
    button:hover {{ background: #eef2f7; }}
    .clip {{ max-width: 1240px; margin: 0 auto 16px; padding: 16px; background: white; border: 1px solid #d8dee8; border-radius: 8px; }}
    .clip[hidden] {{ display: none; }}
    .clip-header {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; }}
    .clip-header span, code {{ background: #eef2f7; padding: 3px 6px; border-radius: 4px; }}
    .clip-body {{ display: grid; grid-template-columns: minmax(220px, 300px) 1fr; gap: 16px; align-items: start; }}
    @media (max-width: 760px) {{ .clip-body {{ grid-template-columns: 1fr; }} }}
    .status {{ font-weight: 700; }}
    video {{ width: min(280px, 100%); max-height: 500px; display: block; margin: 8px 0 12px; background: #111827; }}
    .missing-video {{ display: grid; gap: 6px; margin: 8px 0 12px; padding: 12px; background: #fff7ed; color: #8a3b12; border: 1px solid #fed7aa; border-radius: 8px; }}
    .video-path {{ display: grid; gap: 6px; font-size: 0.9rem; word-break: break-word; }}
    .excerpt {{ line-height: 1.45; }}
    .review-form {{ margin-top: 14px; padding-top: 12px; border-top: 1px solid #e2e8f0; display: grid; gap: 8px; }}
    .review-form h3 {{ margin: 0; }}
    .review-form label:has(input[type="checkbox"]) {{ display: inline-flex; gap: 6px; align-items: center; margin-right: 12px; }}
    pre {{ white-space: pre-wrap; background: #f3f4f6; padding: 8px; border-radius: 6px; overflow-x: auto; }}
    .counts {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .counts span {{ background: #eef2f7; padding: 4px 7px; border-radius: 4px; }}
  </style>
</head>
<body>
  <header>
    <h1>Podcast Cutter Review Dashboard</h1>
    <p>Use <code>python review_dashboard.py add-review --clip-id CLIP_ID --rating 4 --good-clip true --notes "..."</code> to append feedback to <code>benchmarks/human_reviews.jsonl</code>.</p>
    <p>Review podcast story quality: logical start, enough context, clear answer or thesis, payoff, clean ending, subtitle sync/readability and stable speaker framing.</p>
  </header>
  <section class="summary">
    <div class="counts">
      <span>total clips: <strong id="visibleCount">{total_count}</strong> / {total_count}</span>
      <span>reviewed clips: {reviewed_count}</span>
      <span>unreviewed clips: {total_count - reviewed_count}</span>
      <span>missing video files: {missing_video_count}</span>
    </div>
    <p><strong>case_id counts:</strong> {html.escape(case_summary or '-')}</p>
    <p><strong>scenario/content_type counts:</strong> {html.escape(scenario_summary or '-')}</p>
  </section>
  <section class="toolbar">
    <div class="toolbar-grid">
      <label>Search
        <input id="searchInput" type="search" placeholder="clip_id, transcript, reasons, video path">
      </label>
      <label>Status
        <select id="statusFilter">
          <option value="all">all</option>
          <option value="reviewed">reviewed</option>
          <option value="unreviewed">unreviewed</option>
        </select>
      </label>
      <label>Case
        <select id="caseFilter">
          <option value="all">all cases</option>
          {case_options}
        </select>
      </label>
      <label>Scenario / content type
        <select id="scenarioFilter">
          <option value="all">all scenarios</option>
          {scenario_options}
        </select>
      </label>
      <label>Sort
        <select id="sortControl">
          <option value="score_desc">score descending</option>
          <option value="score_asc">score ascending</option>
          <option value="start_asc">start time ascending</option>
          <option value="start_desc">start time descending</option>
          <option value="duration_desc">duration</option>
          <option value="reviewed_first">reviewed/unreviewed</option>
          <option value="unreviewed_first">unreviewed/reviewed</option>
        </select>
      </label>
    </div>
  </section>
  <main id="clipList">
  {body}
  </main>
  <script>
    const clipList = document.getElementById('clipList');
    const clips = Array.from(document.querySelectorAll('.clip'));
    const searchInput = document.getElementById('searchInput');
    const statusFilter = document.getElementById('statusFilter');
    const caseFilter = document.getElementById('caseFilter');
    const scenarioFilter = document.getElementById('scenarioFilter');
    const sortControl = document.getElementById('sortControl');
    const visibleCount = document.getElementById('visibleCount');

    function shellQuote(value) {{
      const text = String(value || '');
      if (/^[a-zA-Z0-9_./:@=-]+$/.test(text)) return text;
      return JSON.stringify(text);
    }}

    function boolText(value) {{
      return value ? 'true' : 'false';
    }}

    function buildReviewCommand(form) {{
      const clipId = form.dataset.clipId;
      const rating = form.querySelector('.review-rating').value;
      const goodClip = form.querySelector('.review-good-clip').checked;
      const boundaryIssue = form.querySelector('.review-boundary-issue').checked;
      const boringSetup = form.querySelector('.review-boring-setup').checked;
      const noPayoff = form.querySelector('.review-no-payoff').checked;
      const tooContextDependent = form.querySelector('.review-too-context-dependent').checked;
      const notes = form.querySelector('.review-notes').value;
      return [
        'python review_dashboard.py add-review',
        '--clip-id ' + shellQuote(clipId),
        '--rating ' + shellQuote(rating),
        '--good-clip ' + boolText(goodClip),
        '--boundary-issue ' + boolText(boundaryIssue),
        '--boring-setup ' + boolText(boringSetup),
        '--no-payoff ' + boolText(noPayoff),
        '--too-context-dependent ' + boolText(tooContextDependent),
        '--notes ' + shellQuote(notes)
      ].join(' ');
    }}

    function updateReviewCommand(form) {{
      form.querySelector('.review-command').value = buildReviewCommand(form);
    }}

    document.querySelectorAll('.review-form').forEach((form) => {{
      form.addEventListener('input', () => updateReviewCommand(form));
      form.addEventListener('change', () => updateReviewCommand(form));
      updateReviewCommand(form);
    }});

    function copyText(text) {{
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(text);
      }} else {{
        const textarea = document.createElement('textarea');
        textarea.value = text;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        textarea.remove();
      }}
    }}

    document.addEventListener('click', (event) => {{
      const copyButton = event.target.closest('[data-copy]');
      if (copyButton) {{
        copyText(copyButton.dataset.copy || '');
      }}
      const commandButton = event.target.closest('.copy-review-command');
      if (commandButton) {{
        const form = commandButton.closest('.review-form');
        updateReviewCommand(form);
        copyText(form.querySelector('.review-command').value);
      }}
    }});

    function applyFiltersAndSort() {{
      const query = searchInput.value.trim().toLowerCase();
      const status = statusFilter.value;
      const caseValue = caseFilter.value;
      const scenarioValue = scenarioFilter.value;
      let visible = 0;

      clips.forEach((clip) => {{
        const matchesQuery = !query || clip.dataset.search.includes(query);
        const matchesStatus = status === 'all' || clip.dataset.status === status;
        const matchesCase = caseValue === 'all' || clip.dataset.case === caseValue;
        const matchesScenario = scenarioValue === 'all' || clip.dataset.scenarioLabel === scenarioValue;
        const show = matchesQuery && matchesStatus && matchesCase && matchesScenario;
        clip.hidden = !show;
        if (show) visible += 1;
      }});

      const sorted = [...clips].sort((left, right) => {{
        const sort = sortControl.value;
        if (sort === 'score_asc') return Number(left.dataset.score) - Number(right.dataset.score);
        if (sort === 'score_desc') return Number(right.dataset.score) - Number(left.dataset.score);
        if (sort === 'start_asc') return Number(left.dataset.start) - Number(right.dataset.start);
        if (sort === 'start_desc') return Number(right.dataset.start) - Number(left.dataset.start);
        if (sort === 'duration_desc') return Number(right.dataset.duration) - Number(left.dataset.duration);
        if (sort === 'reviewed_first') return left.dataset.status.localeCompare(right.dataset.status);
        if (sort === 'unreviewed_first') return right.dataset.status.localeCompare(left.dataset.status);
        return 0;
      }});
      sorted.forEach((clip) => clipList.appendChild(clip));
      visibleCount.textContent = String(visible);
    }}

    [searchInput, statusFilter, caseFilter, scenarioFilter, sortControl].forEach((control) => {{
      control.addEventListener('input', applyFiltersAndSort);
      control.addEventListener('change', applyFiltersAndSort);
    }});
    applyFiltersAndSort();
  </script>
</body>
</html>
"""


def export_html(args: argparse.Namespace) -> None:
    clips = collect_clips(Path(args.results), Path(args.template), Path(args.reviews))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html(clips, output_path), encoding="utf-8")
    print(f"exported_html={output_path} clips={len(clips)}")


def list_clips(args: argparse.Namespace) -> None:
    clips = collect_clips(Path(args.results), Path(args.template), Path(args.reviews))
    print_clip_list(clips, limit=args.limit)


def add_review(args: argparse.Namespace) -> None:
    payload = build_review_payload(args)
    append_review(Path(args.reviews), payload)
    print(f"review_saved clip_id={payload['clip_id']} path={args.reviews}")


def summarize_review_file(
    *,
    results_path: Path,
    template_path: Path,
    reviews_path: Path,
) -> dict[str, Any]:
    reviews = load_jsonl_reviews(reviews_path)
    clips = collect_clips(results_path, template_path, reviews_path)
    clip_lookup = {clip.clip_id: clip for clip in clips}
    by_case: dict[str, dict[str, Any]] = {}
    by_scenario: dict[str, dict[str, Any]] = {}
    unrouted = 0

    for review in reviews:
        clip = clip_lookup.get(str(review.get("clip_id") or "").strip())
        if clip is None:
            unrouted += 1
            continue
        for bucket, key in (
            (by_case, clip.case_id),
            (by_scenario, clip.scenario_id),
        ):
            entry = bucket.setdefault(
                key,
                {
                    "count": 0,
                    "rating_total": 0.0,
                    "good_clip_count": 0,
                    "boundary_issue_count": 0,
                    "no_payoff_count": 0,
                    "too_context_dependent_count": 0,
                },
            )
            entry["count"] += 1
            entry["rating_total"] += float(review.get("rating") or 0.0)
            entry["good_clip_count"] += 1 if bool(review.get("good_clip")) else 0
            entry["boundary_issue_count"] += 1 if bool(review.get("boundary_issue")) else 0
            entry["no_payoff_count"] += 1 if bool(review.get("no_payoff")) else 0
            entry["too_context_dependent_count"] += 1 if bool(review.get("too_context_dependent")) else 0

    def finalize(bucket: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        payload: dict[str, dict[str, Any]] = {}
        for key, value in sorted(bucket.items()):
            count = int(value.get("count") or 0)
            rating_total = float(value.get("rating_total") or 0.0)
            payload[key] = {
                "count": count,
                "average_rating": round(rating_total / count, 4) if count else 0.0,
                "good_clip_ratio": round(float(value.get("good_clip_count") or 0) / count, 4) if count else 0.0,
                "boundary_issue_count": int(value.get("boundary_issue_count") or 0),
                "no_payoff_count": int(value.get("no_payoff_count") or 0),
                "too_context_dependent_count": int(value.get("too_context_dependent_count") or 0),
            }
        return payload

    ratings = [float(review.get("rating") or 0.0) for review in reviews if review.get("rating") is not None]

    return {
        "review_count": len(reviews),
        "average_rating": round(sum(ratings) / len(ratings), 4) if ratings else 0.0,
        "flag_counts": summarize_review_flags(reviews),
        "note_keyword_counts": summarize_review_note_keywords(reviews),
        "by_case": finalize(by_case),
        "by_scenario": finalize(by_scenario),
        "clips_in_results": len(clips),
        "unmatched_review_count": unrouted,
    }


def print_review_summary(args: argparse.Namespace) -> None:
    summary = summarize_review_file(
        results_path=Path(args.results),
        template_path=Path(args.template),
        reviews_path=Path(args.reviews),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local human-review dashboard for podcast cutter clips.")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="List clips discovered from benchmark results.")
    list_parser.add_argument("--results", default=str(DEFAULT_RESULTS_PATH))
    list_parser.add_argument("--template", default=str(DEFAULT_TEMPLATE_PATH))
    list_parser.add_argument("--reviews", default=str(DEFAULT_REVIEWS_PATH))
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.set_defaults(func=list_clips)

    export_parser = subparsers.add_parser("export-html", help="Export a standalone HTML review dashboard.")
    export_parser.add_argument("--results", default=str(DEFAULT_RESULTS_PATH))
    export_parser.add_argument("--template", default=str(DEFAULT_TEMPLATE_PATH))
    export_parser.add_argument("--reviews", default=str(DEFAULT_REVIEWS_PATH))
    export_parser.add_argument("--output", default=str(DEFAULT_HTML_PATH))
    export_parser.set_defaults(func=export_html)

    review_parser = subparsers.add_parser("add-review", help="Append one human review row to JSONL.")
    review_parser.add_argument("--clip-id", required=True)
    review_parser.add_argument("--reviews", default=str(DEFAULT_REVIEWS_PATH))
    review_parser.add_argument("--rating", required=True, type=int, choices=range(1, 6))
    review_parser.add_argument("--good-clip", type=parse_bool, default=False)
    review_parser.add_argument("--boundary-issue", type=parse_bool, default=False)
    review_parser.add_argument("--boring-setup", type=parse_bool, default=False)
    review_parser.add_argument("--no-payoff", type=parse_bool, default=False)
    review_parser.add_argument("--too-context-dependent", type=parse_bool, default=False)
    review_parser.add_argument("--notes", default="")
    review_parser.set_defaults(func=add_review)

    summary_parser = subparsers.add_parser("summary", help="Summarize JSONL human reviews.")
    summary_parser.add_argument("--results", default=str(DEFAULT_RESULTS_PATH))
    summary_parser.add_argument("--template", default=str(DEFAULT_TEMPLATE_PATH))
    summary_parser.add_argument("--reviews", default=str(DEFAULT_REVIEWS_PATH))
    summary_parser.set_defaults(func=print_review_summary)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
