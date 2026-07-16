from __future__ import annotations

import json

from .context import PipelineContext
from .events import PipelineEvent
from .results import PipelineRunResult


class StructuredEventPrinter:
    def __call__(self, event: PipelineEvent) -> None:
        print(event.to_marker(), flush=True)


class HumanReadableEventPrinter:
    def __call__(self, event: PipelineEvent) -> None:
        if event.event == "stage_started":
            print(f"\n[{event.stage}] {event.message}", flush=True)
        elif event.event == "stage_failed":
            print(f"\nERROR [{event.stage}]: {event.message}", flush=True)
        elif event.event == "pipeline_completed" and event.success:
            print("\nPipeline completed successfully.", flush=True)


def print_legacy_summary(context: PipelineContext, result: PipelineRunResult) -> None:
    if not result.success:
        print(f"Workflow failed at {result.failed_stage}: {result.message}")
        return
    raw_files = sorted(context.cuts_raw_dir.glob("*.mp4"))
    subtitle_files = sorted(context.cuts_subtitles_dir.glob("*.mp4"))
    print("\nGenerated artifacts:")
    print(f"  Candidate windows: {'yes' if context.candidate_file.exists() else 'no'}")
    print(f"  Raw clips: {len(raw_files)}")
    print(f"  Subtitled clips: {len(subtitle_files)}")
    if context.analysis_only:
        print("  Initial rendering: skipped (--analysis-only)")
    if context.cutting_log_file.exists():
        try:
            payload = json.loads(context.cutting_log_file.read_text(encoding="utf-8"))
            print(f"  Selection mode: {payload.get('ai_mode') or context.config.ai_mode}")
            print(f"  Selection AI status: {payload.get('ai_status') or 'n/a'}")
        except (OSError, json.JSONDecodeError, TypeError):
            pass
