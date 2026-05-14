import unittest

from local_scoring import score_candidate


class LocalScoringQualityTests(unittest.TestCase):
    def _heatmap(self):
        return [{"start_time": 0.0, "end_time": 60.0, "value": 0.9}]

    def test_gameplay_setup_without_payoff_is_penalized(self):
        transcript = [
            {
                "start": 0.0,
                "end": 35.0,
                "text": "buy menu smoke czekamy utility idziemy dalej no tak",
                "speaker": "Speaker 0",
                "importance": 3,
            }
        ]
        weak = score_candidate(
            {
                "start": 0.0,
                "end": 35.0,
                "duration": 35.0,
                "avg_value": 0.9,
                "text": transcript[0]["text"],
            },
            transcript,
            self._heatmap(),
            strategy_name="gameplay",
        )
        strong = score_candidate(
            {
                "start": 0.0,
                "end": 35.0,
                "duration": 35.0,
                "avg_value": 0.9,
                "text": "patrz push hit headshot kill nice clutch!",
            },
            [
                {
                    "start": 0.0,
                    "end": 35.0,
                    "text": "patrz push hit headshot kill nice clutch!",
                    "speaker": "Speaker 0",
                    "importance": 4,
                }
            ],
            self._heatmap(),
            strategy_name="gameplay",
        )

        self.assertGreater(weak["local_features"]["gameplay_setup_penalty"], 0.2)
        self.assertGreater(weak["local_features"]["low_payoff_penalty"], 0.0)
        self.assertGreater(strong["local_features"]["gameplay_action_score"], weak["local_features"]["gameplay_action_score"])
        self.assertGreater(strong["local_score"], weak["local_score"])

    def test_ad_like_gameplay_clip_gets_sponsor_penalty(self):
        text = "reklama skiny kod promo link w opisie case changer"
        scored = score_candidate(
            {
                "start": 0.0,
                "end": 35.0,
                "duration": 35.0,
                "avg_value": 0.9,
                "text": text,
            },
            [
                {
                    "start": 0.0,
                    "end": 35.0,
                    "text": text,
                    "speaker": "Speaker 0",
                    "importance": 3,
                }
            ],
            self._heatmap(),
            strategy_name="gameplay",
        )

        self.assertGreater(scored["local_features"]["ad_like_penalty"], 0.5)
        self.assertIn("penalized for ad/sponsor-like wording", scored["selection_reasons"])

    def test_tutorial_instruction_signal_beats_transition_only_clip(self):
        instructional = score_candidate(
            {
                "start": 0.0,
                "end": 35.0,
                "duration": 35.0,
                "avg_value": 0.6,
                "text": "teraz pokażę, kliknij dodaj, wybierz szablon i ustaw kolor.",
            },
            [
                {
                    "start": 0.0,
                    "end": 35.0,
                    "text": "teraz pokażę, kliknij dodaj, wybierz szablon i ustaw kolor.",
                    "speaker": "Speaker 0",
                    "importance": 3,
                }
            ],
            self._heatmap(),
            strategy_name="tutorial",
        )
        transition = score_candidate(
            {
                "start": 0.0,
                "end": 35.0,
                "duration": 35.0,
                "avg_value": 0.6,
                "text": "teraz przejdziemy dalej i za chwilę będzie kolejna część.",
            },
            [
                {
                    "start": 0.0,
                    "end": 35.0,
                    "text": "teraz przejdziemy dalej i za chwilę będzie kolejna część.",
                    "speaker": "Speaker 0",
                    "importance": 3,
                }
            ],
            self._heatmap(),
            strategy_name="tutorial",
        )

        self.assertGreater(instructional["local_features"]["tutorial_instruction_score"], 0.5)
        self.assertGreater(transition["local_features"]["low_payoff_penalty"], 0.0)
        self.assertGreater(instructional["local_score"], transition["local_score"])

    def test_commentary_contextless_fragment_is_penalized(self):
        scored = score_candidate(
            {
                "start": 0.0,
                "end": 35.0,
                "duration": 35.0,
                "avg_value": 0.7,
                "text": "i wtedy oni to zrobili bez żadnego wyjaśnienia dalej",
            },
            [
                {
                    "start": 0.0,
                    "end": 35.0,
                    "text": "i wtedy oni to zrobili bez żadnego wyjaśnienia dalej",
                    "speaker": "Speaker 0",
                    "importance": 3,
                }
            ],
            self._heatmap(),
            strategy_name="commentary",
        )

        self.assertGreater(scored["local_features"]["contextless_penalty"], 0.0)
        self.assertGreater(scored["local_features"]["low_payoff_penalty"], 0.0)


if __name__ == "__main__":
    unittest.main()
