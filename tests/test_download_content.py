from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import download_content
from heatmap_contract import HeatmapUnavailableError


def valid_info() -> dict:
    return {
        "id": "current-video",
        "extractor": "youtube",
        "duration": 10.0,
        "heatmap": [
            {"start_time": 0.0, "end_time": 5.0, "value": 0.4},
            {"start_time": 5.0, "end_time": 10.0, "value": 0.8},
        ],
    }


class DownloadContentConfigurationTests(unittest.TestCase):
    def test_insecure_certificate_option_is_never_passed_to_ytdlp(self) -> None:
        captured_options: list[dict] = []

        class FakeYoutubeDL:
            def __init__(self, options: dict) -> None:
                captured_options.append(options)

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback) -> None:
                return None

            def extract_info(self, _url: str, *, download: bool):
                self.assert_download = download
                return valid_info()

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with (
                patch.object(download_content, "YoutubeDL", FakeYoutubeDL),
                patch.dict("os.environ", {}, clear=True),
                patch("sys.stdout", new=io.StringIO()),
            ):
                download_content.download_content(
                    "https://www.youtube.com/watch?v=test",
                    root / "input",
                    root / "metadata",
                )

            self.assertNotIn("nocheckcertificate", captured_options[-1])
            self.assertEqual(captured_options[-1]["compat_opts"], {"no-certifi"})

    def test_heatmap_comes_from_current_extract_info_result(self) -> None:
        class FakeYoutubeDL:
            def __init__(self, _options: dict) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback) -> None:
                return None

            def extract_info(self, _url: str, *, download: bool):
                self.assert_download = download
                return valid_info()

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_dir = root / "input"
            input_dir.mkdir()
            (input_dir / "stale.info.json").write_text(
                json.dumps(
                    {
                        **valid_info(),
                        "id": "stale-video",
                        "heatmap": [
                            {"start_time": 0.0, "end_time": 10.0, "value": 1.0}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(download_content, "YoutubeDL", FakeYoutubeDL),
                patch("sys.stdout", new=io.StringIO()),
            ):
                download_content.download_content(
                    "https://www.youtube.com/watch?v=current-video",
                    input_dir,
                    root / "metadata",
                )

            payload = json.loads((root / "metadata" / "heatmap.json").read_text("utf-8"))
            self.assertEqual(payload["video_id"], "current-video")
            self.assertEqual(payload["points"], valid_info()["heatmap"])

    def test_stale_info_file_is_not_used_when_current_result_has_no_heatmap(self) -> None:
        class FakeYoutubeDL:
            def __init__(self, _options: dict) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback) -> None:
                return None

            def extract_info(self, _url: str, *, download: bool):
                self.assert_download = download
                return {**valid_info(), "heatmap": None}

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_dir = root / "input"
            input_dir.mkdir()
            (input_dir / "stale.info.json").write_text(
                json.dumps(valid_info()),
                encoding="utf-8",
            )
            with (
                patch.object(download_content, "YoutubeDL", FakeYoutubeDL),
                patch("sys.stdout", new=io.StringIO()),
            ):
                with self.assertRaises(HeatmapUnavailableError):
                    download_content.download_content(
                        "https://www.youtube.com/watch?v=current-video",
                        input_dir,
                        root / "metadata",
                    )
            self.assertFalse((root / "metadata" / "heatmap.json").exists())

    def test_current_result_without_heatmap_invalidates_old_final_heatmap(self) -> None:
        class FakeYoutubeDL:
            def __init__(self, _options: dict) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback) -> None:
                return None

            def extract_info(self, _url: str, *, download: bool):
                return {**valid_info(), "heatmap": None}

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            metadata_dir = root / "metadata"
            metadata_dir.mkdir()
            old_heatmap = metadata_dir / "heatmap.json"
            old_heatmap.write_text(json.dumps(valid_info()), encoding="utf-8")
            with (
                patch.object(download_content, "YoutubeDL", FakeYoutubeDL),
                patch("sys.stdout", new=io.StringIO()),
            ):
                with self.assertRaises(HeatmapUnavailableError):
                    download_content.download_content(
                        "https://www.youtube.com/watch?v=current-video",
                        root / "input",
                        metadata_dir,
                    )
            self.assertFalse(old_heatmap.exists())


if __name__ == "__main__":
    unittest.main()
