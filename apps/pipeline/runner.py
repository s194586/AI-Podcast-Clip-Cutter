from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from .context import PipelineContext
from .events import PipelineEvent, message_for_stage, progress_for_stage, redact_text
from .exceptions import PipelineCancelled, PipelineError
from .results import PipelineRunResult, PipelineStageResult


class PipelineStage(Protocol):
    stage: str

    def run(self, context: PipelineContext) -> PipelineStageResult:
        ...


EventSink = Callable[[PipelineEvent], None]


class PipelineRunner:
    def __init__(
        self,
        stages: Iterable[PipelineStage],
        *,
        event_sinks: Iterable[EventSink] = (),
    ) -> None:
        self.stages = tuple(stages)
        self.event_sinks = tuple(event_sinks)

    def run(self, context: PipelineContext) -> PipelineRunResult:
        results: list[PipelineStageResult] = []
        for pipeline_stage in self.stages:
            stage = pipeline_stage.stage
            try:
                context.raise_if_cancelled()
            except PipelineCancelled as exc:
                return self._cancelled_result(results, stage=stage, exc=exc)
            event_binding = getattr(pipeline_stage, "set_event_sink", None)
            if callable(event_binding):
                event_binding(self._emit)
            self._emit(
                PipelineEvent(
                    event="stage_started",
                    stage=stage,
                    message=message_for_stage(stage),
                    progress_percent=progress_for_stage(stage),
                )
            )
            progress = progress_for_stage(stage)
            if progress is not None:
                self._emit(
                    PipelineEvent(
                        event="stage_progress",
                        stage=stage,
                        message=message_for_stage(stage),
                        progress_percent=progress,
                    )
                )
            try:
                result = pipeline_stage.run(context)
                if not result.success:
                    raise PipelineError(result.message)
                context.raise_if_cancelled()
            except KeyboardInterrupt as exc:
                return self._cancelled_result(
                    results, stage=stage, exc=PipelineCancelled("Pipeline cancelled by user.")
                )
            except PipelineCancelled as exc:
                return self._cancelled_result(results, stage=stage, exc=exc)
            except Exception as exc:
                return self._failed_result(results, stage=stage, exc=exc, exit_code=1)

            results.append(result)
            self._emit(
                PipelineEvent(
                    event="stage_completed",
                    stage=stage,
                    message=result.message,
                    progress_percent=(
                        result.progress_percent
                        if result.progress_percent is not None
                        else progress_for_stage(stage)
                    ),
                    success=True,
                    produced_artifacts=result.produced_artifacts,
                    metadata=result.metadata,
                )
            )

        try:
            context.raise_if_cancelled()
        except PipelineCancelled as exc:
            return self._cancelled_result(results, stage="ready", exc=exc)
        completed = PipelineRunResult(
            success=True,
            stage_results=tuple(results),
            message="Pipeline completed successfully.",
            exit_code=0,
        )
        self._emit(
            PipelineEvent(
                event="pipeline_completed",
                stage="ready",
                message=completed.message,
                progress_percent=100.0,
                success=True,
            )
        )
        return completed

    def _failed_result(
        self,
        results: list[PipelineStageResult],
        *,
        stage: str,
        exc: Exception,
        exit_code: int,
    ) -> PipelineRunResult:
        if not isinstance(exc, PipelineError):
            exc = PipelineError(f"{stage} failed: {exc}")
        category = exc.__class__.__name__
        message = redact_text(str(exc).strip() or category)
        failed_stage_result = PipelineStageResult(
            stage=stage,
            success=False,
            message=message,
            error_category=category,
        )
        results.append(failed_stage_result)
        self._emit(
            PipelineEvent(
                event="stage_failed",
                stage=stage,
                message=message,
                progress_percent=progress_for_stage(stage),
                success=False,
                error_category=category,
            )
        )
        self._emit(
            PipelineEvent(
                event="pipeline_completed",
                stage=stage,
                message=message,
                progress_percent=progress_for_stage(stage),
                success=False,
                error_category=category,
            )
        )
        return PipelineRunResult(
            success=False,
            stage_results=tuple(results),
            message=message,
            failed_stage=stage,
            error_category=category,
            exit_code=exit_code,
        )

    def _cancelled_result(
        self,
        results: list[PipelineStageResult],
        *,
        stage: str,
        exc: PipelineCancelled,
    ) -> PipelineRunResult:
        message = redact_text(str(exc).strip() or "Pipeline cancelled by user.")
        self._emit(
            PipelineEvent(
                event="pipeline_cancelled",
                stage=stage,
                message=message,
                progress_percent=progress_for_stage(stage),
                success=False,
                error_category="PipelineCancelled",
            )
        )
        return PipelineRunResult(
            success=False,
            stage_results=tuple(results),
            message=message,
            failed_stage=stage,
            error_category="PipelineCancelled",
            exit_code=130,
        )

    def _emit(self, event: PipelineEvent) -> None:
        for sink in self.event_sinks:
            sink(event)
