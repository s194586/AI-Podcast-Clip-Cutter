from __future__ import annotations

from collections.abc import Callable
from datetime import timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from apps.pipeline.airflow_config import AirflowRunConfig
from apps.pipeline.events import message_for_stage, progress_for_stage, redact_text
from apps.pipeline.registry import PROJECT_STAGE_ORDER

from ..db.database import init_database, session_scope
from ..db.models import utc_now
from ..db.repositories import JobRepository, ProjectRepository
from ..services.project_service import (
    ProjectNotFoundError,
    ensure_project_workspace,
    get_project_status,
    safe_relative_path,
)
from .airflow_client import (
    AirflowApiClient,
    AirflowApiError,
    AirflowSettings,
)
from .base import (
    AIRFLOW_PIPELINE_JOB_TYPE,
    PIPELINE_JOB_TYPES,
    JobResult,
    PipelineStatus,
    ProjectAlreadyRunningError,
    ProjectOrchestratorConfigurationError,
    ProjectOrchestratorNotFoundError,
)


ACTIVE_AIRFLOW_STATES = {
    "deferred",
    "none",
    "queued",
    "restarting",
    "running",
    "scheduled",
    "up_for_reschedule",
    "up_for_retry",
}
TERMINAL_AIRFLOW_STATES = {"cancelled", "canceled", "failed", "success"}
ClientFactory = Callable[[AirflowSettings], AirflowApiClient]
TASK_ORDER = PROJECT_STAGE_ORDER


