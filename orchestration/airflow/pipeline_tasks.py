"""Airflow task adapters over the reusable one-stage pipeline executor."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from apps.api.db.database import init_database, session_scope
from apps.api.db.models import utc_now
from apps.api.db.repositories import JobRepository, ProjectRepository
from apps.pipeline.airflow_config import AirflowRunConfig
from apps.pipeline.cancellation import CancellationToken
from apps.pipeline.context import PipelineContext
from apps.pipeline.events import PipelineEvent, progress_for_stage, redact_text
from apps.pipeline.exceptions import PipelineCancelled
from apps.pipeline.executor import PipelineStageExecutor
from apps.pipeline.registry import DEFAULT_STAGE_REGISTRY
from apps.pipeline.stages import ReviewCandidatesStage


DEFAULT_CONTAINER_PROJECT_ROOT = Path("/opt/ai-cutter")


class AirflowStageStateSink:
    def __init__(self, *, project_id: int, job_id: int, task_id: str) -> None:
        self.project_id = int(project_id)
        self.job_id = int(job_id)
        self.task_id = str(task_id)

    def __call__(self, event: PipelineEvent) -> None:
        init_database()
        with session_scope() as session:
            projects = ProjectRepository(session)
            jobs = JobRepository(session)
            project = projects.get(self.project_id)
            job = jobs.get(self.job_id)
            if project is None or job is None:
                return
            if project.status == "cancelled" or job.status == "cancelled" or job.cancel_requested:
                return

            stage = event.stage or project.current_stage or "waiting"
            progress = event.progress_percent
            if progress is None:
                progress = progress_for_stage(stage)
            if event.event in {
                "stage_started",
                "stage_progress",
                "stage_completed",
                "review_clip_started",
                "review_clip_completed",
                "review_clip_manual",
                "review_clip_failed",
            }:
                jobs.update_state(
                    job,
                    status="completed" if stage == "ready" and event.event == "stage_completed" else "running",
                    current_stage=stage,
                    progress=progress,
                    started_at=job.started_at or utc_now(),
                    finished_at=(utc_now() if stage == "ready" and event.event == "stage_completed" else None),
                    error_message=None,
                    airflow_state=("success" if stage == "ready" and event.event == "stage_completed" else "running"),
                    airflow_task_id=self.task_id,
                )
                if stage != "ready":
                    projects.update_flow_state(
                        project,
                        status="running",
                        current_stage=stage,
                        progress_percent=progress,
                        error_message=None,
                        started_at=project.started_at or utc_now(),
                        completed_at=None,
                    )


def execute_airflow_stage(
    run_config: dict[str, Any],
    stage_name: str,
    *,
    try_number: int = 1,
    max_attempts: int = 1,
    container_project_root: str | Path | None = None,
) -> dict[str, Any]:
    config = AirflowRunConfig.from_dict(run_config)
    task_id = str(stage_name)
    attempt = max(1, int(try_number))
    attempts = max(attempt, int(max_attempts))
    root = Path(
        container_project_root
        or os.environ.get("AIRFLOW_CONTAINER_ROOT")
        or DEFAULT_CONTAINER_PROJECT_ROOT
    ).resolve()
    cancellation = CancellationToken(
        external_check=lambda: _job_or_project_cancelled(config.project_id, config.job_id)
    )
    context = config.build_context(
        container_project_root=root,
        cancellation=cancellation,
    )
    stage_options = {"review_mode": "gemini"} if stage_name == "review_boundaries" else {}
    stage = DEFAULT_STAGE_REGISTRY.create(stage_name, **stage_options)
    sink = AirflowStageStateSink(
        project_id=config.project_id,
        job_id=config.job_id,
        task_id=task_id,
    )
    _mark_attempt(config.job_id, task_id, attempt, attempts)
    try:
        result = PipelineStageExecutor(event_sinks=(sink,)).execute(context, stage)
    except PipelineCancelled:
        _mark_cancelled(config.project_id, config.job_id)
        raise
    except Exception as exc:
        message = redact_text(str(exc).strip() or exc.__class__.__name__)
        if attempt < attempts:
            _mark_retrying(config.project_id, config.job_id, task_id, attempt, attempts, message)
        else:
            _mark_failed(config.project_id, config.job_id, task_id, attempt, attempts, message)
        raise

    return {
        "schema_version": 1,
        "project_id": config.project_id,
        "job_id": config.job_id,
        "stage": result.stage,
        "success": True,
        "skipped": bool(result.metadata.get("skipped")),
    }


def _mark_attempt(job_id: int, task_id: str, attempt: int, max_attempts: int) -> None:
    init_database()
    with session_scope() as session:
        job = JobRepository(session).get(job_id)
        if job is None or job.status == "cancelled" or job.cancel_requested:
            return
        JobRepository(session).update_state(
            job,
            status="running",
            airflow_state="running",
            airflow_task_id=task_id,
            airflow_try_number=attempt,
            airflow_max_tries=max_attempts - 1,
            started_at=job.started_at or utc_now(),
            error_message=None,
        )


def _mark_retrying(
    project_id: int,
    job_id: int,
    task_id: str,
    attempt: int,
    max_attempts: int,
    message: str,
) -> None:
    init_database()
    retry_message = (
        f"{task_id.replace('_', ' ').title()} will retry after attempt "
        f"{attempt} of {max_attempts}: {message}"
    )
    with session_scope() as session:
        projects = ProjectRepository(session)
        jobs = JobRepository(session)
        project = projects.get(project_id)
        job = jobs.get(job_id)
        if project is None or job is None or project.status == "cancelled" or job.cancel_requested:
            return
        jobs.update_state(
            job,
            status="running",
            current_stage=_runtime_stage(task_id),
            airflow_state="up_for_retry",
            airflow_task_id=task_id,
            airflow_try_number=attempt,
            airflow_max_tries=max_attempts - 1,
            error_message=retry_message,
        )
        projects.update_flow_state(
            project,
            status="running",
            current_stage=_runtime_stage(task_id),
            progress_percent=progress_for_stage(_runtime_stage(task_id)),
            error_message=retry_message,
            completed_at=None,
        )


def _mark_failed(
    project_id: int,
    job_id: int,
    task_id: str,
    attempt: int,
    max_attempts: int,
    message: str,
) -> None:
    init_database()
    now = utc_now()
    with session_scope() as session:
        projects = ProjectRepository(session)
        jobs = JobRepository(session)
        project = projects.get(project_id)
        job = jobs.get(job_id)
        if job is not None and (job.status == "cancelled" or job.cancel_requested):
            return
        if project is not None and project.status == "cancelled":
            return
        if job is not None:
            jobs.update_state(
                job,
                status="failed",
                current_stage=_runtime_stage(task_id),
                finished_at=now,
                error_message=message,
                airflow_state="failed",
                airflow_task_id=task_id,
                airflow_try_number=attempt,
                airflow_max_tries=max_attempts - 1,
            )
        if project is not None:
            projects.update_flow_state(
                project,
                status="failed",
                current_stage="failed",
                progress_percent=float(project.progress_percent or 0.0),
                error_message=message,
                completed_at=now,
            )


def _mark_cancelled(project_id: int, job_id: int) -> None:
    init_database()
    now = utc_now()
    with session_scope() as session:
        projects = ProjectRepository(session)
        jobs = JobRepository(session)
        project = projects.get(project_id)
        job = jobs.get(job_id)
        if job is not None:
            jobs.update_state(
                job,
                status="cancelled",
                current_stage="cancelled",
                finished_at=now,
                airflow_state="failed",
                cancel_requested=True,
                error_message="Cancelled by user.",
            )
        if project is not None:
            projects.update_flow_state(
                project,
                status="cancelled",
                current_stage="cancelled",
                progress_percent=float(project.progress_percent or 0.0),
                error_message="Cancelled by user.",
                completed_at=now,
            )


def _job_or_project_cancelled(project_id: int, job_id: int) -> bool:
    init_database()
    with session_scope() as session:
        project = ProjectRepository(session).get(project_id)
        job = JobRepository(session).get(job_id)
        return bool(
            project is None
            or job is None
            or project.status == "cancelled"
            or job.status == "cancelled"
            or job.cancel_requested
        )


def _runtime_stage(task_id: str) -> str:
    return {
        "prepare_workspace": "waiting",
        "download_source": "downloading",
        "transcribe": "transcribing",
        "validate_transcript": "validating_transcript",
        "generate_candidates": "generating_candidates",
        "import_candidates": "importing_candidates",
        "review_boundaries": "reviewing_with_ai",
        "mark_ready": "ready",
    }.get(task_id, task_id)


def review_candidates_with_gemini(config: dict[str, Any]) -> dict[str, Any]:
    """Compatibility helper retained for direct offline review tests."""

    project_id = int(config["project_id"])
    root = Path(os.environ.get("PODCAST_CUTTER_PROJECT_ROOT", Path(__file__).resolve().parents[2])).resolve()
    context = PipelineContext(
        project_id=project_id,
        source_url=None,
        workspace_path=root / "data" / "projects" / str(project_id) / "workspace",
        repository_root=root,
        auto_review=True,
        analysis_only=True,
    )
    result = PipelineStageExecutor().execute(
        context,
        ReviewCandidatesStage(review_mode=str(config.get("clip_review_mode") or "gemini")),
    )
    output = dict(config)
    output["review_summary"] = dict(result.metadata)
    return output
