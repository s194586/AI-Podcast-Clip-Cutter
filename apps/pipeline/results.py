from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PipelineStageResult:
    stage: str
    success: bool
    message: str
    produced_artifacts: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    error_category: str | None = None
    progress_percent: float | None = None


@dataclass(frozen=True)
class PipelineRunResult:
    success: bool
    stage_results: tuple[PipelineStageResult, ...]
    message: str
    failed_stage: str | None = None
    error_category: str | None = None
    exit_code: int = 0

    @property
    def completed_stages(self) -> tuple[str, ...]:
        return tuple(result.stage for result in self.stage_results if result.success)
