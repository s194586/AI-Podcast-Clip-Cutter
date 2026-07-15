from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(os.environ.get("PODCAST_CUTTER_PROJECT_ROOT", Path(__file__).resolve().parents[2])).resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.api.db.database import init_database, session_scope
from apps.api.db.repositories import ClipRepository, JobRepository, ProjectRepository
from apps.api.services.clips import _load_clips_from_candidate_files
from apps.review_agent.service import ReviewAgentService


MEDIA_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm", ".mp3", ".wav", ".m4a")


def validate_project_config(dag_conf: dict[str, Any] | None = None) -> dict[str, Any]:
    config = dict(dag_conf or {})
    source_url = str(config.get("source_url") or "").strip()
    project_id = config.get("project_id")

    init_database()
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        if project_id is None:
            if not source_url:
                raise ValueError("DAG config must include project_id or source_url.")
            project = project_repo.create(source_url=source_url, title=config.get("title"), status="queued")
        else:
            project = project_repo.get(int(project_id))
            if project is None:
                raise ValueError(f"Unknown project_id: {project_id}")
            if source_url:
                project.source_url = source_url
            project.status = "queued"
            project_repo.touch(project)

        return {
            "project_id": project.id,
            "source_url": project.source_url,
            "project_root": str(PROJECT_ROOT),
            "top_n_review": int(config.get("top_n_review") or 5),
        }


def download_media(config: dict[str, Any]) -> dict[str, Any]:
    project_id = int(config["project_id"])
    source_url = str(config.get("source_url") or "").strip()
    mark_project_status(project_id, "processing")
    if not source_url:
        return config

    _run_command(
        [
            _python_executable(),
            "download_content.py",
            source_url,
            "--input",
            str(PROJECT_ROOT / "input"),
            "--metadata",
            str(PROJECT_ROOT / "metadata"),
        ]
    )
    latest_media = _find_latest_file(PROJECT_ROOT / "input", MEDIA_SUFFIXES)
    if latest_media is not None:
        _update_project_paths(project_id, source_video_path=_relative(latest_media))
    return config


def transcribe_audio(config: dict[str, Any]) -> dict[str, Any]:
    project_id = int(config["project_id"])
    mark_project_status(project_id, "transcribing")
    media_path = _find_latest_file(PROJECT_ROOT / "input", MEDIA_SUFFIXES)
    if media_path is None:
        raise FileNotFoundError("No media file found in input/.")

    transcript_path = PROJECT_ROOT / "transcripts" / "final_transcript.json"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    _run_command([_python_executable(), "transcribe.py", "--file", str(media_path), "--out", str(transcript_path)])
    _update_project_paths(project_id, transcript_path=_relative(transcript_path))
    return config


def generate_candidates(config: dict[str, Any]) -> dict[str, Any]:
    project_id = int(config["project_id"])
    mark_project_status(project_id, "analyzing")
    transcript_path = PROJECT_ROOT / "transcripts" / "final_transcript.json"
    heatmap_path = PROJECT_ROOT / "metadata" / "heatmap.json"
    output_path = PROJECT_ROOT / "top_windows.json"
    cutting_log_path = PROJECT_ROOT / "metadata" / "cutting_logic.json"
    if not transcript_path.exists():
        raise FileNotFoundError(f"Missing transcript: {transcript_path}")

    _run_command(
        [
            _python_executable(),
            "analyze_virals.py",
            "--transcript",
            str(transcript_path),
            "--heatmap",
            str(heatmap_path),
            "--save-json",
            str(output_path),
            "--cutting-log",
            str(cutting_log_path),
            "--ai-mode",
            "local_only",
        ]
    )
    _update_project_paths(project_id, candidate_source_path=_relative(output_path))
    return config


def import_candidates_to_sqlite(config: dict[str, Any]) -> dict[str, Any]:
    project_id = int(config["project_id"])
    mark_project_status(project_id, "reviewing")
    clips, source = _load_clips_from_candidate_files(PROJECT_ROOT)

    init_database()
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        clip_repo = ClipRepository(session)
        project = project_repo.get(project_id)
        if project is None:
            raise ValueError(f"Unknown project_id: {project_id}")
        project.candidate_source_path = source
        for clip_payload in clips:
            existing = clip_repo.get_by_external_id(project_id, str(clip_payload["id"]))
            if existing is None:
                clip_repo.create_from_dict(project_id, clip_payload)
                continue
            _update_clip_from_payload(existing, clip_payload)
            clip_repo.touch(existing)
        project_repo.touch(project)
    return config


def review_top_candidates(config: dict[str, Any]) -> dict[str, Any]:
    project_id = int(config["project_id"])
    top_n = max(1, int(config.get("top_n_review") or 5))
    mark_project_status(project_id, "reviewing")
    init_database()
    with session_scope() as session:
        clips = ClipRepository(session).list_for_project(project_id)
        external_ids = [
            clip.external_id
            for clip in sorted(
                clips,
                key=lambda clip: (
                    clip.local_rank is None,
                    clip.local_rank if clip.local_rank is not None else clip.clip_index,
                    clip.clip_index,
                ),
            )[:top_n]
        ]

    service = ReviewAgentService(project_root=PROJECT_ROOT, mode="local_only", use_langgraph=False)
    for external_id in external_ids:
        service.review_clip(project_id=project_id, clip_id=external_id)
    return config


def mark_project_ready(config: dict[str, Any]) -> dict[str, Any]:
    mark_project_status(int(config["project_id"]), "ready")
    return config


def mark_project_status(project_id: int, status: str, error_message: str | None = None) -> None:
    init_database()
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        project = project_repo.get(int(project_id))
        if project is None:
            raise ValueError(f"Unknown project_id: {project_id}")
        project.status = status
        project_repo.touch(project)
        if error_message:
            JobRepository(session).create(
                project_id=project.id,
                job_type="airflow_pipeline",
                status="failed",
                stage=status,
                progress=0.0,
                error_message=error_message,
            )


def mark_project_failed(project_id: int, error_message: str) -> None:
    mark_project_status(project_id, "failed", error_message=error_message)


def _update_project_paths(project_id: int, **paths: str | None) -> None:
    init_database()
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        project = project_repo.get(int(project_id))
        if project is None:
            raise ValueError(f"Unknown project_id: {project_id}")
        for key, value in paths.items():
            if value:
                setattr(project, key, value)
        project_repo.touch(project)


def _update_clip_from_payload(clip: Any, payload: dict[str, Any]) -> None:
    field_map = {
        "clip_index": "index",
        "ai_start": "ai_start",
        "ai_end": "ai_end",
        "edited_start": "edited_start",
        "edited_end": "edited_end",
        "min_start": "min_start",
        "max_start": "max_start",
        "min_end": "min_end",
        "max_end": "max_end",
        "summary": "summary",
        "text": "text",
        "source": "source",
        "candidate_id": "candidate_id",
        "selection_source": "selection_source",
        "local_score": "local_score",
        "local_rank": "local_rank",
        "selection_reasons": "selection_reasons",
        "local_features": "local_features",
    }
    for model_field, payload_field in field_map.items():
        if payload_field in payload:
            setattr(clip, model_field, payload[payload_field])


def _run_command(args: list[str]) -> None:
    try:
        subprocess.run(args, cwd=PROJECT_ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Pipeline command failed with exit code {exc.returncode}: {' '.join(args)}") from exc


def _python_executable() -> str:
    return os.environ.get("PODCAST_CUTTER_PYTHON") or sys.executable


def _find_latest_file(folder: Path, suffixes: tuple[str, ...]) -> Path | None:
    if not folder.exists():
        return None
    files = [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in suffixes]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
