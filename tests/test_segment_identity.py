import copy
import unittest
from pathlib import Path

from apps.review_agent.context import build_clip_transcript_context_from_segments, segment_map
from apps.review_agent.transcript_segments import (
    TranscriptSegmentValidationError,
    normalize_transcript_segments,
)
from transcription.base import TranscriptSegment, TranscriptWord, TranscriptionResult, parse_time_to_seconds, sec_to_hms
from transcription.segment_identity import (
    SEGMENT_ID_SCHEME,
    SEGMENT_ID_VERSION,
    TRANSCRIPT_SCHEMA_VERSION,
    SegmentIdentityError,
    canonical_segment_id,
    canonical_segment_id_from_centiseconds,
    canonical_time_range,
    time_to_centiseconds,
)


class SegmentIdentityTests(unittest.TestCase):
    def test_ids_depend_only_on_canonical_time_range(self):
        segment_id = canonical_segment_id(5.0, 8.0)
        self.assertEqual(segment_id, canonical_segment_id(5.0, 8.0))
        self.assertRegex(segment_id, r"^seg_v1_[0-9a-f]{64}$")
        original = [
            {"start": 10, "end": 12, "text": "Second.", "speaker": "A"},
            {"start": 20, "end": 22, "text": "Third.", "speaker": "B"},
        ]
        changed = [
            {"start": 1, "end": 2, "text": "Inserted.", "speaker": "Z"},
            {"start": 20, "end": 22, "text": "Third changed!", "speaker": "Other"},
            {"start": 10, "end": 12, "text": "Second changed!", "speaker": "Other"},
        ]
        original_ids = {item["segment_id"] for item in normalize_transcript_segments(original)}
        changed_ids = {item["segment_id"] for item in normalize_transcript_segments(changed)}
        self.assertTrue(original_ids <= changed_ids)

    def test_centisecond_rounding_and_invalid_ranges_are_deterministic(self):
        self.assertEqual(time_to_centiseconds("0.0049"), 0)
        self.assertEqual(time_to_centiseconds("0.005"), 1)
        self.assertEqual(time_to_centiseconds("1.005"), 101)
        self.assertEqual(canonical_time_range("00:00:01,005", "00:00:02,005"), (101, 201))
        for start, end in ((True, 1), ("nan", 1), (float("inf"), 1), (-0.01, 1), (2, 2), (3, 2)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(SegmentIdentityError):
                    canonical_segment_id(start, end)

    def test_general_timestamp_helpers_remain_compatible_but_segment_ranges_are_strict(self):
        with self.assertRaises(SegmentIdentityError):
            TranscriptSegment(start=-0.01, end=1, text="Invalid segment").to_dict()
        self.assertEqual(TranscriptWord(start=-0.01, end=1.005, text="Word").to_dict()["start"], "00:00.00")
        self.assertEqual(sec_to_hms(-0.01), "00:00.00")
        self.assertEqual(sec_to_hms(1.005), "00:01.01")
        self.assertEqual(parse_time_to_seconds("01:02.5"), 62.5)
        self.assertEqual(parse_time_to_seconds("01:02:03.5"), 3723.5)
        for value in (True, float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    sec_to_hms(value)

    def test_centisecond_id_helper_requires_valid_integer_ranges(self):
        for start, end in (
            (True, 200),
            (100, True),
            (100.0, 200),
            (100, 200.0),
            ("100", 200),
            (100, "200"),
            (-1, 200),
            (100, 100),
            (200, 100),
        ):
            with self.subTest(start=start, end=end):
                with self.assertRaises(SegmentIdentityError):
                    canonical_segment_id_from_centiseconds(start, end)
        self.assertEqual(
            canonical_segment_id_from_centiseconds(123, 456),
            canonical_segment_id(1.23, 4.56),
        )

    def test_transcription_result_persists_v2_schema_and_canonical_ids(self):
        result = TranscriptionResult(
            backend="test",
            model="test",
            audio_path=Path("input.wav"),
            language="en",
            duration_seconds=2.0,
            transcription_seconds=0.1,
            segments=[TranscriptSegment(start=0.005, end=1.005, text="Hello", speaker="A")],
            device="cpu",
            compute_type="int8",
        )
        payload = result.to_dict()
        self.assertEqual(payload["transcript_schema_version"], TRANSCRIPT_SCHEMA_VERSION)
        self.assertEqual(payload["segment_id_scheme"], SEGMENT_ID_SCHEME)
        self.assertEqual(payload["segment_id_version"], SEGMENT_ID_VERSION)
        self.assertEqual(payload["segments"][0]["start"], "00:00.01")
        self.assertEqual(payload["segments"][0]["end"], "00:01.01")
        self.assertEqual(payload["segments"][0]["segment_id"], canonical_segment_id(0.01, 1.01))
        normalized = normalize_transcript_segments(payload)
        self.assertEqual(normalized[0]["segment_id"], payload["segments"][0]["segment_id"])

    def test_duplicate_time_ranges_are_rejected(self):
        with self.assertRaises(ValueError):
            TranscriptionResult(
                backend="test", model="test", audio_path=Path("input.wav"), language="en",
                duration_seconds=2.0, transcription_seconds=0.1,
                segments=[
                    TranscriptSegment(start=0, end=1, text="One"),
                    TranscriptSegment(start="0.001", end="1.001", text="Two"),
                ],
                device="cpu", compute_type="int8",
            ).to_dict()
        with self.assertRaises(TranscriptSegmentValidationError):
            normalize_transcript_segments([
                {"start": 0, "end": 1, "text": "One"},
                {"start": 0.001, "end": 1.001, "text": "Two"},
            ])

    def test_legacy_ids_are_derived_without_mutating_source(self):
        legacy = {"segments": [{"segment_id": "old-ad-hoc", "start": "00:00:05,5", "end": "00:00:08", "text": "Hello"}]}
        original = copy.deepcopy(legacy)
        normalized = normalize_transcript_segments(legacy)
        self.assertEqual(legacy, original)
        self.assertEqual(normalized[0]["segment_id"], canonical_segment_id(5.5, 8.0))

    def test_v2_requires_matching_header_and_ids(self):
        segment_id = canonical_segment_id(1, 2)
        valid = {
            "transcript_schema_version": 2,
            "segment_id_scheme": SEGMENT_ID_SCHEME,
            "segment_id_version": SEGMENT_ID_VERSION,
            "segments": [{"segment_id": segment_id, "start": 1, "end": 2, "text": "Valid"}],
        }
        self.assertEqual(normalize_transcript_segments(valid)[0]["segment_id"], segment_id)
        for field, value in (("segment_id", None), ("segment_id", "seg_v1_bad"), ("segment_id_scheme", "other"), ("segment_id_version", 99)):
            invalid = copy.deepcopy(valid)
            if field == "segment_id":
                invalid["segments"][0][field] = value
            else:
                invalid[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(TranscriptSegmentValidationError):
                    normalize_transcript_segments(invalid)

    def test_review_context_uses_canonical_ids_and_rejects_duplicates(self):
        context = build_clip_transcript_context_from_segments(
            [
                {"start": 0, "end": 10, "text": "Before"},
                {"start": 10, "end": 20, "text": "Candidate"},
                {"start": 20, "end": 30, "text": "After"},
            ],
            10,
            20,
            context_seconds=10,
        )
        self.assertEqual(context["candidate_segments"][0]["segment_id"], canonical_segment_id(10, 20))
        self.assertEqual([option["option_index"] for option in context["start_boundary_options"]], [1, 2])
        with self.assertRaises(ValueError):
            segment_map({"candidate_segments": [
                {"segment_id": "same", "start": 1, "end": 2, "text": "One"},
                {"segment_id": "same", "start": 2, "end": 3, "text": "Two"},
            ]})
        with self.assertRaises(TranscriptSegmentValidationError):
            build_clip_transcript_context_from_segments(
                [{"start": 10, "end": 20, "text": "One"}, {"start": 10, "end": 20, "text": "Two"}],
                10,
                20,
            )


if __name__ == "__main__":
    unittest.main()
