from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import download_content
import heatmap_contract
from apps.pipeline.stages import download as download_stage
from apps.pipeline.stages.detect_heatmap_peaks import DetectHeatmapPeaksStage
from apps.pipeline.exceptions import DownloadStageError
from heatmap_contract import (
    HeatmapUnavailableError,
    atomic_write_json,
    build_youtube_heatmap,
    load_heatmap_document,
    load_heatmap_points,
)
from source_media_contract import (
    SourceMediaManifestError,
    build_source_media_manifest,
    load_source_media_manifest,
)


def valid_info() -> dict:
    return {
        "id": "abc123",
        "extractor": "youtube",
        "duration": 10.0,
        "heatmap": [
            {"start_time": 0.0, "end_time": 5.0, "value": 0.464},
            {"start_time": 5.0, "end_time": 10.0, "value": 0.8},
        ],
    }


def valid_document() -> dict:
    return build_youtube_heatmap(
        valid_info(), extractor_version=download_content.YT_DLP_VERSION
    )


def write_source_manifest(
    root: Path,
    video: Path,
    *,
    video_id: str = "abc123",
    source_url: str = "https://www.youtube.com/watch?v=abc123",
) -> Path:
    path = root / "metadata" / "source_media.json"
    atomic_write_json(
        path,
        build_source_media_manifest(
            video_id=video_id,
            source_url=source_url,
            media_file=video,
            workspace_path=root,
        ),
    )
    return path


def stage_context(root: Path, video: Path, source_url: str) -> SimpleNamespace:
    return SimpleNamespace(
        source_url=source_url,
        workspace_path=root,
        source_media_file=root / "metadata" / "source_media.json",
        heatmap_file=root / "metadata" / "heatmap.json",
        safe_artifact=lambda path: str(path),
    )


