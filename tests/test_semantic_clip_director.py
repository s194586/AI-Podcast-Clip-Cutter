import unittest
from unittest.mock import patch

from semantic_clip_director import (
    ClipDirectorError,
    GeminiClipDirector,
    SEMANTIC_DIRECTOR_MODE_GEMINI_OPTIONAL,
    SEMANTIC_DIRECTOR_MODE_GEMINI_REQUIRED,
)


class SemanticClipDirectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candidate = {
            "start": 10.0,
            "end": 20.0,
            "duration": 10.0,
            "summary": "krótki opis",
            "local_score": 88.0,
            "selection_reasons": ["strong heatmap support"],
            "local_features": {
                "hook_score": 0.6,
                "boundary_completeness_score": 0.4,
                "payoff_score": 0.3,
                "contextless_penalty": 0.2,
            },
        }
        self.context = [
            {"start": 5.0, "end": 10.0, "speaker": "Speaker 0", "text": "wprowadzenie"},
            {"start": 10.0, "end": 20.0, "speaker": "Speaker 0", "text": "sedno klipu"},
            {"start": 20.0, "end": 26.0, "speaker": "Speaker 0", "text": "puenta"},
        ]

    def test_optional_mode_without_api_key_falls_back_to_local_review(self):
        director = GeminiClipDirector(mode=SEMANTIC_DIRECTOR_MODE_GEMINI_OPTIONAL, api_key="")

        review = director.review_candidate(self.candidate, context_segments=self.context)

        self.assertFalse(review["semantic_director_used"])
        self.assertIn("missing", review["semantic_fallback_reason"].lower())

    @patch("semantic_clip_director.generate_text_with_transport", return_value="not-json")
    def test_invalid_gemini_json_falls_back_in_optional_mode(self, _mock_generate):
        director = GeminiClipDirector(
            mode=SEMANTIC_DIRECTOR_MODE_GEMINI_OPTIONAL,
            api_key="test-key",
        )

        review = director.review_candidate(self.candidate, context_segments=self.context)

        self.assertFalse(review["semantic_director_used"])
        self.assertIn("expecting value", review["semantic_fallback_reason"].lower())

    @patch("semantic_clip_director.generate_text_with_transport", return_value="not-json")
    def test_invalid_gemini_json_raises_in_required_mode(self, _mock_generate):
        director = GeminiClipDirector(
            mode=SEMANTIC_DIRECTOR_MODE_GEMINI_REQUIRED,
            api_key="test-key",
        )

        with self.assertRaises(ClipDirectorError):
            director.review_candidate(self.candidate, context_segments=self.context)

    def test_low_context_moves_start_earlier_when_possible(self):
        director = GeminiClipDirector(mode=SEMANTIC_DIRECTOR_MODE_GEMINI_OPTIONAL, api_key="")
        review = {
            "keep": True,
            "context_score": 0.2,
            "payoff_score": 0.8,
            "too_context_dependent": True,
            "suggested_start": None,
            "suggested_end": None,
        }

        refined = director.refine_boundaries(
            self.candidate,
            review,
            self.context,
            min_duration=8.0,
            max_duration=20.0,
        )

        self.assertEqual(refined["start"], 5.0)
        self.assertTrue(refined["semantic_boundary_adjusted"])

    def test_low_payoff_moves_end_later_when_possible(self):
        director = GeminiClipDirector(mode=SEMANTIC_DIRECTOR_MODE_GEMINI_OPTIONAL, api_key="")
        review = {
            "keep": True,
            "context_score": 0.8,
            "payoff_score": 0.2,
            "too_context_dependent": False,
            "suggested_start": None,
            "suggested_end": None,
        }

        refined = director.refine_boundaries(
            self.candidate,
            review,
            self.context,
            min_duration=8.0,
            max_duration=20.0,
        )

        self.assertEqual(refined["end"], 26.0)
        self.assertTrue(refined["semantic_boundary_adjusted"])

    def test_out_of_range_suggested_bounds_are_clamped(self):
        director = GeminiClipDirector(mode=SEMANTIC_DIRECTOR_MODE_GEMINI_OPTIONAL, api_key="")
        review = {
            "keep": True,
            "context_score": 0.7,
            "payoff_score": 0.7,
            "too_context_dependent": False,
            "suggested_start": -10.0,
            "suggested_end": 100.0,
        }

        refined = director.refine_boundaries(
            self.candidate,
            review,
            self.context,
            min_duration=8.0,
            max_duration=18.0,
        )

        self.assertGreaterEqual(refined["start"], 5.0)
        self.assertLessEqual(refined["end"], 23.0)
        self.assertTrue(refined["semantic_boundary_adjusted"])


if __name__ == "__main__":
    unittest.main()
