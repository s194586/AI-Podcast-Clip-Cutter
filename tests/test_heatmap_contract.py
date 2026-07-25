from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import analyze_virals
import content_classifier
import download_content
import heatmap_contract
from apps.pipeline.stages import download as download_stage
from heatmap_contract import (
    HeatmapUnavailableError,
    atomic_write_json,
    build_youtube_heatmap,
    load_heatmap_points,
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
    return build_youtube_heatmap(valid_info(), extractor_version="test-version")


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

    def test_active_consumers_load_envelope_points(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "heatmap.json"
            atomic_write_json(path, valid_document())
            expected = valid_document()["points"]
            self.assertEqual(analyze_virals.load_heatmap(path), expected)
            self.assertEqual(content_classifier.load_heatmap(path), expected)

    def test_download_stage_propagates_heatmap_unavailable(self) -> None:
        context = SimpleNamespace(
            source_url="https://www.youtube.com/watch?v=abc123",
            input_dir=Path("input"),
            metadata_dir=Path("metadata"),
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

    def test_download_stage_rejects_existing_video_without_heatmap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            video.touch()
            context = SimpleNamespace(
                heatmap_file=root / "metadata" / "heatmap.json",
                safe_artifact=lambda path: str(path),
            )
            locator = SimpleNamespace(latest_video=lambda: video)
            with patch.object(download_stage, "MediaLocator", return_value=locator):
                with self.assertRaises(HeatmapUnavailableError):
                    download_stage.DownloadMediaStage().run(context)

    def test_download_stage_rejects_existing_video_with_provenance_free_heatmap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            video.touch()
            heatmap = root / "metadata" / "heatmap.json"
            heatmap.parent.mkdir()
            heatmap.write_text(json.dumps(valid_info()["heatmap"]), encoding="utf-8")
            context = SimpleNamespace(
                heatmap_file=heatmap,
                safe_artifact=lambda path: str(path),
            )
            locator = SimpleNamespace(latest_video=lambda: video)
            with patch.object(download_stage, "MediaLocator", return_value=locator):
                with self.assertRaises(HeatmapUnavailableError):
                    download_stage.DownloadMediaStage().run(context)

    def test_download_stage_reuses_existing_video_with_trusted_heatmap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video = root / "source.mp4"
            video.touch()
            heatmap = root / "metadata" / "heatmap.json"
            atomic_write_json(heatmap, valid_document())
            context = SimpleNamespace(
                heatmap_file=heatmap,
                safe_artifact=lambda path: str(path),
            )
            locator = SimpleNamespace(latest_video=lambda: video)
            with patch.object(download_stage, "MediaLocator", return_value=locator):
                result = download_stage.DownloadMediaStage().run(context)
            self.assertTrue(result.success)
            self.assertTrue(result.metadata["reused"])


if __name__ == "__main__":
    unittest.main()
