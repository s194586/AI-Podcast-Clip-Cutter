from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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
        }


class PipelineOrchestrator(Protocol):
    def start_project(self, project_id: int) -> JobResult:
        ...

    def get_status(self, project_id: int) -> PipelineStatus:
        ...

    def cancel_project(self, project_id: int) -> PipelineStatus:
        ...
