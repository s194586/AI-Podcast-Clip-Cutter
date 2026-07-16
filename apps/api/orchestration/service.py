from __future__ import annotations

import os
from pathlib import Path

from .base import ProjectOrchestratorConfigurationError
from .local import LocalPipelineOrchestrator


def configured_orchestrator_name() -> str:
    return str(os.environ.get("PIPELINE_ORCHESTRATOR") or "local").strip().lower() or "local"


def get_pipeline_orchestrator(*, project_root: Path) -> LocalPipelineOrchestrator:
    name = configured_orchestrator_name()
    if name == "local":
        return LocalPipelineOrchestrator(project_root=project_root)
    raise ProjectOrchestratorConfigurationError(
        f"Unsupported PIPELINE_ORCHESTRATOR={name!r}. Only 'local' is available in this build."
    )


def recover_orphaned_jobs(*, project_root: Path) -> int:
    name = configured_orchestrator_name()
    if name != "local":
        return 0
    return LocalPipelineOrchestrator(project_root=project_root).recover_orphaned_jobs()
