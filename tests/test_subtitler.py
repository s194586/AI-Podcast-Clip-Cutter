import unittest

import subtitler


class SubtitlerSpeakerTests(unittest.TestCase):
    def test_speaker_style_is_deterministic_for_same_label(self):
        first = subtitler.speaker_style("Speaker 3")
        second = subtitler.speaker_style("Speaker 3")
        self.assertEqual(first, second)

    def test_speaker_smoothing_merges_short_flip_between_same_speakers(self):
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
        self.assertEqual(events[1]["speaker"], "Speaker 0")
        self.assertEqual(metadata["speaker_flips_smoothed"], 1)
        self.assertTrue(metadata["speaker_smoothing_enabled"])
        self.assertEqual(metadata["speaker_color_map"]["Speaker 0"], subtitler.speaker_color_map(["Speaker 0"])["Speaker 0"])

    def test_long_real_speaker_change_is_not_merged(self):
        transcript = [
            {"start": "00:00.00", "end": "00:02.00", "text": "part one", "speaker": "Speaker 0"},
            {"start": "00:02.00", "end": "00:04.50", "text": "different speaker long turn", "speaker": "Speaker 1"},
            {"start": "00:04.50", "end": "00:06.00", "text": "speaker zero again", "speaker": "Speaker 0"},
        ]
        events, metadata = subtitler.build_subtitle_events_with_metadata(
            transcript,
            0.0,
            6.0,
            speaker_smoothing_window=1.0,
        )

        self.assertEqual(events[1]["speaker"], "Speaker 1")
        self.assertEqual(metadata["speaker_flips_smoothed"], 0)

    def test_missing_speaker_labels_still_render_with_default_style(self):
        transcript = [
            {"start": "00:00.00", "end": "00:02.00", "text": "no speaker label here"},
        ]
        events, metadata = subtitler.build_subtitle_events_with_metadata(
            transcript,
            0.0,
            2.0,
        )

        self.assertEqual(events[0]["speaker"], subtitler.DEFAULT_STYLE_NAME)
        self.assertTrue(metadata["speaker_smoothing_enabled"])
        self.assertEqual(metadata["detected_speaker_count"], 0)


if __name__ == "__main__":
    unittest.main()
