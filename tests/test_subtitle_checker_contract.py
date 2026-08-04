from __future__ import annotations

import json
import io
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout

from subtitler_checker import fix_transcription
from transcription.segment_identity import canonical_segment_id


class SubtitleCheckerTranscriptContractTests(unittest.TestCase):
    def test_fix_preserves_top_level_and_segment_fields_and_refreshes_derived_fields(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            transcript_path = root / "final_transcript.json"
            report_path = root / "report.json"
            audio_path = root / "audio.wav"
            original = {
                "transcript_schema_version": 2,
                "segment_id_scheme": "canonical_time_range_sha256",
                "segment_id_version": 1,
                "metadata": {
                    "diarization_mode": "pyannote",
                    "diarization_backend": "pyannote",
                    "custom": "preserve-me",
                },
                "segments": [
                    {
                        "segment_id": canonical_segment_id(0.0, 1.0),
                        "start": "00:00.00",
                        "end": "00:01.00",
                        "text": "First sentence.",
                        "speaker": "Speaker 0",
                        "importance": 5,
                        "chaos": True,
                        "custom_segment_field": "keep",
                        "words": [{"start": "00:00.00", "end": "00:01.00", "text": "First sentence."}],
                    },
                    {
                        "segment_id": canonical_segment_id(0.5, 1.5),
                        "start": "00:00.50",
                        "end": "00:01.50",
                        "text": "Second sentence.",
                        "speaker": "Speaker 1",
                        "importance": 4,
                        "chaos": False,
                        "custom_segment_field": "also-keep",
                        "words": [{"start": "00:00.50", "end": "00:01.50", "text": "Second sentence."}],
                    },
                ],
            }
            transcript_path.write_text(json.dumps(original), encoding="utf-8")
            report_path.write_text(json.dumps({"issues": [], "samples": [], "summary": {}}), encoding="utf-8")

            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                with redirect_stdout(io.StringIO()):
                    fix_transcription(
                        transcript_path,
                        report_path,
                        audio_path,
                        api_key="",
                        model_name="unused",
                    )
            finally:
                os.chdir(previous_cwd)

            fixed = json.loads(transcript_path.read_text(encoding="utf-8"))
            self.assertEqual(fixed["transcript_schema_version"], original["transcript_schema_version"])
            self.assertEqual(fixed["segment_id_scheme"], original["segment_id_scheme"])
            self.assertEqual(fixed["segment_id_version"], original["segment_id_version"])
            self.assertEqual(fixed["metadata"], original["metadata"])

            first, second = fixed["segments"]
            self.assertEqual(first["speaker"], "Speaker 0")
            self.assertEqual(first["importance"], 5)
            self.assertTrue(first["chaos"])
            self.assertEqual(first["custom_segment_field"], "keep")
            self.assertEqual(first["words"], original["segments"][0]["words"])

            self.assertEqual(second["start"], "00:01.00")
            self.assertEqual(second["end"], "00:01.50")
            self.assertEqual(second["segment_id"], canonical_segment_id(1.0, 1.5))
            self.assertNotIn("words", second)
            self.assertEqual(second["speaker"], "Speaker 1")
            self.assertEqual(second["importance"], 4)
            self.assertFalse(second["chaos"])
            self.assertEqual(second["custom_segment_field"], "also-keep")


if __name__ == "__main__":
    unittest.main()
