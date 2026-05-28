import unittest

from content_classifier import (
    VALID_CONTENT_TYPE_MODES,
    VALID_CONTENT_TYPES,
    classify_from_features,
    normalize_content_type_mode,
)


class ContentClassifierTests(unittest.TestCase):
    def test_podcast_is_the_only_supported_content_type(self):
        self.assertEqual(VALID_CONTENT_TYPES, ("podcast",))
        self.assertEqual(VALID_CONTENT_TYPE_MODES, ("auto", "podcast"))

    def test_auto_routes_to_podcast(self):
        result = classify_from_features(
            {
                "speech_coverage_ratio": 0.88,
                "speaker_count": 2,
                "question_ratio": 0.08,
                "podcast_keyword_ratio": 0.01,
            }
        )

        self.assertEqual(result.content_type, "podcast")
        self.assertEqual(result.strategy_name, "podcast")
        self.assertEqual(result.source, "podcast_only_mvp")
        self.assertEqual(result.scores, {"podcast": 1.0})

    def test_forced_podcast_routes_to_podcast(self):
        result = classify_from_features({}, forced_content_type="podcast")

        self.assertEqual(result.content_type, "podcast")
        self.assertEqual(result.strategy_name, "podcast")
        self.assertEqual(result.forced_content_type, "podcast")
        self.assertEqual(result.source, "manual_override")

    def test_legacy_content_types_are_rejected(self):
        for content_type in ("gameplay", "tutorial", "commentary", "generic"):
            with self.subTest(content_type=content_type):
                with self.assertRaises(ValueError):
                    normalize_content_type_mode(content_type)


if __name__ == "__main__":
    unittest.main()