class HeatmapContractTests(unittest.TestCase):
    def test_valid_real_heatmap_has_provenance(self) -> None:
        document = valid_document()
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["source"], "youtube_most_replayed")
        self.assertIs(document["synthetic"], False)
        self.assertEqual(document["video_id"], "abc123")
        self.assertEqual(document["extractor"], "youtube")
        self.assertEqual(document["points"], valid_info()["heatmap"])

    def test_missing_none_and_empty_heatmap_are_unavailable(self) -> None:
        cases = ({}, {"heatmap": None}, {"heatmap": []})
        for replacement in cases:
            with self.subTest(replacement=replacement):
                info = valid_info()
                if replacement:
                    info.update(replacement)
                else:
                    info.pop("heatmap")
                with self.assertRaises(HeatmapUnavailableError):
                    build_youtube_heatmap(info, extractor_version="test")

    def test_invalid_point_is_rejected(self) -> None:
        info = valid_info()
        info["heatmap"] = [{"start_time": 0.0, "end_time": 1.0}]
        with self.assertRaises(HeatmapUnavailableError):
            build_youtube_heatmap(info, extractor_version="test")

    def test_nan_and_infinity_are_rejected(self) -> None:
        for field, value in (
            ("start_time", math.nan),
            ("end_time", math.inf),
            ("value", -math.inf),
        ):
            with self.subTest(field=field, value=value):
                info = valid_info()
                point = dict(info["heatmap"][0])
                point[field] = value
                info["heatmap"] = [point]
                with self.assertRaises(HeatmapUnavailableError):
                    build_youtube_heatmap(info, extractor_version="test")

    def test_out_of_range_value_is_rejected(self) -> None:
        for value in (-0.01, 1.01):
            with self.subTest(value=value):
                info = valid_info()
                info["heatmap"] = [
                    {"start_time": 0.0, "end_time": 1.0, "value": value}
                ]
                with self.assertRaises(HeatmapUnavailableError):
                    build_youtube_heatmap(info, extractor_version="test")

    def test_non_positive_point_duration_is_rejected(self) -> None:
        for end in (0.0, -1.0):
            with self.subTest(end=end):
                info = valid_info()
                info["heatmap"] = [
                    {"start_time": 0.0, "end_time": end, "value": 0.5}
                ]
                with self.assertRaises(HeatmapUnavailableError):
                    build_youtube_heatmap(info, extractor_version="test")

    def test_unordered_and_past_duration_points_are_rejected(self) -> None:
        cases = (
            [
                {"start_time": 5.0, "end_time": 6.0, "value": 0.5},
                {"start_time": 1.0, "end_time": 2.0, "value": 0.5},
            ],
            [{"start_time": 9.0, "end_time": 11.01, "value": 0.5}],
        )
        for points in cases:
            with self.subTest(points=points):
                info = valid_info()
                info["heatmap"] = points
                with self.assertRaises(HeatmapUnavailableError):
                    build_youtube_heatmap(info, extractor_version="test")

    def test_old_provenance_free_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "heatmap.json"
            path.write_text(json.dumps(valid_info()["heatmap"]), encoding="utf-8")
            with self.assertRaises(HeatmapUnavailableError):
                load_heatmap_points(path)

    def test_missing_or_empty_extractor_version_is_rejected(self) -> None:
        for extractor_version in (None, ""):
            with self.subTest(extractor_version=extractor_version):
                document = valid_document()
                if extractor_version is None:
                    document.pop("extractor_version")
                else:
                    document["extractor_version"] = extractor_version
                with self.assertRaises(HeatmapUnavailableError):
                    heatmap_contract.validate_heatmap_document(document)

    def test_malformed_json_is_wrapped_as_heatmap_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "heatmap.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(HeatmapUnavailableError) as raised:
                load_heatmap_points(path)
        self.assertIsInstance(raised.exception.__cause__, json.JSONDecodeError)

    def test_load_heatmap_document_returns_validated_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "heatmap.json"
            atomic_write_json(path, valid_document())
            self.assertEqual(load_heatmap_document(path), valid_document())

    def test_production_placeholder_function_does_not_exist(self) -> None:
        self.assertFalse(hasattr(download_content, "create_placeholder_heatmap"))
        self.assertFalse(hasattr(download_content, "recursive_find_key"))

    def test_atomic_write_publishes_complete_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "heatmap.json"
            atomic_write_json(path, valid_document())
            self.assertEqual(json.loads(path.read_text("utf-8")), valid_document())
            self.assertEqual(list(path.parent.glob(".heatmap.json.*.tmp")), [])

    def test_atomic_write_failure_preserves_previous_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "heatmap.json"
            path.write_text('{"old": true}', encoding="utf-8")
            with patch.object(heatmap_contract.os, "replace", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    atomic_write_json(path, valid_document())
            self.assertEqual(path.read_text("utf-8"), '{"old": true}')
            self.assertEqual(list(path.parent.glob(".heatmap.json.*.tmp")), [])

    def test_active_peak_stage_consumes_validated_heatmap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            heatmap_file = root / "metadata" / "heatmap.json"
            peaks_file = root / "metadata" / "heatmap_peaks.json"
            atomic_write_json(heatmap_file, valid_document())
            context = SimpleNamespace(
                heatmap_file=heatmap_file,
                heatmap_peaks_file=peaks_file,
                safe_artifact=lambda path: Path(path).relative_to(root).as_posix(),
            )

            result = DetectHeatmapPeaksStage().run(context)

            self.assertTrue(result.success)
            self.assertEqual(result.produced_artifacts, ("metadata/heatmap_peaks.json",))
            self.assertTrue(peaks_file.exists())

    def test_download_stage_propagates_heatmap_unavailable(self) -> None:
        context = SimpleNamespace(
            source_url="https://www.youtube.com/watch?v=abc123",
            input_dir=Path("input"),
            metadata_dir=Path("metadata"),
            workspace_path=Path("."),
            config=SimpleNamespace(skip_download=False),
        )
        locator = SimpleNamespace(latest_video=lambda: None)
        error = HeatmapUnavailableError("No real heatmap.")
        with (
            patch.object(download_stage, "MediaLocator", return_value=locator),
            patch.object(download_stage, "download_content", side_effect=error),
        ):
            with self.assertRaises(HeatmapUnavailableError) as raised:
                download_stage.DownloadMediaStage().run(context)
        self.assertIs(raised.exception, error)

    def test_download_stage_wraps_full_download_write_error(self) -> None:
        context = SimpleNamespace(
            source_url="https://www.youtube.com/watch?v=abc123",
            input_dir=Path("input"),
            metadata_dir=Path("metadata"),
            workspace_path=Path("."),
            config=SimpleNamespace(skip_download=False),
        )
        locator = SimpleNamespace(latest_video=lambda: None)
        error = OSError("manifest write failed")
        with (
            patch.object(download_stage, "MediaLocator", return_value=locator),
            patch.object(download_stage, "download_content", side_effect=error),
        ):
            with self.assertRaises(DownloadStageError) as raised:
                download_stage.DownloadMediaStage().run(context)
        self.assertIs(raised.exception.__cause__, error)

    def test_download_stage_refreshes_missing_heatmap_for_existing_video(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            video.touch()
            context = stage_context(root, video, "https://www.youtube.com/watch?v=abc123")
            write_source_manifest(root, video)
            locator = SimpleNamespace(latest_video=lambda: video)
            with (
                patch.object(download_stage, "MediaLocator", return_value=locator),
                patch.object(
                    download_stage,
                    "fetch_youtube_metadata",
                    return_value=valid_info(),
                ) as fetch_metadata,
            ):
                result = download_stage.DownloadMediaStage().run(context)
            self.assertTrue(result.success)
            self.assertEqual(load_heatmap_document(context.heatmap_file), valid_document())
            fetch_metadata.assert_called_once_with(context.source_url)

    def test_download_stage_refreshes_provenance_free_heatmap_for_existing_video(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            video.touch()
            heatmap = root / "metadata" / "heatmap.json"
            heatmap.parent.mkdir()
            heatmap.write_text(json.dumps(valid_info()["heatmap"]), encoding="utf-8")
            context = stage_context(root, video, "https://www.youtube.com/watch?v=abc123")
            write_source_manifest(root, video)
            locator = SimpleNamespace(latest_video=lambda: video)
            with (
                patch.object(download_stage, "MediaLocator", return_value=locator),
                patch.object(
                    download_stage,
                    "fetch_youtube_metadata",
                    return_value=valid_info(),
                ) as fetch_metadata,
            ):
                download_stage.DownloadMediaStage().run(context)
            self.assertEqual(load_heatmap_document(heatmap), valid_document())

    def test_download_stage_refreshes_malformed_heatmap_for_existing_video(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            video.touch()
            heatmap = root / "metadata" / "heatmap.json"
            heatmap.parent.mkdir()
            heatmap.write_text("{malformed", encoding="utf-8")
            context = stage_context(root, video, "https://www.youtube.com/watch?v=abc123")
            write_source_manifest(root, video)
            locator = SimpleNamespace(latest_video=lambda: video)
            with (
                patch.object(download_stage, "MediaLocator", return_value=locator),
                patch.object(
                    download_stage,
                    "fetch_youtube_metadata",
                    return_value=valid_info(),
                ) as fetch_metadata,
            ):
                download_stage.DownloadMediaStage().run(context)
            self.assertEqual(load_heatmap_document(heatmap), valid_document())

    def test_download_stage_reuses_matching_trusted_heatmap_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            video.touch()
            heatmap = root / "metadata" / "heatmap.json"
            atomic_write_json(heatmap, valid_document())
            context = stage_context(root, video, "https://www.youtube.com/watch?v=abc123")
            write_source_manifest(root, video)
            locator = SimpleNamespace(latest_video=lambda: video)
            with (
                patch.object(download_stage, "MediaLocator", return_value=locator),
                patch.object(
                    download_stage,
                    "fetch_youtube_metadata",
                    return_value=valid_info(),
                ) as fetch_metadata,
                patch.object(download_stage, "atomic_write_json") as write_json,
                patch.object(download_stage, "download_content") as download_media,
            ):
                result = download_stage.DownloadMediaStage().run(context)
            self.assertTrue(result.success)
            self.assertTrue(result.metadata["reused"])
            write_json.assert_not_called()
            download_media.assert_not_called()
            fetch_metadata.assert_called_once_with(context.source_url)

    def test_download_stage_refreshes_heatmap_with_different_video_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            video.touch()
            heatmap = root / "metadata" / "heatmap.json"
            stale_document = valid_document()
            stale_document["video_id"] = "stale-video"
            atomic_write_json(heatmap, stale_document)
            current_metadata = valid_info()
            current_metadata["id"] = "  current-video  "
            context = stage_context(root, video, "https://www.youtube.com/watch?v=current-video")
            write_source_manifest(
                root,
                video,
                video_id="current-video",
                source_url=context.source_url,
            )
            locator = SimpleNamespace(latest_video=lambda: video)
            with (
                patch.object(download_stage, "MediaLocator", return_value=locator),
                patch.object(
                    download_stage,
                    "fetch_youtube_metadata",
                    return_value=current_metadata,
                ),
            ):
                download_stage.DownloadMediaStage().run(context)
            self.assertEqual(
                load_heatmap_document(heatmap)["video_id"], "  current-video  "
            )

    def test_download_stage_invalidates_heatmap_when_metadata_has_no_heatmap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            video.touch()
            heatmap = root / "metadata" / "heatmap.json"
            atomic_write_json(heatmap, valid_document())
            context = stage_context(root, video, "https://www.youtube.com/watch?v=abc123")
            manifest = write_source_manifest(root, video)
            locator = SimpleNamespace(latest_video=lambda: video)
            with (
                patch.object(download_stage, "MediaLocator", return_value=locator),
                patch.object(
                    download_stage,
                    "fetch_youtube_metadata",
                    return_value={**valid_info(), "heatmap": None},
                ),
            ):
                with self.assertRaises(HeatmapUnavailableError):
                    download_stage.DownloadMediaStage().run(context)
            self.assertFalse(heatmap.exists())
            self.assertTrue(manifest.exists())

    def test_download_stage_rejects_manifest_with_different_video_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            video.touch()
            context = stage_context(root, video, "https://www.youtube.com/watch?v=current-video")
            write_source_manifest(
                root,
                video,
                video_id="other-video",
                source_url="https://www.youtube.com/watch?v=other-video",
            )
            atomic_write_json(context.heatmap_file, valid_document())
            original_heatmap = context.heatmap_file.read_bytes()
            current_metadata = valid_info()
            current_metadata["id"] = "current-video"
            locator = SimpleNamespace(latest_video=lambda: video)
            with (
                patch.object(download_stage, "MediaLocator", return_value=locator),
                patch.object(
                    download_stage,
                    "fetch_youtube_metadata",
                    return_value=current_metadata,
                ),
            ):
                with self.assertRaises(DownloadStageError):
                    download_stage.DownloadMediaStage().run(context)
            self.assertEqual(context.heatmap_file.read_bytes(), original_heatmap)

    def test_download_stage_rejects_manifest_with_different_source_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            video.touch()
            context = stage_context(root, video, "https://www.youtube.com/watch?v=abc123")
            write_source_manifest(
                root,
                video,
                source_url="https://www.youtube.com/watch?v=other",
            )
            atomic_write_json(context.heatmap_file, valid_document())
            original_heatmap = context.heatmap_file.read_bytes()
            locator = SimpleNamespace(latest_video=lambda: video)
            with (
                patch.object(download_stage, "MediaLocator", return_value=locator),
                patch.object(
                    download_stage,
                    "fetch_youtube_metadata",
                    return_value=valid_info(),
                ),
            ):
                with self.assertRaises(DownloadStageError):
                    download_stage.DownloadMediaStage().run(context)
            self.assertEqual(context.heatmap_file.read_bytes(), original_heatmap)

    def test_download_stage_rejects_manifest_for_different_media_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            other_video = root / "other.mp4"
            video.touch()
            other_video.touch()
            context = stage_context(root, video, "https://www.youtube.com/watch?v=abc123")
            write_source_manifest(root, other_video)
            locator = SimpleNamespace(latest_video=lambda: video)
            with (
                patch.object(download_stage, "MediaLocator", return_value=locator),
                patch.object(
                    download_stage,
                    "fetch_youtube_metadata",
                    return_value=valid_info(),
                ),
            ):
                with self.assertRaises(DownloadStageError):
                    download_stage.DownloadMediaStage().run(context)

    def test_download_stage_rejects_existing_video_without_source_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            video.touch()
            context = stage_context(root, video, "https://www.youtube.com/watch?v=abc123")
            locator = SimpleNamespace(latest_video=lambda: video)
            with (
                patch.object(download_stage, "MediaLocator", return_value=locator),
                patch.object(
                    download_stage,
                    "fetch_youtube_metadata",
                    return_value=valid_info(),
                ),
            ):
                with self.assertRaises(DownloadStageError):
                    download_stage.DownloadMediaStage().run(context)

    def test_invalid_manifest_precedes_missing_heatmap_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            video.touch()
            context = stage_context(root, video, "https://www.youtube.com/watch?v=abc123")
            atomic_write_json(context.heatmap_file, valid_document())
            original_heatmap = context.heatmap_file.read_bytes()
            locator = SimpleNamespace(latest_video=lambda: video)
            with (
                patch.object(download_stage, "MediaLocator", return_value=locator),
                patch.object(
                    download_stage,
                    "fetch_youtube_metadata",
                    return_value={**valid_info(), "heatmap": None},
                ) as fetch_metadata,
            ):
                with self.assertRaises(DownloadStageError):
                    download_stage.DownloadMediaStage().run(context)
            self.assertEqual(context.heatmap_file.read_bytes(), original_heatmap)
            fetch_metadata.assert_called_once_with(context.source_url)

    def test_mismatched_manifest_precedes_missing_heatmap_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            video.touch()
            context = stage_context(root, video, "https://www.youtube.com/watch?v=abc123")
            write_source_manifest(root, video, video_id="other-video")
            atomic_write_json(context.heatmap_file, valid_document())
            original_heatmap = context.heatmap_file.read_bytes()
            locator = SimpleNamespace(latest_video=lambda: video)
            with (
                patch.object(download_stage, "MediaLocator", return_value=locator),
                patch.object(
                    download_stage,
                    "fetch_youtube_metadata",
                    return_value={**valid_info(), "heatmap": None},
                ),
            ):
                with self.assertRaises(DownloadStageError):
                    download_stage.DownloadMediaStage().run(context)
            self.assertEqual(context.heatmap_file.read_bytes(), original_heatmap)

    def test_metadata_transport_error_preserves_existing_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            video.touch()
            context = stage_context(root, video, "https://www.youtube.com/watch?v=abc123")
            manifest = write_source_manifest(root, video)
            atomic_write_json(context.heatmap_file, valid_document())
            original_heatmap = context.heatmap_file.read_bytes()
            error = TimeoutError("yt-dlp timed out")
            locator = SimpleNamespace(latest_video=lambda: video)
            with (
                patch.object(download_stage, "MediaLocator", return_value=locator),
                patch.object(
                    download_stage,
                    "fetch_youtube_metadata",
                    side_effect=error,
                ),
            ):
                with self.assertRaises(DownloadStageError) as raised:
                    download_stage.DownloadMediaStage().run(context)
            self.assertIs(raised.exception.__cause__, error)
            self.assertEqual(context.heatmap_file.read_bytes(), original_heatmap)
            self.assertTrue(manifest.exists())

    def test_heatmap_refresh_write_error_is_download_stage_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            video.touch()
            context = stage_context(root, video, "https://www.youtube.com/watch?v=abc123")
            write_source_manifest(root, video)
            error = OSError("disk full")
            locator = SimpleNamespace(latest_video=lambda: video)
            with (
                patch.object(download_stage, "MediaLocator", return_value=locator),
                patch.object(
                    download_stage,
                    "fetch_youtube_metadata",
                    return_value=valid_info(),
                ),
                patch.object(download_stage, "atomic_write_json", side_effect=error),
            ):
                with self.assertRaises(DownloadStageError) as raised:
                    download_stage.DownloadMediaStage().run(context)
            self.assertIs(raised.exception.__cause__, error)

    def test_download_stage_uses_media_returned_by_current_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "input" / "current.mp4"
            video.parent.mkdir()
            video.touch()
            context = stage_context(root, video, "https://www.youtube.com/watch?v=abc123")
            context.input_dir = root / "input"
            context.metadata_dir = root / "metadata"
            context.config = SimpleNamespace(skip_download=False)
            write_source_manifest(root, video)
            atomic_write_json(context.heatmap_file, valid_document())
            locator = SimpleNamespace(
                latest_video=lambda: None,
                has_video=lambda path: Path(path) == video,
                has_audio=lambda path: Path(path) == video,
            )
            with (
                patch.object(download_stage, "MediaLocator", return_value=locator),
                patch.object(download_stage, "download_content", return_value=video),
            ):
                result = download_stage.DownloadMediaStage().run(context)
            self.assertIn(str(video), result.produced_artifacts)

    def test_source_manifest_rejects_unsafe_or_missing_media_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "input" / "source.mp4"
            video.parent.mkdir()
            video.touch()
            manifest = root / "metadata" / "source_media.json"
            for media_file in ("C:/outside.mp4", "../outside.mp4", "input/missing.mp4"):
                with self.subTest(media_file=media_file):
                    atomic_write_json(
                        manifest,
                        {
                            "schema_version": 1,
                            "source": "youtube",
                            "video_id": "abc123",
                            "source_url": "https://www.youtube.com/watch?v=abc123",
                            "media_file": media_file,
                        },
                    )
                    with self.assertRaises(SourceMediaManifestError):
                        load_source_media_manifest(
                            manifest, workspace_path=root, existing_video=video
                        )


if __name__ == "__main__":
    unittest.main()
