from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import download_content


class DownloadContentConfigurationTests(unittest.TestCase):
    def test_certificate_override_is_explicit_and_disabled_by_default(self) -> None:
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
                return {}

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

            self.assertFalse(captured_options[-1]["nocheckcertificate"])

            with (
                patch.object(download_content, "YoutubeDL", FakeYoutubeDL),
                patch.dict("os.environ", {"YTDLP_NO_CHECK_CERTIFICATES": "true"}, clear=True),
                patch("sys.stdout", new=io.StringIO()),
            ):
                download_content.download_content(
                    "https://www.youtube.com/watch?v=test",
                    root / "input-override",
                    root / "metadata-override",
                )

            self.assertTrue(captured_options[-1]["nocheckcertificate"])


if __name__ == "__main__":
    unittest.main()
