import unittest

from apps.review_agent.transcript_segments import normalize_transcript_segments
from transcription.segment_identity import canonical_segment_id


class TranscriptSegmentNormalizationTests(unittest.TestCase):
    def test_normalizes_object_payload_without_mutating_it(self):
        payload = {
            "segments": [
                {
                    "start": "00:00:05,5",
                    "end": "00:00:08.0",
                    "text": "  Hello\n  world  ",
                    "speaker_id": "speaker-2",
                    "importance": 4,
                    "chaos": True,
                }
            ]
        }

        self.assertEqual(
            normalize_transcript_segments(payload),
            [{
                "segment_id": canonical_segment_id(5.5, 8.0),
                "start": 5.5,
                "end": 8.0,
                "text": "Hello world",
                "speaker": "speaker-2",
                "importance": 4,
                "chaos": True,
            }],
        )
        self.assertEqual(payload["segments"][0]["text"], "  Hello\n  world  ")

    def test_normalizes_list_sorts_by_time_and_supports_speaker_id_variants(self):
        payload = [
            {"start": 10, "end": 12, "text": "second", "speakerId": "B"},
            {"start": "0:02", "end": "0:04", "text": "first", "speaker": "A"},
        ]

        normalized = normalize_transcript_segments(payload)

        self.assertEqual([segment["start"] for segment in normalized], [2.0, 10.0])
        self.assertEqual([segment["speaker"] for segment in normalized], ["A", "B"])

    def test_skips_invalid_entries_and_non_positive_ranges(self):
        payload = [
            None,
            {"start": "not-a-time", "end": 3, "text": "bad"},
            {"start": 3, "end": 3, "text": "empty"},
            {"start": 4, "end": 3, "text": "backwards"},
            {"start": 1, "end": 2, "text": "kept"},
        ]

        self.assertEqual(normalize_transcript_segments(payload)[0]["text"], "kept")


if __name__ == "__main__":
    unittest.main()
