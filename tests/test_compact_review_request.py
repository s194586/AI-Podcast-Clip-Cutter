from __future__ import annotations

import json
import unittest
from copy import deepcopy

from apps.review_agent.context import build_clip_transcript_context_from_segments
from apps.review_agent.providers import (
    COMPACT_REVIEW_REQUEST_CONTRACT_VERSION,
    build_compact_review_request,
    build_gemini_prompt,
)
from apps.review_agent.schemas import GeminiBoundaryDecision


class CompactReviewRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = build_clip_transcript_context_from_segments(
            [
                {"start": 80.0, "end": 100.0, "text": "Before segment.", "speaker": "Host"},
                {"start": 100.0, "end": 120.0, "text": "Candidate opening.", "speaker": ""},
                {"start": 120.0, "end": 140.0, "text": "Candidate payoff.", "speaker": "Guest"},
                {"start": 140.0, "end": 160.0, "text": "After segment."},
            ],
            100.0,
            140.0,
            context_seconds=20.0,
            clip_id="clip_001",
            candidate_id="cand_v1_example",
            min_duration_seconds=15.0,
            max_duration_seconds=80.0,
        )

    def test_projection_is_chronological_one_copy_and_deterministic(self) -> None:
        request = build_compact_review_request(self.context)

        self.assertEqual(request["review_request_contract_version"], COMPACT_REVIEW_REQUEST_CONTRACT_VERSION)
        self.assertEqual(
            request["candidate"],
            {
                "clip_id": "clip_001",
                "candidate_id": "cand_v1_example",
                "current_start": 100.0,
                "current_end": 140.0,
                "minimum_duration_seconds": 15.0,
                "maximum_duration_seconds": 80.0,
                "current_aligned_start_option_index": 2,
                "current_aligned_end_option_index": 2,
                "current_aligned_start_segment_id": self.context["current_aligned_start_segment_id"],
                "current_aligned_end_segment_id": self.context["current_aligned_end_segment_id"],
            },
        )
        segments = request["segments"]
        self.assertEqual([segment["relation"] for segment in segments], ["before", "candidate", "candidate", "after"])
        self.assertEqual([segment["start"] for segment in segments], [80.0, 100.0, 120.0, 140.0])
        self.assertEqual(len({segment["segment_id"] for segment in segments}), len(segments))
        serialized = json.dumps(request, ensure_ascii=False, sort_keys=True)
        for segment in segments:
            expected_id_count = 1 + int(
                segment["segment_id"]
                in {
                    request["candidate"]["current_aligned_start_segment_id"],
                    request["candidate"]["current_aligned_end_segment_id"],
                }
            )
            self.assertEqual(serialized.count(segment["segment_id"]), expected_id_count)
            self.assertEqual(serialized.count(segment["text"]), 1)
        self.assertEqual(build_compact_review_request(self.context), request)

    def test_candidate_id_is_distinct_from_clip_id_and_optional(self) -> None:
        request = build_compact_review_request(self.context)

        self.assertEqual(request["candidate"]["clip_id"], "clip_001")
        self.assertEqual(request["candidate"]["candidate_id"], "cand_v1_example")
        legacy_context = deepcopy(self.context)
        legacy_context["candidate_id"] = None
        self.assertIsNone(build_compact_review_request(legacy_context)["candidate"]["candidate_id"])

    def test_projection_preserves_speakers_and_exact_option_indexes(self) -> None:
        request = build_compact_review_request(self.context)
        segments = request["segments"]
        before, candidate_opening, candidate_payoff, after = segments

        self.assertEqual(before["speaker"], "Host")
        self.assertNotIn("speaker", candidate_opening)
        self.assertEqual(candidate_payoff["speaker"], "Guest")
        self.assertNotIn("speaker", after)
        self.assertEqual(before["start_option_index"], 1)
        self.assertIsNone(before["end_option_index"])
        self.assertEqual(candidate_opening["start_option_index"], 2)
        self.assertEqual(candidate_opening["end_option_index"], 1)
        self.assertEqual(candidate_payoff["start_option_index"], 3)
        self.assertEqual(candidate_payoff["end_option_index"], 2)
        self.assertIsNone(after["start_option_index"])
        self.assertEqual(after["end_option_index"], 3)

    def test_prompt_embeds_only_compact_request_and_requires_segment_ids(self) -> None:
        request = build_compact_review_request(self.context)
        prompt = build_gemini_prompt(request)

        for field in (
            "context_before",
            "candidate_segments",
            "context_after",
            "start_boundary_options",
            "end_boundary_options",
            "allowed_boundary_pairs",
        ):
            self.assertNotIn(f'"{field}"', json.dumps(request))
            self.assertNotIn(f'"{field}"', prompt)
        for segment in request["segments"]:
            self.assertEqual(prompt.count(segment["text"]), 1)
        decision = GeminiBoundaryDecision(
            review_response_contract_version=2,
            decision="render_ready",
            start_segment_id=request["candidate"]["current_aligned_start_segment_id"],
            end_segment_id=request["candidate"]["current_aligned_end_segment_id"],
            reasoning_summary="Complete thought.",
            start_reason="The setup begins here.",
            end_reason="The payoff ends here.",
        )
        self.assertEqual(decision.start_segment_id, request["candidate"]["current_aligned_start_segment_id"])
        self.assertEqual(decision.end_segment_id, request["candidate"]["current_aligned_end_segment_id"])
        self.assertIn("start_segment_id", prompt)
        self.assertIn("end_segment_id", prompt)
        self.assertIn("Do not return option indexes", prompt)
        self.assertIn("Do not invent segment IDs or timestamps", prompt)

    def test_duplicate_segment_ids_fail_explicitly(self) -> None:
        duplicate = dict(self.context)
        duplicate["context_after"] = [dict(self.context["candidate_segments"][0])]

        with self.assertRaisesRegex(ValueError, "Duplicate segment_id"):
            build_compact_review_request(duplicate)

    def test_compact_contract_rejects_empty_segment_id(self) -> None:
        invalid = deepcopy(self.context)
        invalid["candidate_segments"][0]["segment_id"] = ""

        with self.assertRaisesRegex(ValueError, "segment_id must be a non-empty string"):
            build_compact_review_request(invalid)

    def test_compact_contract_rejects_non_positive_or_non_integer_option_indexes(self) -> None:
        for invalid_index in (0, True, "1", 1.0):
            with self.subTest(option_index=invalid_index):
                invalid = deepcopy(self.context)
                invalid["start_boundary_options"][0]["option_index"] = invalid_index
                with self.assertRaisesRegex(ValueError, "option_index must be a positive integer"):
                    build_compact_review_request(invalid)

    def test_compact_contract_rejects_duplicate_option_indexes_in_each_option_list(self) -> None:
        for option_list_name in ("start_boundary_options", "end_boundary_options"):
            with self.subTest(option_list_name=option_list_name):
                invalid = deepcopy(self.context)
                invalid[option_list_name][1]["option_index"] = invalid[option_list_name][0]["option_index"]
                with self.assertRaisesRegex(ValueError, "Duplicate option_index"):
                    build_compact_review_request(invalid)

    def test_compact_contract_rejects_option_for_unknown_segment(self) -> None:
        invalid = deepcopy(self.context)
        invalid["start_boundary_options"][0]["segment_id"] = "seg_v1_missing"

        with self.assertRaisesRegex(ValueError, "not present in compact segments"):
            build_compact_review_request(invalid)

    def test_compact_contract_rejects_unknown_current_aligned_indexes(self) -> None:
        for field_name in (
            "current_aligned_start_option_index",
            "current_aligned_end_option_index",
        ):
            with self.subTest(field_name=field_name):
                invalid = deepcopy(self.context)
                invalid[field_name] = 999
                with self.assertRaisesRegex(ValueError, "does not exist in its boundary option indexes"):
                    build_compact_review_request(invalid)


if __name__ == "__main__":
    unittest.main()
