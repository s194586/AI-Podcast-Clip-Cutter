from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Artifact, Clip, ClipEvaluation, Job, Project, utc_now

_UNSET = object()


class ProjectRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def count(self) -> int:
        return int(self.session.scalar(select(func.count(Project.id))) or 0)

    def create(
        self,
        *,
        source_url: str = "",
        title: str | None = None,
        status: str = "draft",
        current_stage: str = "waiting",
        progress_percent: float = 0.0,
        workspace_path: str | None = None,
        error_message: str | None = None,
        auto_review: bool = True,
        source_video_path: str | None = None,
        transcript_path: str | None = None,
        candidate_source_path: str | None = None,
    ) -> Project:
        project = Project(
            source_url=source_url,
            title=title,
            status=status,
            current_stage=current_stage,
            progress_percent=float(progress_percent),
            workspace_path=workspace_path,
            error_message=error_message,
            auto_review=bool(auto_review),
            source_video_path=source_video_path,
            transcript_path=transcript_path,
            candidate_source_path=candidate_source_path,
        )
        self.session.add(project)
        self.session.flush()
        return project

    def get(self, project_id: int) -> Project | None:
        return self.session.get(Project, project_id)

    def get_default(self) -> Project | None:
        return self.session.scalars(select(Project).order_by(Project.id.asc()).limit(1)).first()

    def list_newest(self) -> list[Project]:
        return list(self.session.scalars(select(Project).order_by(Project.updated_at.desc(), Project.id.desc())).all())

    def clip_count(self, project_id: int) -> int:
        return int(self.session.scalar(select(func.count(Clip.id)).where(Clip.project_id == project_id)) or 0)

    def accepted_clip_count(self, project_id: int) -> int:
        return int(
            self.session.scalar(
                select(func.count(Clip.id)).where(Clip.project_id == project_id, Clip.status == "accepted")
            )
            or 0
        )

    def touch(self, project: Project) -> None:
        project.updated_at = utc_now()

    def update_flow_state(
        self,
        project: Project,
        *,
        status: str | None = None,
        current_stage: str | None = None,
        progress_percent: float | None = None,
        error_message: Any = _UNSET,
        started_at: Any = _UNSET,
        completed_at: Any = _UNSET,
    ) -> None:
        if status is not None:
            project.status = status
        if current_stage is not None:
            project.current_stage = current_stage
        if progress_percent is not None:
            project.progress_percent = float(progress_percent)
        if error_message is not _UNSET:
            project.error_message = error_message
        if started_at is not _UNSET:
            project.started_at = started_at
        if completed_at is not _UNSET:
            project.completed_at = completed_at
        self.touch(project)


class ClipRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_from_dict(self, project_id: int, payload: dict[str, Any]) -> Clip:
        clip = Clip(
            project_id=project_id,
            external_id=str(payload["id"]),
            clip_index=int(payload["index"]),
            ai_start=float(payload["ai_start"]),
            ai_end=float(payload["ai_end"]),
            reviewed_start=float(payload["reviewed_start"]) if payload.get("reviewed_start") is not None else None,
            reviewed_end=float(payload["reviewed_end"]) if payload.get("reviewed_end") is not None else None,
            edited_start=float(payload["edited_start"]),
            edited_end=float(payload["edited_end"]),
            boundary_source=str(payload.get("boundary_source") or "heuristic"),
            min_start=float(payload["min_start"]),
            max_start=float(payload["max_start"]),
            min_end=float(payload["min_end"]),
            max_end=float(payload["max_end"]),
            status=str(payload.get("status") or "draft"),
            render_status=str(payload.get("render_status") or "not_rendered"),
            summary=str(payload.get("summary") or ""),
            text=str(payload.get("text") or ""),
            source=payload.get("source"),
            candidate_id=str(payload["candidate_id"]) if payload.get("candidate_id") is not None else None,
            selection_source=payload.get("selection_source"),
            local_score=float(payload["local_score"]) if payload.get("local_score") is not None else None,
            local_rank=int(payload["local_rank"]) if payload.get("local_rank") is not None else None,
            selection_reasons=list(payload.get("selection_reasons") or []),
            local_features=dict(payload.get("local_features") or {}),
            raw_outputs=list(payload.get("raw_outputs") or []),
            subtitled_outputs=list(payload.get("subtitled_outputs") or []),
            last_render_output_dir=payload.get("last_render_output_dir"),
            last_render_warnings=list(payload.get("last_render_warnings") or []),
        )
        self.session.add(clip)
        self.session.flush()
        return clip

    def get_by_external_id(self, project_id: int, external_id: str) -> Clip | None:
        return self.session.scalars(
            select(Clip).where(Clip.project_id == project_id, Clip.external_id == external_id).limit(1)
        ).first()

    def list_for_project(self, project_id: int) -> list[Clip]:
        return list(
            self.session.scalars(
                select(Clip).where(Clip.project_id == project_id).order_by(Clip.clip_index.asc(), Clip.id.asc())
            ).all()
        )

    def touch(self, clip: Clip) -> None:
        clip.updated_at = utc_now()


class JobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        project_id: int,
        job_type: str,
        status: str = "draft",
        stage: str | None = None,
        progress: float = 0.0,
        current_stage: str | None = None,
        process_id: int | None = None,
        log_path: str | None = None,
        started_at: Any = None,
        finished_at: Any = None,
        exit_code: int | None = None,
        error_message: str | None = None,
        orchestrator_type: str = "local",
        airflow_dag_id: str | None = None,
        airflow_dag_run_id: str | None = None,
        airflow_state: str | None = None,
        airflow_task_id: str | None = None,
        airflow_try_number: int | None = None,
        airflow_max_tries: int | None = None,
        cancel_requested: bool = False,
    ) -> Job:
        job = Job(
            project_id=project_id,
            job_type=job_type,
            status=status,
            stage=stage,
            current_stage=current_stage or stage,
            progress=progress,
            process_id=process_id,
            log_path=log_path,
            started_at=started_at,
            finished_at=finished_at,
            exit_code=exit_code,
            error_message=error_message,
            orchestrator_type=orchestrator_type,
            airflow_dag_id=airflow_dag_id,
            airflow_dag_run_id=airflow_dag_run_id,
            airflow_state=airflow_state,
            airflow_task_id=airflow_task_id,
            airflow_try_number=airflow_try_number,
            airflow_max_tries=airflow_max_tries,
            cancel_requested=cancel_requested,
        )
        self.session.add(job)
        self.session.flush()
        return job

    def get(self, job_id: int) -> Job | None:
        return self.session.get(Job, job_id)

    def latest_for_project(self, project_id: int, job_type: str | None = None) -> Job | None:
        statement = select(Job).where(Job.project_id == project_id)
        if job_type is not None:
            statement = statement.where(Job.job_type == job_type)
        return self.session.scalars(statement.order_by(Job.created_at.desc(), Job.id.desc()).limit(1)).first()

    def active_for_project(self, project_id: int, job_type: str | None = None) -> Job | None:
        statement = select(Job).where(Job.project_id == project_id, Job.status.in_(("queued", "running")))
        if job_type is not None:
            statement = statement.where(Job.job_type == job_type)
        return self.session.scalars(statement.order_by(Job.created_at.desc(), Job.id.desc()).limit(1)).first()

    def latest_for_project_types(self, project_id: int, job_types: tuple[str, ...]) -> Job | None:
        return self.session.scalars(
            select(Job)
            .where(Job.project_id == project_id, Job.job_type.in_(job_types))
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        ).first()

    def active_for_project_types(self, project_id: int, job_types: tuple[str, ...]) -> Job | None:
        return self.session.scalars(
            select(Job)
            .where(
                Job.project_id == project_id,
                Job.job_type.in_(job_types),
                Job.status.in_(("queued", "running")),
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        ).first()

    def list_active_types(self, job_types: tuple[str, ...]) -> list[Job]:
        return list(
            self.session.scalars(
                select(Job)
                .where(Job.job_type.in_(job_types), Job.status.in_(("queued", "running")))
                .order_by(Job.created_at.asc(), Job.id.asc())
            ).all()
        )

    def list_active(self, job_type: str | None = None) -> list[Job]:
        statement = select(Job).where(Job.status.in_(("queued", "running")))
        if job_type is not None:
            statement = statement.where(Job.job_type == job_type)
        return list(self.session.scalars(statement.order_by(Job.created_at.asc(), Job.id.asc())).all())

    def update_state(
        self,
        job: Job,
        *,
        status: str | None = None,
        current_stage: str | None = None,
        progress: float | None = None,
        process_id: Any = _UNSET,
        log_path: Any = _UNSET,
        started_at: Any = _UNSET,
        finished_at: Any = _UNSET,
        exit_code: Any = _UNSET,
        error_message: Any = _UNSET,
        orchestrator_type: Any = _UNSET,
        airflow_dag_id: Any = _UNSET,
        airflow_dag_run_id: Any = _UNSET,
        airflow_state: Any = _UNSET,
        airflow_task_id: Any = _UNSET,
        airflow_try_number: Any = _UNSET,
        airflow_max_tries: Any = _UNSET,
        cancel_requested: Any = _UNSET,
    ) -> None:
        if status is not None:
            job.status = status
        if current_stage is not None:
            job.current_stage = current_stage
            job.stage = current_stage
        if progress is not None:
            job.progress = float(progress)
        if process_id is not _UNSET:
            job.process_id = process_id
        if log_path is not _UNSET:
            job.log_path = log_path
        if started_at is not _UNSET:
            job.started_at = started_at
        if finished_at is not _UNSET:
            job.finished_at = finished_at
        if exit_code is not _UNSET:
            job.exit_code = exit_code
        if error_message is not _UNSET:
            job.error_message = error_message
        if orchestrator_type is not _UNSET:
            job.orchestrator_type = orchestrator_type
        if airflow_dag_id is not _UNSET:
            job.airflow_dag_id = airflow_dag_id
        if airflow_dag_run_id is not _UNSET:
            job.airflow_dag_run_id = airflow_dag_run_id
        if airflow_state is not _UNSET:
            job.airflow_state = airflow_state
        if airflow_task_id is not _UNSET:
            job.airflow_task_id = airflow_task_id
        if airflow_try_number is not _UNSET:
            job.airflow_try_number = airflow_try_number
        if airflow_max_tries is not _UNSET:
            job.airflow_max_tries = airflow_max_tries
        if cancel_requested is not _UNSET:
            job.cancel_requested = bool(cancel_requested)
        job.updated_at = utc_now()

    def latest_failed_error(self, project_id: int) -> str | None:
        job = self.session.scalars(
            select(Job)
            .where(Job.project_id == project_id, Job.status == "failed", Job.error_message.is_not(None))
            .order_by(Job.updated_at.desc(), Job.id.desc())
            .limit(1)
        ).first()
        return job.error_message if job is not None else None


class ClipEvaluationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        project_id: int,
        external_clip_id: str,
        decision: str,
        quality_score: float,
        context_score: float,
        hook_score: float,
        payoff_score: float,
        boundary_score: float,
        privacy_risk: str,
        recommended_action: str,
        suggested_start: float | None,
        suggested_end: float | None,
        crop_advice: str,
        needs_more_context: bool,
        reasons_json: list[Any],
        warnings_json: list[Any],
        raw_result_json: dict[str, Any],
        clip_id: int | None = None,
        provider: str = "local_stub",
        model: str | None = None,
        selected_start_segment_id: str | None = None,
        selected_end_segment_id: str | None = None,
        reviewed_start: float | None = None,
        reviewed_end: float | None = None,
        start_delta_seconds: float | None = None,
        end_delta_seconds: float | None = None,
        reasoning_summary: str = "",
        start_reason: str = "",
        end_reason: str = "",
        context_seconds: float | None = None,
    ) -> ClipEvaluation:
        evaluation = ClipEvaluation(
            project_id=project_id,
            clip_id=clip_id,
            external_clip_id=external_clip_id,
            provider=provider,
            model=model,
            decision=decision,
            quality_score=quality_score,
            context_score=context_score,
            hook_score=hook_score,
            payoff_score=payoff_score,
            boundary_score=boundary_score,
            privacy_risk=privacy_risk,
            recommended_action=recommended_action,
            selected_start_segment_id=selected_start_segment_id,
            selected_end_segment_id=selected_end_segment_id,
            suggested_start=suggested_start,
            suggested_end=suggested_end,
            reviewed_start=reviewed_start,
            reviewed_end=reviewed_end,
            start_delta_seconds=start_delta_seconds,
            end_delta_seconds=end_delta_seconds,
            reasoning_summary=reasoning_summary,
            start_reason=start_reason,
            end_reason=end_reason,
            context_seconds=context_seconds,
            crop_advice=crop_advice,
            needs_more_context=needs_more_context,
            reasons_json=list(reasons_json or []),
            warnings_json=list(warnings_json or []),
            raw_result_json=dict(raw_result_json or {}),
        )
        self.session.add(evaluation)
        self.session.flush()
        return evaluation

    def latest_for_clip(self, project_id: int, external_clip_id: str) -> ClipEvaluation | None:
        return self.session.scalars(
            select(ClipEvaluation)
            .where(
                ClipEvaluation.project_id == project_id,
                ClipEvaluation.external_clip_id == external_clip_id,
            )
            .order_by(ClipEvaluation.created_at.desc(), ClipEvaluation.id.desc())
            .limit(1)
        ).first()


class ArtifactRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        project_id: int,
        artifact_type: str,
        path: str,
        clip_id: int | None = None,
        filename: str | None = None,
        media_type: str | None = None,
    ) -> Artifact:
        artifact = Artifact(
            project_id=project_id,
            clip_id=clip_id,
            artifact_type=artifact_type,
            path=path,
            filename=filename or Path(path).name,
            media_type=media_type,
        )
        self.session.add(artifact)
        self.session.flush()
        return artifact

    def list_for_project(self, project_id: int) -> list[Artifact]:
        return list(self.session.scalars(select(Artifact).where(Artifact.project_id == project_id)).all())

    def list_for_clip(self, clip_id: int) -> list[Artifact]:
        return list(self.session.scalars(select(Artifact).where(Artifact.clip_id == clip_id)).all())
