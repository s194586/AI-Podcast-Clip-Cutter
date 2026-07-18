from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import download_content


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

            self.assertNotIn("nocheckcertificate", captured_options[-1])
            self.assertEqual(captured_options[-1]["compat_opts"], {"no-certifi"})


if __name__ == "__main__":
    unittest.main()
