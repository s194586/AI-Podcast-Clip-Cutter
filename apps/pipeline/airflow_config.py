from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .cancellation import CancellationToken
from .config import PipelineConfig
from .context import PipelineContext


AIRFLOW_RUN_CONFIG_SCHEMA_VERSION = 1
AIRFLOW_RUN_CONFIG_FIELDS = frozenset(
    {
        "schema_version",
        "project_id",
        "job_id",
        "source_url",
        "workspace_relative_path",
        "auto_review",
        "subtitle_checker_mode",
    }
)
SUPPORTED_SUBTITLE_CHECKER_MODES = frozenset({"off", "local_only", "limited", "full"})


@dataclass(frozen=True)
class AirflowRunConfig:
    schema_version: int
    project_id: int
    job_id: int
    source_url: str
    workspace_relative_path: str
    auto_review: bool
    subtitle_checker_mode: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AirflowRunConfig":
        if not isinstance(payload, dict):
            raise ValueError("Airflow run configuration must be a JSON object.")
        unknown = set(payload) - AIRFLOW_RUN_CONFIG_FIELDS
        missing = AIRFLOW_RUN_CONFIG_FIELDS - set(payload)
        if unknown:
            raise ValueError(f"Unknown Airflow run configuration fields: {', '.join(sorted(unknown))}")
        if missing:
            raise ValueError(f"Missing Airflow run configuration fields: {', '.join(sorted(missing))}")

        schema_version = _positive_integer(payload["schema_version"], "schema_version")
        if schema_version != AIRFLOW_RUN_CONFIG_SCHEMA_VERSION:
            raise ValueError(f"Unsupported Airflow run configuration schema_version: {schema_version}")
        project_id = _positive_integer(payload["project_id"], "project_id")
        job_id = _positive_integer(payload["job_id"], "job_id")
        source_url = payload["source_url"]
        if not isinstance(source_url, str) or not source_url.strip():
            raise ValueError("source_url must be a non-empty string.")
        if len(source_url) > 2048:
            raise ValueError("source_url exceeds the supported length.")
        if type(payload["auto_review"]) is not bool:
            raise ValueError("auto_review must be a boolean.")
        subtitle_checker_mode = payload["subtitle_checker_mode"]
        if subtitle_checker_mode not in SUPPORTED_SUBTITLE_CHECKER_MODES:
            raise ValueError("subtitle_checker_mode is not supported.")

        workspace = _safe_workspace_path(payload["workspace_relative_path"], project_id)
        return cls(
            schema_version=schema_version,
            project_id=project_id,
            job_id=job_id,
            source_url=source_url.strip(),
            workspace_relative_path=workspace,
            auto_review=payload["auto_review"],
            subtitle_checker_mode=subtitle_checker_mode,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "job_id": self.job_id,
            "source_url": self.source_url,
            "workspace_relative_path": self.workspace_relative_path,
            "auto_review": self.auto_review,
            "subtitle_checker_mode": self.subtitle_checker_mode,
        }

    def build_context(
        self,
        *,
        container_project_root: Path,
        cancellation: CancellationToken | None = None,
    ) -> PipelineContext:
        root = Path(container_project_root).resolve()
        workspace = (root / Path(*PurePosixPath(self.workspace_relative_path).parts)).resolve()
        data_root = (root / "data").resolve()
        try:
            workspace.relative_to(data_root)
        except ValueError as exc:
            raise ValueError("Airflow workspace must remain inside the configured data root.") from exc
        return PipelineContext(
            project_id=self.project_id,
            source_url=self.source_url,
            workspace_path=workspace,
            repository_root=root,
            auto_review=self.auto_review,
            analysis_only=True,
            config=PipelineConfig(
                ai_mode="local_only",
                subtitle_checker_mode=self.subtitle_checker_mode,
            ),
            cancellation=cancellation or CancellationToken(),
        )


def _positive_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer.")
    return value


def _safe_workspace_path(value: Any, project_id: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("workspace_relative_path must be a non-empty relative path.")
    raw = value.strip()
    if "\\" in raw or ":" in raw:
        raise ValueError("workspace_relative_path must use a relative POSIX path.")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("workspace_relative_path contains an unsafe path component.")
    expected = PurePosixPath("data", "projects", str(project_id), "workspace")
    if path != expected:
        raise ValueError(f"Project {project_id} must use workspace {expected.as_posix()}.")
    return path.as_posix()
