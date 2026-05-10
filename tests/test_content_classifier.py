import unittest

from content_classifier import classify_from_features


class ContentClassifierTests(unittest.TestCase):
    def test_podcast_like_features_route_to_podcast(self):
        result = classify_from_features(
            {
                "speech_coverage_ratio": 0.88,
                "avg_segment_duration": 2.8,
                "long_segment_ratio": 0.36,
                "speaker_count": 2,
                "speaker_switch_rate_per_minute": 4.2,
                "dominant_speaker_ratio": 0.58,
                "motion_score": 0.08,
                "scene_change_rate": 0.03,
                "face_presence_ratio": 0.82,
                "face_stability": 0.78,
                "gameplay_keyword_ratio": 0.0,
                "tutorial_keyword_ratio": 0.0,
                "podcast_keyword_ratio": 0.01,
                "emotion_segment_ratio": 0.11,
                "short_segment_ratio": 0.08,
                "heatmap_volatility": 0.04,
                "heatmap_high_energy_ratio": 0.05,
            }
        )
        self.assertEqual(result.content_type, "podcast")

    def test_gameplay_like_features_route_to_gameplay(self):
        result = classify_from_features(
            {
                "speech_coverage_ratio": 0.73,
                "avg_segment_duration": 1.1,
                "speaker_count": 4,
                "speaker_switch_rate_per_minute": 10.5,
                "dominant_speaker_ratio": 0.34,
                "motion_score": 0.74,
                "scene_change_rate": 0.31,
                "face_presence_ratio": 0.28,
                "face_stability": 0.24,
                "face_overlay_ratio": 0.55,
                "gameplay_keyword_ratio": 0.026,
                "tutorial_keyword_ratio": 0.001,
                "emotion_segment_ratio": 0.39,
                "short_segment_ratio": 0.48,
                "chaos_ratio": 0.22,
                "heatmap_volatility": 0.16,
                "heatmap_high_energy_ratio": 0.22,
            }
        )
        self.assertEqual(result.content_type, "gameplay")

    def test_tutorial_like_features_route_to_tutorial(self):
        result = classify_from_features(
            {
                "speech_coverage_ratio": 0.91,
                "avg_segment_duration": 2.4,
                "speaker_count": 1,
                "speaker_switch_rate_per_minute": 0.0,
                "dominant_speaker_ratio": 1.0,
                "motion_score": 0.14,
                "scene_change_rate": 0.05,
                "face_presence_ratio": 0.12,
                "face_large_ratio": 0.08,
                "gameplay_keyword_ratio": 0.001,
                "tutorial_keyword_ratio": 0.024,
                "emotion_segment_ratio": 0.07,
                "short_segment_ratio": 0.10,
                "avg_words_per_second": 2.5,
                "heatmap_volatility": 0.05,
                "heatmap_high_energy_ratio": 0.04,
            }
        )
        self.assertEqual(result.content_type, "tutorial")

    def test_ambiguous_features_fall_back_to_generic(self):
        result = classify_from_features(
            {
                "speech_coverage_ratio": 0.48,
                "avg_segment_duration": 1.5,
                "speaker_count": 1,
                "speaker_switch_rate_per_minute": 0.6,
                "dominant_speaker_ratio": 0.96,
                "motion_score": 0.24,
                "scene_change_rate": 0.08,
                "face_presence_ratio": 0.18,
                "gameplay_keyword_ratio": 0.002,
                "tutorial_keyword_ratio": 0.002,
                "podcast_keyword_ratio": 0.0,
                "emotion_segment_ratio": 0.14,
                "short_segment_ratio": 0.2,
                "heatmap_volatility": 0.06,
                "heatmap_high_energy_ratio": 0.07,
            }
        )
        self.assertEqual(result.content_type, "generic")


if __name__ == "__main__":
    unittest.main()
