from __future__ import annotations

import json

from content_classifier import classify_content, save_content_profile

from ..context import PipelineContext
from ..exceptions import CandidateGenerationError
from ..results import PipelineStageResult
from .common import MediaLocator, python_script, run_stage_command


class GenerateCandidatesStage:
    stage = "generating_candidates"

    def run(self, context: PipelineContext) -> PipelineStageResult:
        if not context.transcript_file.exists():
            raise CandidateGenerationError("Candidate generation requires final_transcript.json.")
        if not context.heatmap_file.exists():
            raise CandidateGenerationError("Candidate generation requires metadata/heatmap.json.")

        if _can_reuse_candidates(context):
            return PipelineStageResult(
                stage=self.stage,
                success=True,
                message="Existing deterministic clip candidates reused.",
                produced_artifacts=(
                    context.safe_artifact(context.candidate_file),
                    context.safe_artifact(context.content_profile_file),
                    context.safe_artifact(context.cutting_log_file),
                ),
                metadata={"selection_mode": context.config.ai_mode, "reused": True},
            )

        video_path = MediaLocator(context).latest_video()
        try:
            profile = classify_content(
                context.transcript_file,
                context.heatmap_file,
                video_path=video_path,
                forced_content_type=context.config.content_type,
            )
            save_content_profile(profile, context.content_profile_file)
        except Exception as exc:
            raise CandidateGenerationError(f"Podcast content profiling failed: {exc}") from exc

        command = python_script(context, "analyze_virals.py") + [
            "--transcript",
            str(context.transcript_file),
            "--heatmap",
            str(context.heatmap_file),
            "--save-json",
            str(context.candidate_file),
            "--cutting-log",
            str(context.cutting_log_file),
            "--ai-mode",
            context.config.ai_mode,
            "--content-profile",
            str(context.content_profile_file),
            "--content-type",
            context.config.content_type,
            "--layout-mode",
            context.config.layout_mode,
        ]
        if video_path is not None:
            command.extend(["--video", str(video_path)])
        if context.config.skip_smart_context:
            command.append("--skip-smart-context")

        run_stage_command(
            context,
            command,
            description="Podcast candidate generation",
            error_type=CandidateGenerationError,
        )
        if not context.candidate_file.exists():
            raise CandidateGenerationError("Candidate generation did not produce top_windows.json.")
        artifacts = [
            context.safe_artifact(context.candidate_file),
            context.safe_artifact(context.content_profile_file),
        ]
        if context.cutting_log_file.exists():
            artifacts.append(context.safe_artifact(context.cutting_log_file))
        return PipelineStageResult(
            stage=self.stage,
            success=True,
            message="Deterministic clip candidates generated.",
            produced_artifacts=tuple(artifacts),
            metadata={"selection_mode": context.config.ai_mode},
        )


def _can_reuse_candidates(context: PipelineContext) -> bool:
    required = (
        context.candidate_file,
        context.content_profile_file,
        context.cutting_log_file,
    )
    if not all(path.is_file() for path in required):
        return False
    source_mtime = max(context.transcript_file.stat().st_mtime, context.heatmap_file.stat().st_mtime)
    if context.candidate_file.stat().st_mtime < source_mtime:
        return False
    try:
        payload = json.loads(context.candidate_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if isinstance(payload, list):
        return bool(payload)
    if isinstance(payload, dict):
        return bool(payload.get("top_windows") or payload.get("windows"))
    return False
