import unittest

from local_scoring import score_candidate


class LocalScoringQualityTests(unittest.TestCase):
    def _heatmap(self):
        return [{"start_time": 0.0, "end_time": 60.0, "value": 0.9}]

    def test_legacy_strategy_name_is_normalized_to_podcast(self):
        text = "dlaczego to bylo wazne? bo wtedy pierwszy raz uslyszalem odpowiedz."
        scored = score_candidate(
            {
                "start": 0.0,
                "end": 35.0,
                "duration": 35.0,
                "avg_value": 0.9,
                "text": text,
            },
            [
                {"start": 0.0, "end": 15.0, "text": "dlaczego to bylo wazne?", "speaker": "Speaker 0"},
                {"start": 15.0, "end": 35.0, "text": "bo wtedy pierwszy raz uslyszalem odpowiedz.", "speaker": "Speaker 1"},
            ],
            self._heatmap(),
            strategy_name="gameplay",
        )

        self.assertEqual(scored["selection_strategy"], "podcast")
        self.assertEqual(scored["local_features"]["setup_penalty"], 0.0)

    def test_podcast_clip_with_question_and_answer_scores_above_contextless_fragment(self):
        good_text = "dlaczego to bylo wazne? bo wtedy zrozumialem, ze trzeba zmienic decyzje."
        weak_text = "i wtedy oni to zrobili bez zadnego wyjasnienia dalej"
        good = score_candidate(
            {"start": 0.0, "end": 35.0, "duration": 35.0, "avg_value": 0.8, "text": good_text},
            [
                {"start": 0.0, "end": 10.0, "text": "dlaczego to bylo wazne?", "speaker": "Speaker 0"},
                {
                    "start": 10.0,
                    "end": 35.0,
                    "text": "bo wtedy zrozumialem, ze trzeba zmienic decyzje.",
                    "speaker": "Speaker 1",
                },
            ],
            self._heatmap(),
        )
        weak = score_candidate(
            {"start": 0.0, "end": 35.0, "duration": 35.0, "avg_value": 0.8, "text": weak_text},
            [{"start": 0.0, "end": 35.0, "text": weak_text, "speaker": "Speaker 0"}],
            self._heatmap(),
        )

        self.assertGreater(good["local_features"]["podcast_dialogue_payoff_score"], weak["local_features"]["podcast_dialogue_payoff_score"])
        self.assertGreater(weak["local_features"]["contextless_penalty"], 0.0)
        self.assertGreater(good["local_score"], weak["local_score"])

    def test_sponsor_like_podcast_clip_gets_penalty(self):
        text = "reklama sponsor kod promo link w opisie"
        scored = score_candidate(
            {"start": 0.0, "end": 35.0, "duration": 35.0, "avg_value": 0.9, "text": text},
            [{"start": 0.0, "end": 35.0, "text": text, "speaker": "Speaker 0"}],
            self._heatmap(),
        )

        self.assertGreater(scored["local_features"]["ad_like_penalty"], 0.5)
        self.assertIn("penalized for ad/sponsor-like wording", scored["selection_reasons"])


if __name__ == "__main__":
    unittest.main()
