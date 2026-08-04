from __future__ import annotations

import os
from pathlib import Path
import unittest


@unittest.skipUnless(
    os.environ.get("RUN_PYANNOTE_INTEGRATION") == "1",
    "Set RUN_PYANNOTE_INTEGRATION=1 to run the real Pyannote smoke test.",
)
class PyannoteIntegrationSmokeTest(unittest.TestCase):
    def test_real_community_one_pipeline_returns_exclusive_turns(self):
        audio_value = str(os.environ.get("PYANNOTE_TEST_AUDIO") or "").strip()
        token = str(os.environ.get("HF_TOKEN") or "").strip()
        self.assertTrue(audio_value, "PYANNOTE_TEST_AUDIO is required when the integration flag is set.")
        self.assertTrue(token, "HF_TOKEN is required when the integration flag is set.")
        audio_path = Path(audio_value)
        self.assertTrue(audio_path.is_file(), "PYANNOTE_TEST_AUDIO must point to an existing audio file.")

        from diarization import DiarizationConfig, PyannoteDiarizationBackend

        backend = PyannoteDiarizationBackend(DiarizationConfig(), environment=os.environ)
        turns = backend.speaker_turns(audio_path)

        self.assertIsInstance(turns, list)
        for turn in turns:
            self.assertGreaterEqual(turn.start, 0)
            self.assertGreater(turn.end, turn.start)
            self.assertTrue(turn.raw_speaker)


if __name__ == "__main__":
    unittest.main()
