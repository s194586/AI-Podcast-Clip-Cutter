from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .clips import PROJECT_ROOT, record_render_result, validate_adjusted_bounds


VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".webm")
SOURCE_VIDEO_FILENAMES = ("source.mp4", "source.mov", "source.mkv", "source.webm")
FORMAT_VARIANT_RE = re.compile(r"\.f\d+$", re.IGNORECASE)


class RenderError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 500, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def to_detail(self) -> dict[str, Any]:
        return {"message": self.message, **self.details}


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._") or "clip"


def _relative(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def locate_source_video(project_root: Path = PROJECT_ROOT) -> Path | None:
    input_dir = project_root / "input"
    for filename in SOURCE_VIDEO_FILENAMES:
        path = input_dir / filename
        if path.is_file():
            return path
    return None


def _is_format_variant(path: Path) -> bool:
    return bool(FORMAT_VARIANT_RE.search(path.stem))


def _preferred_video_candidate(candidates: list[Path]) -> Path | None:
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda path: (
            not _is_format_variant(path),
            path.suffix.lower() == ".mp4",
            path.stat().st_size,
            path.stat().st_mtime,
        ),
    )


def locate_input_video(project_root: Path = PROJECT_ROOT) -> Path | None:
    source_video = locate_source_video(project_root)
    if source_video is not None:
        return source_video

    input_dir = project_root / "input"
    if not input_dir.exists():
        return None
    candidates: list[Path] = []
    for extension in VIDEO_EXTENSIONS:
        candidates.extend(input_dir.glob(f"*{extension}"))
    candidates = [path for path in candidates if path.is_file()]
    return _preferred_video_candidate(candidates)


def locate_transcript(project_root: Path = PROJECT_ROOT) -> Path | None:
    candidates = [
        project_root / "transcripts" / "final_transcript.json",
        project_root / "metadata" / "transcript.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _run_command(command: list[str], project_root: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=str(project_root), capture_output=True, text=True)
    if completed.returncode != 0:
        command_text = " ".join(command)
        raise RenderError(
            f"Render command failed: {Path(command[1]).name if len(command) > 1 else command[0]}",
            details={
                "command": command_text,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            },
        )
    return completed


def _prepare_cutting_log(render_dir: Path, project_root: Path) -> Path:
    output_log = render_dir / "cutting_logic.json"
    source_log = project_root / "metadata" / "cutting_logic.json"
    if source_log.exists():
        shutil.copy2(source_log, output_log)
    else:
        output_log.write_text("{}\n", encoding="utf-8")
    return output_log


def _write_adjusted_window(render_dir: Path, clip: dict[str, Any], start: float, end: float, duration: float) -> Path:
    windows_path = render_dir / "editor_window.json"
    payload = [
        {
            "start": start,
            "end": end,
            "duration": duration,
            "summary": clip.get("summary", ""),
            "text": clip.get("text", ""),
            "editor_clip_id": clip["id"],
            "ai_start": clip["ai_start"],
            "ai_end": clip["ai_end"],
        }
    ]
    windows_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return windows_path


def render_adjusted_clip(
    clip: dict[str, Any],
    start: Any,
    end: Any,
    *,
    project_root: Path = PROJECT_ROOT,
    runtime_root: Path | None = None,
    project_id: int | str | None = None,
    source_video_path: Path | None = None,
) -> dict[str, Any]:
    script_root = Path(project_root)
    runtime_root = Path(runtime_root) if runtime_root is not None else script_root
    edited_start, edited_end, duration = validate_adjusted_bounds(clip, start, end)
    video_path = source_video_path if source_video_path is not None and source_video_path.is_file() else locate_input_video(runtime_root)
    if video_path is None:
        raise RenderError(
            "Missing source video. Put source.mp4, source.mov, source.mkv, or source.webm in input/ before rendering.",
            status_code=400,
        )

    transcript_path = locate_transcript(runtime_root)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    render_dir = runtime_root / "outputs" / "editor_renders" / f"{timestamp}_{_safe_name(clip['id'])}"
    raw_dir = render_dir / "raw"
    subtitles_dir = render_dir / "subtitles"
    render_dir.mkdir(parents=True, exist_ok=False)

    windows_path = _write_adjusted_window(render_dir, clip, edited_start, edited_end, duration)
    cutting_log_path = _prepare_cutting_log(render_dir, runtime_root)
    transcript_arg = transcript_path if transcript_path is not None else runtime_root / "transcripts" / "final_transcript.json"

    cutter_command = [
        sys.executable,
        str(script_root / "cutter.py"),
        "--video",
        str(video_path),
        "--windows",
        str(windows_path),
        "--transcript",
        str(transcript_arg),
        "--output-dir",
        str(raw_dir),
        "--cutting-log",
        str(cutting_log_path),
    ]
    cutter_result = _run_command(cutter_command, script_root)
    raw_outputs = sorted(raw_dir.glob("segment_*.mp4"))
    if not raw_outputs:
        raise RenderError(
            "cutter.py completed but did not create a segment_*.mp4 output.",
            details={"stdout": cutter_result.stdout[-4000:], "stderr": cutter_result.stderr[-4000:]},
        )

    warnings: list[str] = []
    subtitler_stdout = ""
    subtitler_stderr = ""
    subtitled_outputs: list[Path] = []

    if transcript_path is None:
        warnings.append("Transcript not found. Raw clip rendered; subtitles were skipped.")
    else:
        subtitler_command = [
            sys.executable,
            str(script_root / "subtitler.py"),
            "--transcript",
            str(transcript_path),
            "--input-dir",
            str(raw_dir),
            "--output-raw",
            str(raw_dir),
            "--output-subs",
            str(subtitles_dir),
        ]
        try:
            subtitler_result = _run_command(subtitler_command, script_root)
            subtitler_stdout = subtitler_result.stdout
            subtitler_stderr = subtitler_result.stderr
            subtitled_outputs = sorted(subtitles_dir.glob("segment_*.mp4"))
            if not subtitled_outputs:
                warnings.append("subtitler.py ran but did not create a subtitled output.")
        except RenderError as exc:
            warnings.append(f"Subtitle render failed: {exc.message}")
            subtitler_stdout = str(exc.details.get("stdout", ""))
            subtitler_stderr = str(exc.details.get("stderr", ""))

    status = "completed" if not warnings else "completed_with_warnings"
    result = {
        "status": status,
        "clip_id": clip["id"],
        "start": edited_start,
        "end": edited_end,
        "duration": duration,
        "output_dir": _relative(render_dir, script_root),
        "raw_outputs": [_relative(path, script_root) for path in raw_outputs],
        "subtitled_outputs": [_relative(path, script_root) for path in subtitled_outputs],
        "windows_file": _relative(windows_path, script_root),
        "cutting_log": _relative(cutting_log_path, script_root),
        "warnings": warnings,
        "logs": {
            "cutter_stdout": cutter_result.stdout[-4000:],
            "cutter_stderr": cutter_result.stderr[-4000:],
            "subtitler_stdout": subtitler_stdout[-4000:],
            "subtitler_stderr": subtitler_stderr[-4000:],
        },
    }
    result["clip"] = record_render_result(clip["id"], result, project_root=script_root, project_id=project_id)
    return result
