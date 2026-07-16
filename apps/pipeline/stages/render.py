from __future__ import annotations

from ..context import PipelineContext
from ..exceptions import RenderStageError
from ..results import PipelineStageResult
from .common import MediaLocator, python_script, run_stage_command


class RenderInitialClipsStage:
    stage = "rendering"

    def run(self, context: PipelineContext) -> PipelineStageResult:
        if not context.candidate_file.exists():
            raise RenderStageError("Initial rendering requires top_windows.json.")
        video_path = MediaLocator(context).latest_video()
        if video_path is None:
            raise RenderStageError("Initial rendering requires source video with audio.")

        cutter_command = python_script(context, "cutter.py") + [
            "--video",
            str(video_path),
            "--windows",
            str(context.candidate_file),
            "--transcript",
            str(context.transcript_file),
            "--output-dir",
            str(context.cuts_raw_dir),
            "--cutting-log",
            str(context.cutting_log_file),
            "--layout-mode",
            context.config.layout_mode,
        ]
        run_stage_command(
            context,
            cutter_command,
            description="Initial clip rendering",
            error_type=RenderStageError,
        )

        subtitle_command = python_script(context, "subtitler.py") + [
            "--transcript",
            str(context.transcript_file),
            "--input-dir",
            str(context.cuts_raw_dir),
            "--output-raw",
            str(context.cuts_raw_dir),
            "--output-subs",
            str(context.cuts_subtitles_dir),
        ]
        run_stage_command(
            context,
            subtitle_command,
            description="Subtitle burn-in",
            error_type=RenderStageError,
        )
        raw_outputs = sorted(context.cuts_raw_dir.glob("*.mp4"))
        subtitled_outputs = sorted(context.cuts_subtitles_dir.glob("*.mp4"))
        return PipelineStageResult(
            stage=self.stage,
            success=True,
            message="Initial clips rendered with subtitles.",
            produced_artifacts=tuple(
                context.safe_artifact(path) for path in raw_outputs + subtitled_outputs
            ),
            metadata={
                "raw_clip_count": len(raw_outputs),
                "subtitled_clip_count": len(subtitled_outputs),
            },
        )


class CleanupInputStage:
    stage = "cleanup"

    def run(self, context: PipelineContext) -> PipelineStageResult:
        if not context.config.cleanup:
            return PipelineStageResult(
                stage=self.stage,
                success=True,
                message="Input cleanup not requested.",
                metadata={"skipped": True},
            )
        deleted = 0
        for path in context.input_dir.iterdir():
            if path.is_file() and path.suffix.lower() in {
                ".mp4",
                ".mkv",
                ".mov",
                ".webm",
                ".m4a",
                ".mp3",
            }:
                path.unlink()
                deleted += 1
        return PipelineStageResult(
            stage=self.stage,
            success=True,
            message=f"Removed {deleted} input media file(s).",
            metadata={"deleted_file_count": deleted},
        )
