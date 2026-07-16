from __future__ import annotations

import json

from pipeline_modes import subtitle_checker_sample_limit

from ..context import PipelineContext
from ..exceptions import TranscriptValidationError
from ..results import PipelineStageResult
from .common import MediaLocator, python_script, run_stage_command


class ValidateTranscriptStage:
    stage = "validating_transcript"

    def run(self, context: PipelineContext) -> PipelineStageResult:
        mode = str(context.config.subtitle_checker_mode)
        if context.config.skip_subtitle_checker or mode == "off":
            return PipelineStageResult(
                stage=self.stage,
                success=True,
                message="Transcript validation skipped by configuration.",
                metadata={"skipped": True, "mode": mode},
            )
        if not context.transcript_file.exists():
            raise TranscriptValidationError("Transcript validation requires final_transcript.json.")
        audio_path = MediaLocator(context).latest_audio()
        if audio_path is None:
            raise TranscriptValidationError("Transcript validation requires a usable audio stream.")

        cached = self._cached_result(context, audio_path)
        if cached is not None:
            return cached

        command = python_script(context, "subtitler_checker.py") + [
            "--audio",
            str(audio_path),
            "--transcript",
            str(context.transcript_file),
            "--report",
            str(context.subtitle_report_file),
        ]
        if mode == "local_only":
            command.append("--warn-only")
        if context.config.auto_fix_subtitles:
            command.append("--fix")
        sample_limit = subtitle_checker_sample_limit(
            mode,
            default_full_samples=context.config.subtitle_checker_ai_samples,
        )
        if sample_limit <= 0:
            command.append("--skip-ai")
        else:
            command.extend(["--max-samples", str(sample_limit)])

        run_stage_command(
            context,
            command,
            description="Transcript validation",
            error_type=TranscriptValidationError,
        )
        if not context.subtitle_report_file.exists():
            raise TranscriptValidationError("Transcript validation did not produce its report.")
        return PipelineStageResult(
            stage=self.stage,
            success=True,
            message="Transcript validation completed.",
            produced_artifacts=(
                context.safe_artifact(context.transcript_file),
                context.safe_artifact(context.subtitle_report_file),
            ),
            metadata={"skipped": False, "mode": mode},
        )

    def _cached_result(self, context: PipelineContext, audio_path) -> PipelineStageResult | None:
        report_path = context.subtitle_report_file
        if context.config.force_subtitle_checker or not report_path.exists():
            return None
        source_mtime = max(context.transcript_file.stat().st_mtime, audio_path.stat().st_mtime)
        if report_path.stat().st_mtime < source_mtime:
            return None
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            summary = dict(report.get("summary") or {})
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        status = str(summary.get("status") or "unknown")
        if status == "fail" and context.config.subtitle_checker_mode != "local_only":
            raise TranscriptValidationError(
                "The current transcript validation report failed; use --force-subtitle-checker after corrections."
            )
        return PipelineStageResult(
            stage=self.stage,
            success=True,
            message="Current transcript validation report reused.",
            produced_artifacts=(context.safe_artifact(report_path),),
            metadata={"reused": True, "status": status},
        )
