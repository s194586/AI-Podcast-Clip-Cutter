import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from apps.api.services.clip_service import _clip_transcript_excerpt
from apps.api.services.timecode import format_timecode, parse_timecode


class PortfolioMvpRuntimeTests(unittest.TestCase):
    def test_timecode_supports_positions_durations_and_legacy_seconds(self):
        self.assertEqual(format_timecode(0), "00:00:00.0")
        self.assertEqual(format_timecode(3723.56), "01:02:03.6")
        self.assertEqual(format_timecode(83.6, duration=True), "01:23.6")
        self.assertEqual(parse_timecode("01:23.6"), 83.6)
        self.assertEqual(parse_timecode("01:02:03.5"), 3723.5)
        self.assertEqual(parse_timecode(83.6), 83.6)
        with self.assertRaises(ValueError):
            parse_timecode("not-a-time")

    def test_excerpt_uses_only_overlapping_canonical_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "final_transcript.json"
            transcript.write_text(
                json.dumps(
                    {
                        "segments": [
                            {"start": "00:00.0", "end": "00:05.0", "text": "before"},
                            {"start": "00:05.0", "end": "00:10.0", "text": "inside"},
                            {"start": "00:10.0", "end": "00:15.0", "text": "after"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            clip = SimpleNamespace(
                text="",
                edited_start=6.0,
                edited_end=11.0,
                project=SimpleNamespace(transcript_path=str(transcript)),
            )
            self.assertEqual(_clip_transcript_excerpt(clip), "inside after")


if __name__ == "__main__":
    unittest.main()
