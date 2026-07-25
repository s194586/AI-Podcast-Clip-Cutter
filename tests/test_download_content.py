from __future__ import annotations

import io
import json
import os
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
    def test_current_media_rejects_path_outside_input_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_dir = root / "input"
            input_dir.mkdir()
            outside_mp4 = root / "outside.mp4"
            outside_mp4.touch()
            with (
                patch.object(download_content, "file_has_audio", return_value=True),
                patch.object(download_content, "file_has_video", return_value=True),
            ):
                media = download_content.current_downloaded_media_file(
                    {"filepath": str(outside_mp4)},
                    object(),
                    [],
                    input_dir,
                )
            self.assertIsNone(media)

    def test_download_without_current_mp4_publishes_no_documents(self) -> None:
        class FakeYoutubeDL:
            def __init__(self, _options: dict) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback) -> None:
                return None

            def extract_info(self, _url: str, *, download: bool):
                return valid_info()

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with (
                patch.object(download_content, "YoutubeDL", FakeYoutubeDL),
                patch("sys.stdout", new=io.StringIO()),
            ):
                media = download_content.download_content(
                    "https://www.youtube.com/watch?v=current-video",
                    root / "input",
                    root / "metadata",
                )
            self.assertIsNone(media)
            self.assertFalse((root / "metadata" / "source_media.json").exists())
            self.assertFalse((root / "metadata" / "heatmap.json").exists())

    def test_source_manifest_write_failure_leaves_heatmap_unchanged(self) -> None:
        class FakeYoutubeDL:
            def __init__(self, _options: dict) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback) -> None:
                return None

            def extract_info(self, _url: str, *, download: bool):
                info = valid_info()
                info["filepath"] = str(final_mp4)
                return info

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_dir = root / "input"
            input_dir.mkdir()
            final_mp4 = input_dir / "current.mp4"
            final_mp4.touch()
            metadata_dir = root / "metadata"
            metadata_dir.mkdir()
            old_heatmap = metadata_dir / "heatmap.json"
            old_heatmap.write_bytes(b'{"old": true}\n')
            original_write = download_content.atomic_write_json
            write_error = OSError("manifest write failed")

            def fail_manifest(path, payload):
                if Path(path).name == "source_media.json":
                    raise write_error
                original_write(path, payload)

            with (
                patch.object(download_content, "YoutubeDL", FakeYoutubeDL),
                patch.object(download_content, "file_has_audio", return_value=True),
                patch.object(download_content, "file_has_video", return_value=True),
                patch.object(download_content, "atomic_write_json", side_effect=fail_manifest),
                patch("sys.stdout", new=io.StringIO()),
            ):
                with self.assertRaises(OSError) as raised:
                    download_content.download_content(
                        "https://www.youtube.com/watch?v=current-video",
                        input_dir,
                        metadata_dir,
                        workspace_path=root,
                    )
            self.assertIs(raised.exception, write_error)
            self.assertEqual(old_heatmap.read_bytes(), b'{"old": true}\n')

    def test_heatmap_write_failure_keeps_valid_source_manifest(self) -> None:
        class FakeYoutubeDL:
            def __init__(self, _options: dict) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback) -> None:
                return None

            def extract_info(self, _url: str, *, download: bool):
                info = valid_info()
                info["filepath"] = str(final_mp4)
                return info

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_dir = root / "input"
            input_dir.mkdir()
            final_mp4 = input_dir / "current.mp4"
            final_mp4.touch()
            write_error = OSError("heatmap write failed")
            original_write = download_content.atomic_write_json

            def fail_heatmap(path, payload):
                if Path(path).name == "heatmap.json":
                    raise write_error
                original_write(path, payload)

            with (
                patch.object(download_content, "YoutubeDL", FakeYoutubeDL),
                patch.object(download_content, "file_has_audio", return_value=True),
                patch.object(download_content, "file_has_video", return_value=True),
                patch.object(download_content, "atomic_write_json", side_effect=fail_heatmap),
                patch("sys.stdout", new=io.StringIO()),
            ):
                with self.assertRaises(OSError) as raised:
                    download_content.download_content(
                        "https://www.youtube.com/watch?v=current-video",
                        input_dir,
                        root / "metadata",
                        workspace_path=root,
                    )
            self.assertIs(raised.exception, write_error)
            manifest = json.loads((root / "metadata" / "source_media.json").read_text("utf-8"))
            self.assertEqual(manifest["media_file"], "input/current.mp4")

    def test_full_download_writes_source_manifest_for_final_mp4(self) -> None:
        calls: list[bool] = []
        captured_options: list[dict] = []

        class FakeYoutubeDL:
            def __init__(self, options: dict) -> None:
                captured_options.append(options)

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback) -> None:
                return None

            def extract_info(self, _url: str, *, download: bool):
                calls.append(download)
                info = valid_info()
                info["filepath"] = str(final_mp4)
                return info

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_dir = root / "input"
            input_dir.mkdir()
            final_mp4 = input_dir / "final.mp4"
            final_mp4.touch()
            stale_mp4 = input_dir / "stale.mp4"
            stale_mp4.touch()
            os.utime(stale_mp4, (final_mp4.stat().st_atime, final_mp4.stat().st_mtime + 10))
            with (
                patch.object(download_content, "YoutubeDL", FakeYoutubeDL),
                patch.object(
                    download_content,
                    "find_latest_file",
                    side_effect=AssertionError("mtime-based media lookup must not run"),
                ),
                patch.object(download_content, "file_has_audio", return_value=True),
                patch.object(download_content, "file_has_video", return_value=True),
                patch("sys.stdout", new=io.StringIO()),
            ):
                downloaded_media = download_content.download_content(
                    "https://www.youtube.com/watch?v=current-video",
                    input_dir,
                    root / "metadata",
                    workspace_path=root,
                )

            manifest = json.loads((root / "metadata" / "source_media.json").read_text("utf-8"))
            heatmap = json.loads((root / "metadata" / "heatmap.json").read_text("utf-8"))
            self.assertEqual(calls, [True])
            self.assertEqual(downloaded_media, final_mp4.resolve())
            self.assertIn("%(id)s", captured_options[0]["outtmpl"])
            self.assertEqual(manifest["media_file"], "input/final.mp4")
            self.assertEqual(manifest["video_id"], heatmap["video_id"])
            self.assertEqual(list((root / "metadata").glob(".source_media.json.*.tmp")), [])

    def test_metadata_only_fetch_uses_download_false(self) -> None:
        calls: list[bool] = []

        class FakeYoutubeDL:
            def __init__(self, _options: dict) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback) -> None:
                return None

            def extract_info(self, _url: str, *, download: bool):
                calls.append(download)
                return valid_info()

        with patch.object(download_content, "YoutubeDL", FakeYoutubeDL):
            document = download_content.fetch_youtube_heatmap_metadata(
                "https://www.youtube.com/watch?v=current-video"
            )

        self.assertEqual(calls, [False])
        self.assertEqual(document["video_id"], "current-video")

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
                info = valid_info()
                info["filepath"] = str(current_mp4)
                return info

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_dir = root / "input"
            input_dir.mkdir()
            current_mp4 = input_dir / "current.mp4"
            current_mp4.touch()
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
                patch.object(download_content, "file_has_audio", return_value=True),
                patch.object(download_content, "file_has_video", return_value=True),
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
                return {**valid_info(), "heatmap": None, "filepath": str(current_mp4)}

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_dir = root / "input"
            input_dir.mkdir()
            current_mp4 = input_dir / "current.mp4"
            current_mp4.touch()
            (input_dir / "stale.info.json").write_text(
                json.dumps(valid_info()),
                encoding="utf-8",
            )
            with (
                patch.object(download_content, "YoutubeDL", FakeYoutubeDL),
                patch.object(download_content, "file_has_audio", return_value=True),
                patch.object(download_content, "file_has_video", return_value=True),
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
                return {**valid_info(), "heatmap": None, "filepath": str(current_mp4)}

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_dir = root / "input"
            input_dir.mkdir()
            current_mp4 = input_dir / "current.mp4"
            current_mp4.touch()
            metadata_dir = root / "metadata"
            metadata_dir.mkdir()
            old_heatmap = metadata_dir / "heatmap.json"
            old_heatmap.write_text(json.dumps(valid_info()), encoding="utf-8")
            with (
                patch.object(download_content, "YoutubeDL", FakeYoutubeDL),
                patch.object(download_content, "file_has_audio", return_value=True),
                patch.object(download_content, "file_has_video", return_value=True),
                patch("sys.stdout", new=io.StringIO()),
            ):
                with self.assertRaises(HeatmapUnavailableError):
                    download_content.download_content(
                        "https://www.youtube.com/watch?v=current-video",
                        input_dir,
                        metadata_dir,
                    )
            self.assertFalse(old_heatmap.exists())


if __name__ == "__main__":
    unittest.main()
