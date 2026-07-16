from __future__ import annotations

from download_content import download_content

from ..context import PipelineContext
from ..exceptions import DownloadStageError
from ..results import PipelineStageResult
from .common import MediaLocator


class DownloadMediaStage:
    stage = "downloading"

    def run(self, context: PipelineContext) -> PipelineStageResult:
        locator = MediaLocator(context)
        existing_video = locator.latest_video()
        if existing_video is not None:
            return PipelineStageResult(
                stage=self.stage,
                success=True,
                message="Existing source media reused.",
                produced_artifacts=(context.safe_artifact(existing_video),),
                metadata={"reused": True},
            )
        if not context.source_url:
            raise DownloadStageError("No source media exists and no source URL was provided.")

        if context.config.skip_download:
            print("  Source media is missing, so download will run despite --skip-download.")
        try:
            download_content(
                context.source_url,
                str(context.input_dir),
                str(context.metadata_dir),
                prefer_1080=True,
            )
        except Exception as exc:
            raise DownloadStageError(f"Source media download failed: {exc}") from exc

        media = locator.latest_video()
        if media is None:
            raise DownloadStageError("Download completed without a usable video and audio stream.")
        artifacts = [context.safe_artifact(media)]
        if context.heatmap_file.exists():
            artifacts.append(context.safe_artifact(context.heatmap_file))
        return PipelineStageResult(
            stage=self.stage,
            success=True,
            message="Source media downloaded.",
            produced_artifacts=tuple(artifacts),
            metadata={"reused": False},
        )
