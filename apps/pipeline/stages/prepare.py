from __future__ import annotations

import os
import shutil

from gemini_transport import bootstrap_ssl_certificates, get_api_key
from pipeline_modes import allows_gemini, requires_gemini

from ..context import PipelineContext
from ..exceptions import WorkspacePreparationError
from ..results import PipelineStageResult


class PrepareWorkspaceStage:
    stage = "waiting"

    def run(self, context: PipelineContext) -> PipelineStageResult:
        missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
        if missing:
            raise WorkspacePreparationError(
                f"Missing required tool(s): {', '.join(missing)}. Install FFmpeg and ensure they are on PATH."
            )

        for directory in (
            context.input_dir,
            context.metadata_dir,
            context.transcripts_dir,
            context.cuts_raw_dir,
            context.cuts_subtitles_dir,
            context.outputs_dir,
            context.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        deleted = self._cleanup_generated_artifacts(context)
        self._prepare_gemini_environment(context)
        return PipelineStageResult(
            stage=self.stage,
            success=True,
            message="Workspace prepared.",
            metadata={"removed_stale_artifact_count": deleted},
        )

    def _cleanup_generated_artifacts(self, context: PipelineContext) -> int:
        deleted = 0
        for directory in (context.cuts_raw_dir, context.cuts_subtitles_dir):
            for path in directory.glob("*"):
                if path.is_file():
                    path.unlink()
                    deleted += 1

        if context.project_id is not None:
            return deleted

        keep_names = {"heatmap.json"}
        keep_suffixes = (".info.json", ".heatmap.json", ".md", ".txt", ".gitkeep")
        for path in context.metadata_dir.glob("*"):
            if not path.is_file() or path.name in keep_names or path.name.endswith(keep_suffixes):
                continue
            path.unlink()
            deleted += 1

        if context.candidate_file.exists():
            context.candidate_file.unlink()
            deleted += 1
        return deleted

    def _prepare_gemini_environment(self, context: PipelineContext) -> None:
        os.environ["UV_NATIVE_TLS"] = "1"
        if not allows_gemini(context.config.ai_mode):
            return
        bootstrap_ssl_certificates(quiet=True)
        if get_api_key():
            return
        if requires_gemini(context.config.ai_mode):
            raise WorkspacePreparationError(
                "Gemini mode is required but no Gemini API key is configured."
            )
        print("  Gemini is not configured; candidate selection will use its local fallback.")
