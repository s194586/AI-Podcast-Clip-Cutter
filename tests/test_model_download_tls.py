from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from transcription.faster_whisper_backend import FasterWhisperBackend


class ModelDownloadTlsConfigurationTests(unittest.TestCase):
    def test_insecure_curl_is_explicit_and_disabled_by_default(self) -> None:
        backend = FasterWhisperBackend.__new__(FasterWhisperBackend)
        completed = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")

        with (
            patch("transcription.faster_whisper_backend.shutil.which", return_value="curl"),
            patch("transcription.faster_whisper_backend.subprocess.run", return_value=completed) as run,
            patch.dict("os.environ", {}, clear=True),
        ):
            backend._curl_fetch_text("https://example.invalid/model")
            self.assertNotIn("--insecure", run.call_args.args[0])

        with (
            patch("transcription.faster_whisper_backend.shutil.which", return_value="curl"),
            patch("transcription.faster_whisper_backend.subprocess.run", return_value=completed) as run,
            patch.dict(
                "os.environ",
                {"MODEL_DOWNLOAD_NO_CHECK_CERTIFICATES": "true"},
                clear=True,
            ),
        ):
            backend._curl_fetch_text("https://example.invalid/model")
            self.assertIn("--insecure", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
