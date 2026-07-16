from __future__ import annotations

from apps.api.db.database import init_database, session_scope
from apps.api.db.models import utc_now
from apps.api.db.repositories import ProjectRepository

from .context import PipelineContext
from .events import PipelineEvent, progress_for_stage


class ProjectStateEventSink:
    """Persists project state for direct entrypoint runs; jobs remain orchestrator-owned."""

    def __init__(self, context: PipelineContext) -> None:
        if context.project_id is None:
            raise ValueError("ProjectStateEventSink requires project_id.")
        self.context = context

    def __call__(self, event: PipelineEvent) -> None:
        project_id = int(self.context.project_id or 0)
        init_database()
        with session_scope() as session:
            repository = ProjectRepository(session)
            project = repository.get(project_id)
            if project is None:
                return
            if project.status == "cancelled" and event.event != "pipeline_cancelled":
                return

            if event.event in {
                "review_clip_started",
                "review_clip_completed",
                "review_clip_manual",
                "review_clip_failed",
            }:
                repository.update_flow_state(
                    project,
                    status="running",
                    current_stage="reviewing_with_ai",
                    progress_percent=event.progress_percent,
                    error_message=None,
                )
                return

            if event.event in {"stage_started", "stage_progress", "stage_completed"}:
                stage = event.stage or project.current_stage or "waiting"
                if stage not in {
                    "waiting",
                    "downloading",
                    "transcribing",
                    "validating_transcript",
                    "generating_candidates",
                    "importing_candidates",
                    "reviewing_with_ai",
                    "ready",
                }:
                    return
                progress = event.progress_percent
                if progress is None:
                    progress = progress_for_stage(stage)
                started_at = utc_now() if event.event == "stage_started" and stage == "waiting" else project.started_at
                repository.update_flow_state(
                    project,
                    status="ready" if stage == "ready" and event.event == "stage_completed" else "running",
                    current_stage=stage,
                    progress_percent=progress,
                    error_message=None,
                    started_at=started_at,
                    completed_at=(utc_now() if stage == "ready" and event.event == "stage_completed" else None),
                )
                return

            if event.event == "stage_failed":
                cancelled = event.error_category == "PipelineCancelled"
                repository.update_flow_state(
                    project,
                    status="cancelled" if cancelled else "failed",
                    current_stage="cancelled" if cancelled else "failed",
                    progress_percent=float(project.progress_percent or 0.0),
                    error_message=event.message,
                    completed_at=utc_now(),
                )
                return

            if event.event == "pipeline_cancelled":
                repository.update_flow_state(
                    project,
                    status="cancelled",
                    current_stage="cancelled",
                    progress_percent=float(project.progress_percent or 0.0),
                    error_message=event.message,
                    completed_at=utc_now(),
                )
                return

            if event.event == "pipeline_completed" and event.success:
                repository.update_flow_state(
                    project,
                    status="ready",
                    current_stage="ready",
                    progress_percent=100.0,
                    error_message=None,
                    completed_at=utc_now(),
                )
