from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


LOCAL_PIPELINE_JOB_TYPE = "local_pipeline"
AIRFLOW_PIPELINE_JOB_TYPE = "airflow_pipeline"
PIPELINE_JOB_TYPES = (LOCAL_PIPELINE_JOB_TYPE, AIRFLOW_PIPELINE_JOB_TYPE)


class ProjectAlreadyRunningError(RuntimeError):
    pass


class ProjectOrchestratorConfigurationError(RuntimeError):
    pass


class ProjectOrchestratorNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class JobResult:
    project_id: int
    job_id: int | None
    status: str
    stage: str
    progress_percent: float
    message: str
    error_message: str | None = None
    orchestrator_type: str = "local"
    airflow_dag_id: str | None = None
    airflow_dag_run_id: str | None = None
    airflow_state: str | None = None
    airflow_ui_url: str | None = None

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "job_id": self.job_id,
            "status": self.status,
            "stage": self.stage,
            "current_stage": self.stage,
            "progress_percent": self.progress_percent,
            "message": self.message,
            "error_message": self.error_message,
            "orchestrator_type": self.orchestrator_type,
            "airflow_dag_id": self.airflow_dag_id,
            "airflow_dag_run_id": self.airflow_dag_run_id,
            "airflow_state": self.airflow_state,
            "airflow_ui_url": self.airflow_ui_url,
        }


@dataclass(frozen=True)
class PipelineStatus:
    project_id: int
    status: str
    stage: str
    progress_percent: float
    message: str
    error_message: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    job_id: int | None = None
    log_path: str | None = None
    orchestrator_type: str = "local"
    airflow_dag_id: str | None = None
    airflow_dag_run_id: str | None = None
    airflow_state: str | None = None
    airflow_ui_url: str | None = None
    airflow_task_id: str | None = None
    retry_attempt: int | None = None
    retry_max_attempts: int | None = None

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "status": self.status,
            "stage": self.stage,
            "current_stage": self.stage,
            "progress_percent": self.progress_percent,
            "message": self.message,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "job_id": self.job_id,
            "log_path": self.log_path,
            "orchestrator_type": self.orchestrator_type,
            "airflow_dag_id": self.airflow_dag_id,
            "airflow_dag_run_id": self.airflow_dag_run_id,
            "airflow_state": self.airflow_state,
            "airflow_ui_url": self.airflow_ui_url,
            "airflow_task_id": self.airflow_task_id,
            "retry_attempt": self.retry_attempt,
            "retry_max_attempts": self.retry_max_attempts,
        }


class PipelineOrchestrator(Protocol):
    def start_project(self, project_id: int) -> JobResult:
        ...

    def get_status(self, project_id: int) -> PipelineStatus:
        ...

    def cancel_project(self, project_id: int) -> PipelineStatus:
        ...
