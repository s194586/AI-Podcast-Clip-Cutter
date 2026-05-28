import unittest
from unittest.mock import patch

import subtitler


class SubtitlerSpeakerTests(unittest.TestCase):
    def test_speaker_style_is_deterministic_per_label(self):
        self.assertEqual(subtitler.speaker_style("Speaker 1"), subtitler.speaker_style("Speaker 1"))
        self.assertNotEqual(subtitler.speaker_style("Speaker 0"), subtitler.speaker_style("Speaker 1"))
        self.assertEqual(
            subtitler.collect_speaker_styles([{"speaker": "Speaker 1"}]),
            [subtitler.DEFAULT_STYLE_NAME, "Speaker 1"],
        )

    def test_unstable_short_flip_falls_back_to_single_style(self):
        transcript = [
            {"start": "00:00.00", "end": "00:02.00", "text": "first part", "speaker": "Speaker 0"},
            {"start": "00:02.00", "end": "00:02.70", "text": "short flip", "speaker": "Speaker 1"},
            {"start": "00:02.70", "end": "00:05.00", "text": "same speaker returns", "speaker": "Speaker 0"},
        ]
        events, metadata = subtitler.build_subtitle_events_with_metadata(
            transcript,
            0.0,
            5.0,
            speaker_smoothing_window=1.0,
        )

        self.assertEqual(len(events), 3)
        self.assertEqual({event["speaker"] for event in events}, {subtitler.DEFAULT_STYLE_NAME})
        self.assertEqual(events[1]["detected_speaker"], "Speaker 0")
        self.assertEqual(metadata["speaker_flips_smoothed"], 1)
        self.assertTrue(metadata["speaker_smoothing_enabled"])
        self.assertEqual(list(metadata["speaker_color_map"].keys()), [subtitler.DEFAULT_STYLE_NAME])
        self.assertEqual(metadata["speaker_color_mode"], "single_style_fallback")

    def test_podcast_mvp_forces_single_visual_style(self):
        transcript = [
            {"start": "00:00.00", "end": "00:04.00", "text": "host one", "speaker": "Speaker 0"},
            {"start": "00:04.00", "end": "00:09.00", "text": "host two", "speaker": "Speaker 1"},
            {"start": "00:09.00", "end": "00:14.00", "text": "host one again", "speaker": "Speaker 0"},
        ]
        events, metadata = subtitler.build_subtitle_events_with_metadata(
            transcript,
            0.0,
            14.0,
            content_type_hint="podcast",
            expected_speaker_mode="multi",
        )

        self.assertEqual({event["speaker"] for event in events}, {subtitler.DEFAULT_STYLE_NAME})
        self.assertEqual(metadata["detected_speaker_count"], 2)
        self.assertEqual(metadata["effective_speaker_count"], 2)
        self.assertEqual(metadata["speaker_color_mode"], "single_style_fallback")
        self.assertEqual(metadata["subtitle_color_policy"], "single_color_podcast_mvp")
        self.assertEqual(list(metadata["speaker_color_map"].keys()), [subtitler.DEFAULT_STYLE_NAME])

    def test_podcast_ass_file_stays_single_style_without_emphasis_color(self):
        ass = subtitler.create_ass_file(
            [
                {"start": 0.0, "end": 2.0, "text": "hello", "speaker": "Default", "render_style": "Default", "importance": 3},
                {"start": 2.0, "end": 4.0, "text": "big moment", "speaker": "Default", "render_style": "Default", "importance": 5},
            ]
        )

        self.assertIn("Dialogue: 0,0:00:00.00,0:00:02.00,Default", ass)
        self.assertIn("Dialogue: 0,0:00:02.00,0:00:04.00,Default", ass)
        self.assertNotIn("Dialogue: 0,0:00:02.00,0:00:04.00,ChaosEmphasis", ass)

    def test_local_subtitle_correction_preserves_timestamps_and_segment_count(self):
        transcript = [
            {"start": "00:00.00", "end": "00:02.00", "text": "to  jest test ,napisow", "speaker": "Speaker 0"},
            {"start": "00:02.00", "end": "00:04.00", "text": "druga linia", "speaker": "Speaker 0"},
        ]
        events, metadata = subtitler.build_subtitle_events_with_metadata(
            transcript,
            0.0,
            4.0,
            subtitle_correction_mode="local_only",
        )

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["start"], 0.0)
        self.assertEqual(events[0]["end"], 2.0)
        self.assertEqual(events[0]["text"], "To jest test, napisow")
        self.assertTrue(metadata["subtitles_corrected"])
        self.assertEqual(metadata["corrected_segments_count"], 2)

    def test_subtitle_correction_off_leaves_text_unchanged(self):
        transcript = [
            {"start": "00:00.00", "end": "00:02.00", "text": "to  jest test ,napisow", "speaker": "Speaker 0"},
        ]
        events, metadata = subtitler.build_subtitle_events_with_metadata(
            transcript,
            0.0,
            2.0,
            subtitle_correction_mode="off",
        )

        self.assertEqual(events[0]["text"], "to  jest test ,napisow")
        self.assertFalse(metadata["subtitles_corrected"])

    @patch("semantic_clip_director.generate_text_with_transport", return_value="not-json")
    def test_invalid_api_subtitle_correction_falls_back(self, _mock_generate):
        transcript = [
            {"start": "00:00.00", "end": "00:02.00", "text": "to  jest test ,napisow", "speaker": "Speaker 0"},
        ]
        events, metadata = subtitler.build_subtitle_events_with_metadata(
            transcript,
            0.0,
            2.0,
            subtitle_correction_mode="gemini_optional",
            semantic_model="models/gemini-2.5-flash",
            api_key="test-key",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["text"], "To jest test, napisow")
        self.assertIn("expecting value", metadata["correction_fallback_reason"].lower())


if __name__ == "__main__":
    unittest.main()