class AirflowOrchestrator:
    def __init__(
        self,
        *,
        project_root: Path,
        settings: AirflowSettings | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        try:
            self.settings = settings or AirflowSettings.from_environment()
        except ValueError as exc:
            raise ProjectOrchestratorConfigurationError(str(exc)) from exc
        self.client_factory = client_factory or AirflowApiClient

    def start_project(self, project_id: int) -> JobResult:
        init_database()
        with session_scope() as session:
            projects = ProjectRepository(session)
            jobs = JobRepository(session)
            project = projects.get(int(project_id))
            if project is None:
                raise ProjectOrchestratorNotFoundError(f"Unknown project_id: {project_id}")
            if jobs.active_for_project_types(project.id, PIPELINE_JOB_TYPES) is not None:
                raise ProjectAlreadyRunningError(
                    f"Project {project.id} already has an active pipeline run."
                )
            workspace = ensure_project_workspace(project.id, project_root=self.project_root)
            workspace_relative = safe_relative_path(workspace, project_root=self.project_root)
            job = jobs.create(
                project_id=project.id,
                job_type=AIRFLOW_PIPELINE_JOB_TYPE,
                status="queued",
                current_stage="waiting",
                progress=float(progress_for_stage("waiting") or 0.0),
                orchestrator_type="airflow",
                airflow_dag_id=self.settings.dag_id,
                airflow_state="queued",
            )
            dag_run_id = _dag_run_id(project.id, job.id, job.created_at)
            jobs.update_state(job, airflow_dag_run_id=dag_run_id)
            project.workspace_path = workspace_relative
            projects.update_flow_state(
                project,
                status="queued",
                current_stage="waiting",
                progress_percent=progress_for_stage("waiting"),
                error_message=None,
                completed_at=None,
            )
            run_config = AirflowRunConfig.from_dict(
                {
                    "schema_version": 1,
                    "project_id": project.id,
                    "job_id": job.id,
                    "source_url": project.source_url,
                    "workspace_relative_path": workspace_relative.replace("\\", "/"),
                    "auto_review": bool(project.auto_review),
                    "subtitle_checker_mode": "local_only",
                }
            )
            job_id = job.id

        try:
            with self.client_factory(self.settings) as client:
                response = client.trigger_dag_run(
                    dag_id=self.settings.dag_id,
                    dag_run_id=dag_run_id,
                    conf=run_config.to_dict(),
                )
            airflow_state = _safe_airflow_state(response.get("state"), default="queued")
        except AirflowApiError as exc:
            self._mark_trigger_failed(project_id, job_id)
            raise ProjectOrchestratorConfigurationError(
                "Airflow is unavailable; the requested pipeline was not started."
            ) from exc

        with session_scope() as session:
            job = JobRepository(session).get(job_id)
            if job is not None:
                JobRepository(session).update_state(job, airflow_state=airflow_state)
        return JobResult(
            project_id=int(project_id),
            job_id=job_id,
            status="queued",
            stage="waiting",
            progress_percent=float(progress_for_stage("waiting") or 0.0),
            message=message_for_stage("waiting"),
            orchestrator_type="airflow",
            airflow_dag_id=self.settings.dag_id,
            airflow_dag_run_id=dag_run_id,
            airflow_state=airflow_state,
            airflow_ui_url=self._ui_url(dag_run_id),
        )

    def get_status(self, project_id: int) -> PipelineStatus:
        job = self._latest_job(project_id)
        if job is not None and job.status in {"queued", "running"}:
            try:
                self._reconcile_job(job.id)
            except AirflowApiError:
                self._mark_unavailable(job.id)
        status = get_project_status(int(project_id))
        return self._pipeline_status(status)

    def cancel_project(self, project_id: int) -> PipelineStatus:
        job = self._latest_job(project_id)
        if job is None or not job.airflow_dag_id or not job.airflow_dag_run_id:
            raise ProjectOrchestratorNotFoundError(
                f"Project {project_id} does not have an Airflow DagRun."
            )
        if job.status not in {"queued", "running"}:
            return self._pipeline_status(get_project_status(int(project_id)))
        self._persist_cancelled(project_id, job.id)
        try:
            with self.client_factory(self.settings) as client:
                tasks = client.list_task_instances(
                    dag_id=job.airflow_dag_id,
                    dag_run_id=job.airflow_dag_run_id,
                )
                for task in tasks:
                    task_id = _safe_task_id(task.get("task_id"))
                    state = _safe_airflow_state(task.get("state"), default="unknown")
                    if task_id and state in ACTIVE_AIRFLOW_STATES:
                        client.fail_task_instance(
                            dag_id=job.airflow_dag_id,
                            dag_run_id=job.airflow_dag_run_id,
                            task_id=task_id,
                        )
                client.set_dag_run_failed(
                    dag_id=job.airflow_dag_id,
                    dag_run_id=job.airflow_dag_run_id,
                )
        except AirflowApiError:
            self._mark_unavailable(job.id, preserve_terminal=True)
        return self._pipeline_status(get_project_status(int(project_id)))

    def read_project_log_tail(self, project_id: int, *, tail: int = 200) -> dict[str, Any]:
        status = self.get_status(project_id)
        lines = [
            f"Orchestrator: Airflow",
            f"DagRun state: {status.airflow_state or 'unknown'}",
            f"Current stage: {status.stage}",
        ]
        if status.retry_attempt is not None and status.retry_max_attempts is not None:
            lines.append(f"Attempt: {status.retry_attempt} of {status.retry_max_attempts}")
        if status.error_message:
            lines.append(redact_text(status.error_message))
        line_count = max(1, min(int(tail or 200), 1000))
        return {"project_id": int(project_id), "tail": line_count, "lines": lines[-line_count:]}

    def reconcile_active_jobs(self) -> int:
        init_database()
        with session_scope() as session:
            job_ids = [
                job.id
                for job in JobRepository(session).list_active(AIRFLOW_PIPELINE_JOB_TYPE)
            ]
        reconciled = 0
        for job_id in job_ids:
            try:
                self._reconcile_job(job_id)
            except AirflowApiError:
                self._mark_unavailable(job_id)
            else:
                reconciled += 1
        return reconciled

    def _reconcile_job(self, job_id: int) -> None:
        with session_scope() as session:
            job = JobRepository(session).get(job_id)
            if job is None or not job.airflow_dag_id or not job.airflow_dag_run_id:
                return
            dag_id = job.airflow_dag_id
            run_id = job.airflow_dag_run_id
        with self.client_factory(self.settings) as client:
            run = client.get_dag_run(dag_id=dag_id, dag_run_id=run_id)
            tasks = client.list_task_instances(dag_id=dag_id, dag_run_id=run_id)
        self._apply_remote_state(job_id, run, tasks)

    def _apply_remote_state(
        self,
        job_id: int,
        run: dict[str, Any],
        tasks: list[dict[str, Any]],
    ) -> None:
        run_state = _safe_airflow_state(run.get("state"), default="unknown")
        task = _current_task(tasks)
        task_id = _safe_task_id(task.get("task_id")) if task else None
        task_state = _safe_airflow_state(task.get("state"), default="unknown") if task else None
        try_number = _optional_int(task.get("try_number")) if task else None
        max_tries = _optional_int(task.get("max_tries")) if task else None
        now = utc_now()
        with session_scope() as session:
            projects = ProjectRepository(session)
            jobs = JobRepository(session)
            job = jobs.get(job_id)
            if job is None:
                return
            project = projects.get(job.project_id)
            if project is None:
                return
            if job.cancel_requested or job.status == "cancelled" or project.status == "cancelled":
                return

            if run_state in {"cancelled", "canceled"}:
                self._update_cancelled(projects, jobs, project, job, now)
                return
            if run_state == "success":
                jobs.update_state(
                    job,
                    status="completed",
                    current_stage="ready",
                    progress=100.0,
                    finished_at=job.finished_at or now,
                    error_message=None,
                    airflow_state="success",
                    airflow_task_id="mark_ready",
                )
                projects.update_flow_state(
                    project,
                    status="ready",
                    current_stage="ready",
                    progress_percent=100.0,
                    error_message=None,
                    completed_at=project.completed_at or now,
                )
                return
            final_task_failure = bool(
                task_state == "failed"
                and try_number is not None
                and max_tries is not None
                and try_number > max_tries
            )
            if run_state == "failed" or final_task_failure:
                stage = _runtime_stage(task_id)
                message = (
                    f"Airflow task {task_id} exhausted its configured attempts."
                    if task_id
                    else "Airflow DagRun failed."
                )
                jobs.update_state(
                    job,
                    status="failed",
                    current_stage=stage,
                    progress=float(job.progress or 0.0),
                    finished_at=now,
                    error_message=message,
                    airflow_state="failed",
                    airflow_task_id=task_id,
                    airflow_try_number=try_number,
                    airflow_max_tries=max_tries,
                )
                projects.update_flow_state(
                    project,
                    status="failed",
                    current_stage="failed",
                    progress_percent=float(project.progress_percent or 0.0),
                    error_message=message,
                    completed_at=now,
                )
                return

            stage = _runtime_stage(task_id)
            retrying = task_state in {"up_for_retry", "restarting", "up_for_reschedule"}
            state = task_state if retrying else run_state
            error_message = None
            if retrying and try_number is not None and max_tries is not None:
                error_message = f"Airflow scheduled another attempt for {task_id}."
            jobs.update_state(
                job,
                status="running" if run_state == "running" or task_id else "queued",
                current_stage=stage,
                progress=progress_for_stage(stage),
                started_at=job.started_at or (now if run_state == "running" else None),
                error_message=error_message,
                airflow_state=state,
                airflow_task_id=task_id,
                airflow_try_number=try_number,
                airflow_max_tries=max_tries,
            )
            projects.update_flow_state(
                project,
                status="running" if run_state == "running" or task_id else "queued",
                current_stage=stage,
                progress_percent=progress_for_stage(stage),
                error_message=error_message,
                started_at=project.started_at or (now if run_state == "running" else None),
                completed_at=None,
            )

    def _mark_trigger_failed(self, project_id: int, job_id: int) -> None:
        message = "Airflow is unavailable; the requested pipeline was not started."
        now = utc_now()
        with session_scope() as session:
            projects = ProjectRepository(session)
            jobs = JobRepository(session)
            project = projects.get(project_id)
            job = jobs.get(job_id)
            if job is not None:
                jobs.update_state(
                    job,
                    status="failed",
                    current_stage="waiting",
                    finished_at=now,
                    error_message=message,
                    airflow_state="unavailable",
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

    def _mark_unavailable(self, job_id: int, *, preserve_terminal: bool = False) -> None:
        with session_scope() as session:
            job = JobRepository(session).get(job_id)
            if job is None:
                return
            options: dict[str, Any] = {"airflow_state": "unavailable"}
            if not preserve_terminal and job.status in {"queued", "running"}:
                options["error_message"] = "Airflow state is temporarily unavailable."
            JobRepository(session).update_state(job, **options)

    def _persist_cancelled(self, project_id: int, job_id: int) -> None:
        now = utc_now()
        with session_scope() as session:
            projects = ProjectRepository(session)
            jobs = JobRepository(session)
            project = projects.get(project_id)
            job = jobs.get(job_id)
            if project is None or job is None:
                raise ProjectOrchestratorNotFoundError(f"Unknown project_id: {project_id}")
            self._update_cancelled(projects, jobs, project, job, now)

    @staticmethod
    def _update_cancelled(projects, jobs, project, job, now) -> None:
        jobs.update_state(
            job,
            status="cancelled",
            current_stage="cancelled",
            finished_at=now,
            error_message="Cancelled by user.",
            airflow_state="failed",
            cancel_requested=True,
        )
        projects.update_flow_state(
            project,
            status="cancelled",
            current_stage="cancelled",
            progress_percent=float(project.progress_percent or 0.0),
            error_message="Cancelled by user.",
            completed_at=now,
        )

    def _latest_job(self, project_id: int):
        init_database()
        with session_scope() as session:
            project = ProjectRepository(session).get(int(project_id))
            if project is None:
                raise ProjectOrchestratorNotFoundError(f"Unknown project_id: {project_id}")
            job = JobRepository(session).latest_for_project(
                project.id,
                AIRFLOW_PIPELINE_JOB_TYPE,
            )
            if job is None:
                return None
            session.expunge(job)
            return job

    def _pipeline_status(self, status: dict[str, Any]) -> PipelineStatus:
        job = status.get("job") or {}
        run_id = job.get("airflow_dag_run_id")
        return PipelineStatus(
            project_id=int(status["project_id"]),
            status=str(status["status"]),
            stage=str(status["stage"]),
            progress_percent=float(status["progress_percent"]),
            message=str(status["message"]),
            error_message=status.get("error_message") or job.get("error_message"),
            started_at=status.get("started_at"),
            updated_at=status.get("updated_at"),
            completed_at=status.get("completed_at"),
            job_id=job.get("id"),
            orchestrator_type="airflow",
            airflow_dag_id=job.get("airflow_dag_id"),
            airflow_dag_run_id=run_id,
            airflow_state=job.get("airflow_state"),
            airflow_ui_url=self._ui_url(run_id) if run_id else None,
            airflow_task_id=job.get("airflow_task_id"),
            retry_attempt=job.get("retry_attempt"),
            retry_max_attempts=job.get("retry_max_attempts"),
        )

    def _ui_url(self, dag_run_id: str) -> str | None:
        if not self.settings.ui_base_url:
            return None
        return (
            f"{self.settings.ui_base_url.rstrip('/')}/dags/"
            f"{quote(self.settings.dag_id, safe='')}/runs/{quote(dag_run_id, safe='')}"
        )


def _dag_run_id(project_id: int, job_id: int, created_at) -> str:
    value = created_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    stamp = value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"project-{int(project_id)}-job-{int(job_id)}-{stamp}"


def _safe_airflow_state(value: Any, *, default: str) -> str:
    state = str(value or default).strip().lower()
    allowed = ACTIVE_AIRFLOW_STATES | TERMINAL_AIRFLOW_STATES | {
        "skipped",
        "upstream_failed",
        "removed",
        "unknown",
        "unavailable",
    }
    return state if state in allowed else "unknown"


def _safe_task_id(value: Any) -> str | None:
    task_id = str(value or "").strip()
    return task_id if task_id in TASK_ORDER else None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _current_task(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    ranked_states = (
        "up_for_retry",
        "restarting",
        "up_for_reschedule",
        "running",
        "failed",
        "queued",
        "scheduled",
    )
    safe_tasks = [task for task in tasks if _safe_task_id(task.get("task_id"))]
    for state in ranked_states:
        for task in safe_tasks:
            if _safe_airflow_state(task.get("state"), default="unknown") == state:
                return task
    successful = {
        _safe_task_id(task.get("task_id")): task
        for task in safe_tasks
        if _safe_airflow_state(task.get("state"), default="unknown") == "success"
    }
    for task_id in reversed(TASK_ORDER):
        if task_id in successful:
            return successful[task_id]
    return None


def _runtime_stage(task_id: str | None) -> str:
    return {
        "prepare_workspace": "waiting",
        "download_source": "downloading",
        "transcribe": "transcribing",
        "validate_transcript": "validating_transcript",
        "generate_candidates": "generating_candidates",
        "import_candidates": "importing_candidates",
        "review_boundaries": "reviewing_with_ai",
        "mark_ready": "ready",
    }.get(task_id or "", "waiting")
