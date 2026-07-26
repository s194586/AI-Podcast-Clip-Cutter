import unittest

from apps.pipeline.content_mode import VALID_CONTENT_TYPE_MODES, normalize_content_type_mode


class ContentModeTests(unittest.TestCase):
    def test_accepts_podcast_and_legacy_auto_mode(self):
        self.assertEqual(VALID_CONTENT_TYPE_MODES, ("auto", "podcast"))
        self.assertEqual(normalize_content_type_mode("podcast"), "podcast")
        self.assertEqual(normalize_content_type_mode("auto"), "auto")

    def test_normalizes_case_and_whitespace(self):
        self.assertEqual(normalize_content_type_mode("  PODCAST  "), "podcast")

    def test_none_and_false_use_default_while_true_is_rejected(self):
        self.assertEqual(normalize_content_type_mode(None), "auto")
        self.assertEqual(normalize_content_type_mode(False), "auto")
        with self.assertRaises(ValueError):
            normalize_content_type_mode(True)

    def test_invalid_value_preserves_controlled_error(self):
        with self.assertRaisesRegex(ValueError, "Unsupported content type for the podcast-only product: gameplay"):
            normalize_content_type_mode("gameplay")


if __name__ == "__main__":
    unittest.main()
