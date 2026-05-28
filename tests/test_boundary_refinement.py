import unittest

from analyze_virals import build_local_selection, refine_story_bounds_for_strategy


class BoundaryRefinementTests(unittest.TestCase):
    def _context(self):
        return [
            {"start": 0.0, "end": 10.0, "text": "Najpierw ustawiamy kontekst.", "speaker": "Speaker 0"},
            {"start": 10.0, "end": 20.0, "text": "Potem pada najwazniejsza teza.", "speaker": "Speaker 0"},
            {"start": 20.0, "end": 30.0, "text": "Dlatego ten fragment ma sens.", "speaker": "Speaker 0"},
        ]

    def test_podcast_clip_aligns_to_sentence_boundaries(self):
        window = {"start": 5.0, "end": 24.0, "duration": 19.0, "summary": "", "text": ""}
        start, end, decisions, metadata = refine_story_bounds_for_strategy(
            5.0,
            24.0,
            self._context(),
            window,
            max_duration=40.0,
            min_duration=10.0,
            strategy_name="podcast",
        )

        self.assertEqual(start, 0.0)
        self.assertEqual(end, 30.0)
        self.assertFalse(metadata["max_duration_clamped"])
        self.assertTrue(any("sentence start" in decision for decision in decisions))

    def test_local_selection_records_podcast_boundary_metadata(self):
        sentences = self._context()
        window = {
            "candidate_id": "cand_001",
            "start": 5.0,
            "end": 24.0,
            "duration": 19.0,
            "avg_value": 0.7,
            "summary": "",
            "text": "",
            "selection_reasons": ["test"],
            "local_score": 88.0,
            "local_rank": 1,
        }
        refined, _decision = build_local_selection(
            window,
            sentences,
            index=1,
            max_duration=40.0,
            context_margin=0.0,
            reason="unit test",
            min_duration=10.0,
            strategy_name="podcast",
        )

        metadata = refined["boundary_metadata"]
        self.assertEqual(metadata["original_start"], 5.0)
        self.assertEqual(metadata["original_end"], 24.0)
        self.assertEqual(metadata["refined_start"], 0.0)
        self.assertEqual(metadata["refined_end"], 30.0)
        self.assertTrue(metadata["sentence_boundary_used"])
        self.assertTrue(metadata["speaker_turn_boundary_used"])
        self.assertFalse(metadata["max_duration_clamped"])
        self.assertTrue(metadata["boundary_refined"])

    def test_podcast_padding_respects_max_duration(self):
        context = [
            {"start": 10.0, "end": 13.0, "text": "krotkie wprowadzenie", "speaker": "Speaker 0"},
            {"start": 13.0, "end": 18.0, "text": "glowna odpowiedz i sedno wypowiedzi", "speaker": "Speaker 0"},
            {"start": 18.0, "end": 21.0, "text": "domkniecie mysli", "speaker": "Speaker 0"},
        ]
        window = {"start": 13.2, "end": 18.1, "duration": 4.9, "summary": "", "text": ""}
        start, end, _decisions, metadata = refine_story_bounds_for_strategy(
            13.2,
            18.1,
            context,
            window,
            max_duration=8.0,
            min_duration=4.0,
            strategy_name="podcast",
        )

        self.assertLessEqual(end - start, 8.0)
        self.assertGreaterEqual(metadata["preroll_added"], 0.0)
        self.assertGreaterEqual(metadata["postroll_added"], 0.0)
        self.assertIn(metadata["context_padding_reason"], {"", "podcast_context_padding"})

    def test_boundary_fallback_marks_missing_context(self):
        window = {"start": 12.0, "end": 22.0, "duration": 10.0, "summary": "", "text": ""}
        start, end, _decisions, metadata = refine_story_bounds_for_strategy(
            12.0,
            22.0,
            [],
            window,
            max_duration=30.0,
            min_duration=8.0,
            strategy_name="podcast",
        )

        self.assertEqual(start, 12.0)
        self.assertEqual(end, 22.0)
        self.assertFalse(metadata["segment_boundary_aligned"])
        self.assertFalse(metadata["sentence_boundary_aligned"])
        self.assertEqual(metadata["fallback_alignment_reason"], "no_transcript_context")

    def test_podcast_expands_new_thread_ending_toward_closure(self):
        context = [
            {"start": 0.0, "end": 8.0, "text": "Najpierw jest pytanie i kontekst.", "speaker": "Speaker 0"},
            {"start": 8.0, "end": 16.0, "text": "Tutaj czesc osob podnosi argument, ze to nie ma sensu.", "speaker": "Speaker 0"},
            {"start": 16.0, "end": 24.0, "text": "Ale finalnie sedno jest proste i to jest pointa.", "speaker": "Speaker 0"},
        ]
        window = {"start": 1.0, "end": 15.0, "duration": 14.0, "summary": "", "text": ""}
        start, end, _decisions, metadata = refine_story_bounds_for_strategy(
            1.0,
            15.0,
            context,
            window,
            max_duration=40.0,
            min_duration=10.0,
            strategy_name="podcast",
        )

        self.assertEqual(start, 0.0)
        self.assertEqual(end, 24.0)
        self.assertGreater(end, 15.0)
        self.assertTrue(metadata["payoff_detected"])
        self.assertEqual(metadata["ending_type"], "closed")


if __name__ == "__main__":
    unittest.main()
