from __future__ import annotations

from pathlib import Path

from download_content import (
    HeatmapUnavailableError,
    build_youtube_heatmap_document,
    download_content,
    fetch_youtube_metadata,
)
from heatmap_contract import (
    atomic_write_json,
    load_heatmap_document,
    validate_youtube_metadata_identity,
)
from source_media_contract import SourceMediaManifestError, load_source_media_manifest

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
            if not context.source_url:
                raise DownloadStageError(
                    "Existing source media cannot be verified without a source URL."
                )
            try:
                current_metadata = fetch_youtube_metadata(context.source_url)
            except Exception as exc:
                raise DownloadStageError(
                    f"Source metadata refresh failed: {exc}"
                ) from exc
            try:
                current_video_id = validate_youtube_metadata_identity(current_metadata)
            except HeatmapUnavailableError as exc:
                raise DownloadStageError(
                    "Existing media cannot be safely reused and requires re-download."
                ) from exc

            try:
                source_manifest = load_source_media_manifest(
                    context.source_media_file,
                    workspace_path=context.workspace_path,
                    existing_video=existing_video,
                )
            except SourceMediaManifestError as exc:
                raise DownloadStageError(
                    "Existing media cannot be safely reused and requires re-download."
                ) from exc
            if source_manifest["video_id"].strip() != current_video_id.strip():
                raise DownloadStageError(
                    "Existing media cannot be safely reused and requires re-download."
                )
            if source_manifest["source_url"].strip() != context.source_url.strip():
                raise DownloadStageError(
                    "Existing media cannot be safely reused and requires re-download."
                )

            try:
                current_document = build_youtube_heatmap_document(current_metadata)
            except HeatmapUnavailableError:
                context.heatmap_file.unlink(missing_ok=True)
                raise

            try:
                stored_document = load_heatmap_document(context.heatmap_file)
            except HeatmapUnavailableError:
                stored_document = None

            if (
                stored_document is None
                or stored_document["video_id"].strip()
                != current_document["video_id"].strip()
            ):
                try:
                    atomic_write_json(context.heatmap_file, current_document)
                except OSError as exc:
                    raise DownloadStageError(
                        f"Refreshed heatmap could not be written: {exc}"
                    ) from exc
            return PipelineStageResult(
                stage=self.stage,
                success=True,
                message="Existing source media reused.",
                produced_artifacts=(
                    context.safe_artifact(existing_video),
                    context.safe_artifact(context.heatmap_file),
                ),
                metadata={"reused": True},
            )
        if not context.source_url:
            raise DownloadStageError("No source media exists and no source URL was provided.")

        if context.config.skip_download:
            print("  Source media is missing, so download will run despite --skip-download.")
        try:
            downloaded_media = download_content(
                context.source_url,
                str(context.input_dir),
                str(context.metadata_dir),
                prefer_1080=True,
                workspace_path=str(context.workspace_path),
            )
        except HeatmapUnavailableError:
            raise
        except Exception as exc:
            raise DownloadStageError(f"Source media download failed: {exc}") from exc

        if downloaded_media is None:
            raise DownloadStageError("Download completed without a usable video and audio stream.")
        media = Path(downloaded_media)
        if not locator.has_video(media) or not locator.has_audio(media):
            raise DownloadStageError("Download completed without a usable video and audio stream.")
        try:
            manifest = load_source_media_manifest(
                context.source_media_file,
                workspace_path=context.workspace_path,
                existing_video=media,
            )
            heatmap = load_heatmap_document(context.heatmap_file)
        except SourceMediaManifestError as exc:
            raise DownloadStageError("Downloaded media provenance could not be recorded.") from exc
        except HeatmapUnavailableError:
            raise
        if manifest["video_id"].strip() != heatmap["video_id"].strip():
            raise DownloadStageError("Downloaded media provenance does not match the heatmap.")
        artifacts = [context.safe_artifact(media)]
        if context.heatmap_file.exists():
            artifacts.append(context.safe_artifact(context.heatmap_file))
        artifacts.append(context.safe_artifact(context.source_media_file))
        return PipelineStageResult(
            stage=self.stage,
            success=True,
            message="Source media downloaded.",
            produced_artifacts=tuple(artifacts),
            metadata={"reused": False},
        )
