from __future__ import annotations

import os
from pathlib import Path

from .base import PipelineOrchestrator, ProjectOrchestratorConfigurationError
from .airflow import AirflowOrchestrator
from .local import LocalPipelineOrchestrator


def configured_orchestrator_name() -> str:
    return str(os.environ.get("PIPELINE_ORCHESTRATOR") or "local").strip().lower() or "local"


def get_pipeline_orchestrator(*, project_root: Path) -> PipelineOrchestrator:
    name = configured_orchestrator_name()
    if name == "local":
        return LocalPipelineOrchestrator(project_root=project_root)
    if name == "airflow":
        return AirflowOrchestrator(project_root=project_root)
    raise ProjectOrchestratorConfigurationError(
        f"Unsupported PIPELINE_ORCHESTRATOR={name!r}. Expected 'local' or 'airflow'."
    )


def recover_orphaned_jobs(*, project_root: Path) -> int:
    name = configured_orchestrator_name()
    if name == "local":
        return LocalPipelineOrchestrator(project_root=project_root).recover_orphaned_jobs()
    if name == "airflow":
        return AirflowOrchestrator(project_root=project_root).reconcile_active_jobs()
    raise ProjectOrchestratorConfigurationError(
        f"Unsupported PIPELINE_ORCHESTRATOR={name!r}. Expected 'local' or 'airflow'."
    )
