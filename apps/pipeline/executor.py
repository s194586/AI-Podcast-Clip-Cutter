from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from .context import PipelineContext
from .events import PipelineEvent, message_for_stage, progress_for_stage
from .exceptions import PipelineError
from .results import PipelineStageResult


class PipelineStage(Protocol):
    stage: str

    def run(self, context: PipelineContext) -> PipelineStageResult:
        ...


EventSink = Callable[[PipelineEvent], None]


class PipelineStageExecutor:
    """Execute one reusable stage and emit non-terminal lifecycle events."""

    def __init__(self, *, event_sinks: Iterable[EventSink] = ()) -> None:
        self.event_sinks = tuple(event_sinks)

    def execute(self, context: PipelineContext, pipeline_stage: PipelineStage) -> PipelineStageResult:
        stage = pipeline_stage.stage
        context.raise_if_cancelled()
        event_binding = getattr(pipeline_stage, "set_event_sink", None)
        if callable(event_binding):
            event_binding(self._emit)

        progress = progress_for_stage(stage)
        self._emit(
            PipelineEvent(
                event="stage_started",
                stage=stage,
                message=message_for_stage(stage),
                progress_percent=progress,
            )
        )
        if progress is not None:
            self._emit(
                PipelineEvent(
                    event="stage_progress",
                    stage=stage,
                    message=message_for_stage(stage),
                    progress_percent=progress,
                )
            )

        result = pipeline_stage.run(context)
        if not result.success:
            raise PipelineError(result.message)
        context.raise_if_cancelled()
        self._emit(
            PipelineEvent(
                event="stage_completed",
                stage=stage,
                message=result.message,
                progress_percent=(
                    result.progress_percent
                    if result.progress_percent is not None
                    else progress
                ),
                success=True,
                produced_artifacts=result.produced_artifacts,
                metadata=result.metadata,
            )
        )
        return result

    def _emit(self, event: PipelineEvent) -> None:
        for sink in self.event_sinks:
            sink(event)
