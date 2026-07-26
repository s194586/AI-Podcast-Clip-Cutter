from __future__ import annotations

import copy
import math
import unittest

from candidate_windows import CandidateWindowConfig, generate_candidate_windows


def peak(
    rank: int,
    peak_time: float,
    *,
    start_time: float | None = None,
    end_time: float | None = None,
    raw_value: float = 0.8,
    smoothed_value: float = 0.7,
    prominence: float = 0.2,
) -> dict:
    return {
        "rank": rank,
        "peak_time": peak_time,
        "start_time": peak_time - 1.0 if start_time is None else start_time,
        "end_time": peak_time + 1.0 if end_time is None else end_time,
        "raw_value": raw_value,
        "smoothed_value": smoothed_value,
        "prominence": prominence,
    }


def document(duration: float = 180.0, peaks: list[dict] | None = None) -> dict:
    return {
        "schema_version": 1,
        "source": "youtube_most_replayed",
        "algorithm": "time_weighted_local_prominence",
        "algorithm_version": 1,
        "video_id": "video-123",
        "duration_seconds": duration,
        "peaks": [] if peaks is None else peaks,
    }


class CandidateWindowTests(unittest.TestCase):
    def generate(self, duration: float = 180.0, peaks: list[dict] | None = None, **config: object) -> dict:
        return generate_candidate_windows(document(duration, peaks), CandidateWindowConfig(**config))

    def test_peak_in_middle_gets_symmetric_target_window(self) -> None:
        candidate = self.generate(peaks=[peak(1, 90.0)])["candidates"][0]
        self.assertEqual((candidate["start_time"], candidate["end_time"]), (60.0, 120.0))

    def test_peaks_near_edges_shift_full_window(self) -> None:
        start = self.generate(duration=120.0, peaks=[peak(1, 10.0)])["candidates"][0]
        end = self.generate(duration=120.0, peaks=[peak(1, 110.0)])["candidates"][0]
        self.assertEqual((start["start_time"], start["end_time"]), (0.0, 60.0))
        self.assertEqual((end["start_time"], end["end_time"]), (60.0, 120.0))

    def test_short_videos_use_entire_recording_or_no_candidates(self) -> None:
        whole = self.generate(duration=45.0, peaks=[peak(1, 22.5)])["candidates"]
        empty = self.generate(duration=29.9, peaks=[peak(1, 10.0)])["candidates"]
        self.assertEqual((whole[0]["start_time"], whole[0]["end_time"]), (0.0, 45.0))
        self.assertEqual(empty, [])

    def test_empty_peaks_produces_empty_candidates(self) -> None:
        self.assertEqual(self.generate()["candidates"], [])

    def test_identical_windows_are_deduplicated(self) -> None:
        candidates = self.generate(peaks=[peak(1, 10.0), peak(2, 20.0)])["candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source_peak_rank"], 1)

    def test_overlap_suppression_and_equal_threshold(self) -> None:
        suppressed = self.generate(peaks=[peak(1, 60.0), peak(2, 80.0)])["candidates"]
        equal = self.generate(
            peaks=[peak(1, 60.0), peak(2, 81.0)], max_overlap_ratio=0.65
        )["candidates"]
        self.assertEqual([item["source_peak_rank"] for item in suppressed], [1])
        self.assertEqual([item["source_peak_rank"] for item in equal], [1, 2])

    def test_max_candidates_and_reranking_after_suppression(self) -> None:
        candidates = self.generate(
            duration=300.0,
            peaks=[peak(3, 30.0), peak(1, 100.0), peak(2, 120.0), peak(4, 220.0)],
            max_candidates=2,
        )["candidates"]
        self.assertEqual([item["rank"] for item in candidates], [1, 2])
        self.assertEqual([item["source_peak_rank"] for item in candidates], [1, 3])

    def test_source_peak_data_is_preserved_without_a_combined_score(self) -> None:
        candidate = self.generate(peaks=[peak(4, 90.0, raw_value=0.88, smoothed_value=0.81, prominence=0.22)])["candidates"][0]
        self.assertEqual(candidate["source_peak_rank"], 4)
        self.assertEqual(candidate["replay_interest"], {"raw_value": 0.88, "smoothed_value": 0.81, "prominence": 0.22})
        forbidden = {"viral_score", "virality_score", "semantic_score", "hook_score", "emotion_score", "payoff_score", "controversy_score", "quality_score", "confidence", "predicted_engagement", "content_type", "reason", "title", "summary"}
        self.assertFalse(forbidden.intersection(candidate))

    def test_input_order_immutability_and_repeated_calls_are_deterministic(self) -> None:
        peaks = [peak(2, 150.0), peak(1, 30.0), peak(3, 250.0)]
        payload = document(300.0, peaks)
        original = copy.deepcopy(payload)
        first = generate_candidate_windows(payload)
        second = generate_candidate_windows({**payload, "peaks": list(reversed(peaks))})
        self.assertEqual(payload, original)
        self.assertEqual(first, second)
        self.assertEqual(first, generate_candidate_windows(payload))

    def test_invalid_configuration_is_rejected(self) -> None:
        invalid = (
            {"min_duration_seconds": math.nan}, {"target_duration_seconds": math.inf},
            {"max_overlap_ratio": True}, {"max_candidates": True},
            {"min_duration_seconds": 29.9}, {"target_duration_seconds": 29.0},
            {"max_duration_seconds": 59.0}, {"max_duration_seconds": 90.1},
            {"max_overlap_ratio": -0.1}, {"max_overlap_ratio": 1.1}, {"max_candidates": 0},
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    CandidateWindowConfig(**kwargs)

    def test_malformed_peak_documents_are_rejected(self) -> None:
        invalid_documents = (
            {**document(), "schema_version": 2},
            {**document(), "schema_version": True},
            {**document(), "source": "other"},
            {**document(), "video_id": ""},
            {**document(), "duration_seconds": math.nan},
            {**document(), "peaks": "not-a-list"},
            document(peaks=[peak(1, 90.0, start_time=91.0, end_time=90.0)]),
            document(peaks=[peak(1, 100.0, start_time=10.0, end_time=20.0)]),
            document(peaks=[peak(1, 90.0, raw_value=1.1)]),
            document(peaks=[peak(1, 90.0), peak(1, 120.0)]),
        )
        for payload in invalid_documents:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    generate_candidate_windows(payload)


if __name__ == "__main__":
    unittest.main()
