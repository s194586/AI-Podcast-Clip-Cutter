"""Optional Airflow adapters over the reusable pipeline services.

This module intentionally imports without Apache Airflow installed. The release does
not add or enable Airflow; the existing DAG placeholder can call these thin helpers.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from apps.api.db.database import init_database, session_scope
from apps.api.db.models import utc_now
from apps.api.db.repositories import JobRepository, ProjectRepository
from apps.api.services.project_service import ensure_project_workspace, safe_relative_path
from apps.pipeline.config import PipelineConfig
from apps.pipeline.context import PipelineContext
from apps.pipeline.events import progress_for_stage
from apps.pipeline.stages import (
    DownloadMediaStage,
    GenerateCandidatesStage,
    ImportCandidatesStage,
    MarkProjectReadyStage,
    ReviewCandidatesStage,
    TranscribeAudioStage,
    ValidateTranscriptStage,
)


PROJECT_ROOT = Path(
    os.environ.get("PODCAST_CUTTER_PROJECT_ROOT", Path(__file__).resolve().parents[2])
).resolve()


def validate_project_config(dag_conf: dict[str, Any] | None = None) -> dict[str, Any]:
    config = dict(dag_conf or {})
    source_url = str(config.get("source_url") or "").strip()
    project_id = config.get("project_id")
    init_database()
    with session_scope() as session:
        repository = ProjectRepository(session)
        if project_id is None:
            if not source_url:
                raise ValueError("DAG config must include project_id or source_url.")
            project = repository.create(
                source_url=source_url,
                title=config.get("title"),
                status="queued",
                current_stage="waiting",
            )
        else:
            project = repository.get(int(project_id))
            if project is None:
                raise ValueError(f"Unknown project_id: {project_id}")
            if source_url:
                project.source_url = source_url
            repository.update_flow_state(
                project,
                status="queued",
                current_stage="waiting",
                progress_percent=0.0,
                error_message=None,
                completed_at=None,
            )
        workspace = ensure_project_workspace(project.id, project_root=PROJECT_ROOT)
        project.workspace_path = safe_relative_path(workspace, project_root=PROJECT_ROOT)
        repository.touch(project)
        config.update(
            {
                "project_id": project.id,
                "source_url": project.source_url,
                "workspace_dir": str(workspace),
                "project_root": str(PROJECT_ROOT),
                "clip_review_mode": str(
                    config.get("clip_review_mode")
                    or os.environ.get("CLIP_REVIEW_MODE")
                    or "gemini"
                ),
            }
        )
    return config


def download_media(config: dict[str, Any]) -> dict[str, Any]:
    return _run_stage(config, DownloadMediaStage())


def transcribe_audio(config: dict[str, Any]) -> dict[str, Any]:
    return _run_stage(config, TranscribeAudioStage())


def generate_candidates(config: dict[str, Any]) -> dict[str, Any]:
    context = _context(config)
    if not context.config.skip_subtitle_checker:
        _mark_running(context.project_id, "validating_transcript")
        ValidateTranscriptStage().run(context)
    _mark_running(context.project_id, "generating_candidates")
    result = GenerateCandidatesStage().run(context)
    config["candidate_result"] = _result_dict(result)
    return config


def import_candidates_to_sqlite(config: dict[str, Any]) -> dict[str, Any]:
    return _run_stage(config, ImportCandidatesStage())


def review_candidates_with_gemini(config: dict[str, Any]) -> dict[str, Any]:
    context = _context(config, auto_review=True)
    _mark_running(context.project_id, "reviewing_with_ai")
    stage = ReviewCandidatesStage(review_mode=str(config.get("clip_review_mode") or "gemini"))
    result = stage.run(context)
    config["review_summary"] = dict(result.metadata)
    return config


def review_top_candidates(config: dict[str, Any]) -> dict[str, Any]:
    return review_candidates_with_gemini(config)


def mark_project_ready(config: dict[str, Any]) -> dict[str, Any]:
    MarkProjectReadyStage().run(_context(config))
    return config


def mark_project_status(project_id: int, status: str, error_message: str | None = None) -> None:
    normalized = str(status or "waiting")
    stage_aliases = {
        "processing": "downloading",
        "analyzing": "generating_candidates",
        "reviewing": "importing_candidates",
    }
    stage = stage_aliases.get(normalized, normalized)
    init_database()
    with session_scope() as session:
        repository = ProjectRepository(session)
        project = repository.get(int(project_id))
        if project is None:
            raise ValueError(f"Unknown project_id: {project_id}")
        failed = stage == "failed"
        repository.update_flow_state(
            project,
            status="failed" if failed else ("ready" if stage == "ready" else "running"),
            current_stage=stage,
            progress_percent=(
                float(project.progress_percent or 0.0)
                if failed
                else float(progress_for_stage(stage) or 0.0)
            ),
            error_message=error_message,
            completed_at=utc_now() if stage in {"ready", "failed"} else None,
        )
        if failed and error_message:
            JobRepository(session).create(
                project_id=project.id,
                job_type="airflow_pipeline",
                status="failed",
                stage=stage,
                current_stage=stage,
                progress=float(project.progress_percent or 0.0),
                error_message=error_message,
            )


def mark_project_failed(project_id: int, error_message: str) -> None:
    mark_project_status(project_id, "failed", error_message=error_message)


def _context(config: dict[str, Any], *, auto_review: bool | None = None) -> PipelineContext:
    project_id = int(config["project_id"])
    root = Path(config.get("project_root") or PROJECT_ROOT).resolve()
    workspace = Path(
        config.get("workspace_dir")
        or ensure_project_workspace(project_id, project_root=root)
    ).resolve()
    options = PipelineConfig(
        ai_mode="local_only",
        subtitle_checker_mode=str(config.get("subtitle_checker_mode") or "local_only"),
        skip_subtitle_checker=bool(config.get("skip_subtitle_checker", False)),
        transcription_backend=str(config.get("transcription_backend") or "faster_whisper"),
        whisper_model=str(config.get("whisper_model") or "small"),
        transcription_device=str(config.get("transcription_device") or "auto"),
        transcription_compute_type=str(config.get("transcription_compute_type") or "auto"),
    )
    return PipelineContext(
        project_id=project_id,
        source_url=str(config.get("source_url") or "") or None,
        workspace_path=workspace,
        repository_root=root,
        auto_review=bool(True if auto_review is None else auto_review),
        analysis_only=True,
        config=options,
    )


def _run_stage(config: dict[str, Any], stage) -> dict[str, Any]:
    context = _context(config)
    _mark_running(context.project_id, stage.stage)
    result = stage.run(context)
    config[f"{stage.stage}_result"] = _result_dict(result)
    return config


def _mark_running(project_id: int | None, stage: str) -> None:
    if project_id is None:
        raise ValueError("Pipeline stage requires project_id.")
    mark_project_status(project_id, stage)


def _result_dict(result) -> dict[str, Any]:
    return {
        "stage": result.stage,
        "success": result.success,
        "message": result.message,
        "produced_artifacts": list(result.produced_artifacts),
        **dict(result.metadata),
    }
