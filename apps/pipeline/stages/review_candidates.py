from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from apps.review_agent.service import (
    ClipReviewCancelledError,
    ReviewAgentService,
    ReviewBatchTimeoutError,
)

from ..context import PipelineContext
from ..events import PipelineEvent
from ..exceptions import PipelineCancelled, ReviewStageError
from ..results import PipelineStageResult


class ReviewCandidatesStage:
    stage = "reviewing_with_ai"

    def __init__(self, *, review_mode: str | None = None) -> None:
        self.review_mode = review_mode
        self._event_sink: Callable[[PipelineEvent], None] | None = None

    def set_event_sink(self, event_sink: Callable[[PipelineEvent], None]) -> None:
        self._event_sink = event_sink

    def run(self, context: PipelineContext) -> PipelineStageResult:
        if context.project_id is None:
            raise ReviewStageError("Automatic review requires an existing project_id.")
        if not context.auto_review:
            return PipelineStageResult(
                stage=self.stage,
                success=True,
                message="Automatic review disabled; clips remain ready for human review.",
                metadata={"skipped": True},
            )
        try:
            service_options = {"project_root": context.repository_root}
            if self.review_mode is not None:
                service_options["mode"] = self.review_mode
            service = ReviewAgentService(**service_options)
            summary = _call_batch_review(
                service,
                project_id=context.project_id,
                cancellation_check=lambda: context.is_cancelled,
                progress_callback=self._emit_review_progress,
            )
        except ClipReviewCancelledError as exc:
            raise PipelineCancelled("Pipeline cancelled during automatic boundary review.") from exc
        except ReviewBatchTimeoutError as exc:
            raise ReviewStageError(
                "Automatic boundary review exceeded its configured batch timeout."
            ) from exc
        except Exception as exc:
            raise ReviewStageError(f"Automatic boundary review failed: {exc}") from exc

        failed_count = int(summary.get("failed_count") or 0)
        clip_count = int(summary.get("clip_count") or 0)
        if clip_count > 0 and failed_count >= clip_count:
            raise ReviewStageError(
                "Automatic boundary review failed technically for every clip."
            )
        provider = str(summary.get("provider") or "unknown")
        return PipelineStageResult(
            stage=self.stage,
            success=True,
            message=f"Automatic boundary review completed with {provider}.",
            metadata={
                "provider": provider,
                "clip_count": clip_count,
                "manual_review_count": int(summary.get("manual_review_count") or 0),
                "failed_count": failed_count,
            },
            progress_percent=95.0,
        )

    def _emit_review_progress(self, event_name: str, metadata: dict[str, Any]) -> None:
        if self._event_sink is None:
            return
        index = max(1, int(metadata.get("index") or 1))
        total = max(1, int(metadata.get("total") or 1))
        completed = event_name != "review_clip_started"
        completed_count = index if completed else index - 1
        progress = 85.0 + (10.0 * completed_count / total)
        messages = {
            "review_clip_started": f"Reviewing clip {index} of {total}",
            "review_clip_completed": f"Reviewed clip {index} of {total}",
            "review_clip_manual": f"Clip {index} of {total} needs manual review",
            "review_clip_failed": f"Clip {index} of {total} review failed safely",
        }
        safe_metadata = {
            key: metadata[key]
            for key in (
                "clip_id",
                "index",
                "total",
                "provider",
                "decision",
                "retry_used",
                "review_workflow",
                "review_workflow_version",
            )
            if key in metadata
        }
        self._event_sink(
            PipelineEvent(
                event=event_name,
                stage=self.stage,
                message=messages.get(event_name, "Review progress updated"),
                progress_percent=round(progress, 2),
                success=False if event_name == "review_clip_failed" else None,
                metadata=safe_metadata,
            )
        )


def _call_batch_review(
    service: Any,
    *,
    project_id: int,
    cancellation_check: Callable[[], bool],
    progress_callback: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "project_id": project_id,
        "apply_safe_suggestions": True,
    }
    try:
        signature = inspect.signature(service.review_project_clips)
        supports_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
    except (TypeError, ValueError):
        signature = None
        supports_kwargs = False
    optional = {
        "cancellation_check": cancellation_check,
        "progress_callback": progress_callback,
        "skip_completed": True,
    }
    for key, value in optional.items():
        if supports_kwargs or (signature is not None and key in signature.parameters):
            options[key] = value
    return service.review_project_clips(**options)
