from __future__ import annotations

from collections.abc import Iterable

from heatmap_contract import HeatmapUnavailableError

from .context import PipelineContext
from .events import PipelineEvent, message_for_stage, progress_for_stage, redact_text
from .executor import EventSink, PipelineStage, PipelineStageExecutor
from .exceptions import PipelineCancelled, PipelineError
from .results import PipelineRunResult, PipelineStageResult


class PipelineRunner:
    def __init__(
        self,
        stages: Iterable[PipelineStage],
        *,
        event_sinks: Iterable[EventSink] = (),
    ) -> None:
        self.stages = tuple(stages)
        self.event_sinks = tuple(event_sinks)
        self.stage_executor = PipelineStageExecutor(event_sinks=(self._emit,))

    def run(self, context: PipelineContext) -> PipelineRunResult:
        results: list[PipelineStageResult] = []
        for pipeline_stage in self.stages:
            stage = pipeline_stage.stage
            try:
                context.raise_if_cancelled()
            except PipelineCancelled as exc:
                return self._cancelled_result(results, stage=stage, exc=exc)
            try:
                result = self.stage_executor.execute(context, pipeline_stage)
            except KeyboardInterrupt as exc:
                return self._cancelled_result(
                    results, stage=stage, exc=PipelineCancelled("Pipeline cancelled by user.")
                )
            except PipelineCancelled as exc:
                return self._cancelled_result(results, stage=stage, exc=exc)
            except Exception as exc:
                return self._failed_result(results, stage=stage, exc=exc, exit_code=1)

            results.append(result)

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
        error_code = None
        if isinstance(exc, HeatmapUnavailableError):
            category = exc.__class__.__name__
            message = "YouTube Most Replayed data is unavailable for this video."
            error_code = "heatmap_unavailable"
        else:
            if not isinstance(exc, PipelineError):
                exc = PipelineError(f"{stage} failed: {exc}")
            category = exc.__class__.__name__
            message = redact_text(str(exc).strip() or category)
        failed_stage_result = PipelineStageResult(
            stage=stage,
            success=False,
            message=message,
            error_category=category,
            error_code=error_code,
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
                error_code=error_code,
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
                error_code=error_code,
            )
        )
        return PipelineRunResult(
            success=False,
            stage_results=tuple(results),
            message=message,
            failed_stage=stage,
            error_category=category,
            error_code=error_code,
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
