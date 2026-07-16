from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cancellation import CancellationToken
from .config import PipelineConfig


@dataclass(repr=False)
class PipelineContext:
    project_id: int | None
    source_url: str | None
    workspace_path: Path
    repository_root: Path
    auto_review: bool = True
    analysis_only: bool = False
    config: PipelineConfig = field(default_factory=PipelineConfig)
    cancellation: CancellationToken = field(default_factory=CancellationToken, repr=False)

    def __post_init__(self) -> None:
        self.repository_root = Path(self.repository_root).resolve()
        self.workspace_path = Path(self.workspace_path).resolve()
        self.source_url = str(self.source_url).strip() if self.source_url else None
        if self.project_id is not None:
            self.project_id = int(self.project_id)
            expected = (self.repository_root / "data" / "projects" / str(self.project_id) / "workspace").resolve()
            if self.workspace_path != expected:
                raise ValueError(
                    f"Project {self.project_id} must use its isolated workspace: {expected}"
                )

    @classmethod
    def for_legacy_cli(
        cls,
        *,
        source_url: str | None,
        repository_root: Path,
        workspace_path: Path | None = None,
        analysis_only: bool = False,
        config: PipelineConfig | None = None,
    ) -> "PipelineContext":
        root = Path(repository_root).resolve()
        return cls(
            project_id=None,
            source_url=source_url,
            workspace_path=Path(workspace_path).resolve() if workspace_path else root,
            repository_root=root,
            auto_review=False,
            analysis_only=analysis_only,
            config=config or PipelineConfig(),
        )

    @property
    def input_dir(self) -> Path:
        return self.workspace_path / "input"

    @property
    def metadata_dir(self) -> Path:
        return self.workspace_path / "metadata"

    @property
    def transcripts_dir(self) -> Path:
        return self.workspace_path / "transcripts"

    @property
    def cuts_dir(self) -> Path:
        return self.workspace_path / "cuts"

    @property
    def cuts_raw_dir(self) -> Path:
        return self.cuts_dir / "raw"

    @property
    def cuts_subtitles_dir(self) -> Path:
        return self.cuts_dir / "subtitles"

    @property
    def outputs_dir(self) -> Path:
        return self.workspace_path / "outputs"

    @property
    def logs_dir(self) -> Path:
        return self.workspace_path / "logs"

    @property
    def transcript_file(self) -> Path:
        return self.transcripts_dir / "final_transcript.json"

    @property
    def subtitle_report_file(self) -> Path:
        return self.metadata_dir / "subtitle_check_report.json"

    @property
    def cutting_log_file(self) -> Path:
        return self.metadata_dir / "cutting_logic.json"

    @property
    def content_profile_file(self) -> Path:
        return self.metadata_dir / "content_profile.json"

    @property
    def heatmap_file(self) -> Path:
        return self.metadata_dir / "heatmap.json"

    @property
    def candidate_file(self) -> Path:
        return self.workspace_path / "top_windows.json"

    def safe_artifact(self, path: Path) -> str:
        candidate = Path(path).resolve()
        try:
            return candidate.relative_to(self.workspace_path).as_posix()
        except ValueError:
            return candidate.name

    @property
    def is_cancelled(self) -> bool:
        return self.cancellation.is_cancelled

    def raise_if_cancelled(self) -> None:
        self.cancellation.raise_if_cancelled()

    def safe_summary(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "workspace": self.workspace_path.name,
            "auto_review": bool(self.auto_review),
            "analysis_only": bool(self.analysis_only),
            "has_source_url": bool(self.source_url),
        }

    def __repr__(self) -> str:
        return (
            "PipelineContext("
            f"project_id={self.project_id!r}, "
            f"workspace_path={self.workspace_path!r}, "
            f"repository_root={self.repository_root!r}, "
            f"auto_review={self.auto_review!r}, "
            f"analysis_only={self.analysis_only!r}, "
            f"source_url_configured={bool(self.source_url)!r}, "
            f"config={self.config!r})"
        )
