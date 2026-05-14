import unittest

from analyze_virals import build_local_selection, refine_story_bounds_for_strategy


class BoundaryRefinementTests(unittest.TestCase):
    def _context(self):
        return [
            {"start": 0.0, "end": 10.0, "text": "Najpierw ustawiamy kontekst.", "speaker": "Speaker 0"},
            {"start": 10.0, "end": 20.0, "text": "Potem pada najważniejsza teza.", "speaker": "Speaker 0"},
            {"start": 20.0, "end": 30.0, "text": "Dlatego ten fragment ma sens.", "speaker": "Speaker 0"},
        ]

    def test_talk_clip_aligns_to_sentence_boundaries(self):
        window = {"start": 5.0, "end": 24.0, "duration": 19.0, "summary": "", "text": ""}
        start, end, decisions, metadata = refine_story_bounds_for_strategy(
            5.0,
            24.0,
            self._context(),
            window,
            max_duration=40.0,
            min_duration=10.0,
            strategy_name="commentary",
        )

        self.assertEqual(start, 0.0)
        self.assertEqual(end, 30.0)
        self.assertFalse(metadata["max_duration_clamped"])
        self.assertTrue(any("sentence start" in decision for decision in decisions))

    def test_local_selection_records_boundary_metadata(self):
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
            strategy_name="commentary",
        )

        metadata = refined["boundary_metadata"]
        self.assertEqual(metadata["original_start"], 5.0)
        self.assertEqual(metadata["original_end"], 24.0)
        self.assertEqual(metadata["refined_start"], 0.0)
        self.assertEqual(metadata["refined_end"], 30.0)
        self.assertTrue(metadata["sentence_boundary_used"])
        self.assertTrue(metadata["speaker_turn_boundary_used"])
        self.assertFalse(metadata["max_duration_clamped"])

    def test_gameplay_trim_keeps_short_preroll_before_payoff(self):
        context = [
            {"start": 0.0, "end": 10.0, "text": "buy menu i smoke, czekamy", "speaker": "Speaker 0"},
            {"start": 10.0, "end": 20.0, "text": "utility i chodzenie bez kontaktu", "speaker": "Speaker 0"},
            {"start": 20.0, "end": 35.0, "text": "push, hit, headshot, kill nice!", "speaker": "Speaker 1"},
        ]
        window = {"start": 0.0, "end": 35.0, "duration": 35.0, "summary": "", "text": ""}
        start, end, decisions, metadata = refine_story_bounds_for_strategy(
            0.0,
            35.0,
            context,
            window,
            max_duration=45.0,
            min_duration=15.0,
            strategy_name="gameplay",
        )

        self.assertGreater(start, 0.0)
        self.assertLessEqual(start, 20.0)
        self.assertEqual(end, 35.0)
        self.assertFalse(metadata["max_duration_clamped"])
        self.assertTrue(any("pre-roll" in decision for decision in decisions))


if __name__ == "__main__":
    unittest.main()
