import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import subtitler


class SubtitlerSpeakerTests(unittest.TestCase):
    def test_extract_segment_time_accepts_long_podcast_minutes(self):
        start, end = subtitler.extract_segment_time_from_filename("segment_2_106-29_78_107-15_68.mp4")

        self.assertAlmostEqual(start, 6389.78)
        self.assertAlmostEqual(end, 6435.68)

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

        self.assertEqual(len(events), 2)
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

    def test_requested_local_correction_is_ignored(self):
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

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start"], 0.0)
        self.assertEqual(events[0]["end"], 4.0)
        self.assertEqual(events[0]["text"].replace("\n", " "), "To jest test, napisow druga linia")
        self.assertFalse(metadata["subtitles_corrected"])
        self.assertEqual(metadata["corrected_segments_count"], 0)
        self.assertEqual(metadata["subtitle_corrector_used"], "off")
        self.assertEqual(metadata["correction_fallback_reason"], "external_subtitle_correction_disabled")

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

        self.assertEqual(events[0]["text"], "To jest test, napisow")
        self.assertFalse(metadata["subtitles_corrected"])

    @patch("socket.socket.connect")
    def test_requested_external_correction_never_opens_a_network_connection(self, network_connect):
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
        self.assertEqual(metadata["correction_fallback_reason"], "external_subtitle_correction_disabled")
        self.assertEqual(metadata["subtitle_correction_requested"], "gemini_optional")
        network_connect.assert_not_called()


class DeterministicSubtitleFormatterTests(unittest.TestCase):
    @staticmethod
    def _word(text, start, end):
        return {"text": text, "start": start, "end": end}

    def test_word_timestamps_create_several_short_non_overlapping_cues(self):
        words = [
            self._word(text, index * 0.4, (index + 1) * 0.4)
            for index, text in enumerate(
                "to jest pierwsza krótka fraza, potem pojawia się druga naturalna część wypowiedzi.".split()
            )
        ]
        events, metadata = subtitler.build_subtitle_events_with_metadata(
            [{"start": 0.0, "end": 4.8, "text": "ignored segment text", "words": words}],
            0.0,
            4.8,
            content_type_hint="podcast",
        )

        self.assertGreaterEqual(len(events), 2)
        self.assertTrue(all(1 <= len(event["text"].replace("\n", " ").split()) <= 7 for event in events))
        self.assertTrue(all(left["end"] <= right["start"] for left, right in zip(events, events[1:])))
        self.assertEqual(metadata["word_timestamp_segments"], 1)
        self.assertEqual(metadata["segment_timestamp_fallbacks"], 0)

    def test_segment_timestamps_are_used_when_word_timestamps_are_missing(self):
        text = "pierwsza część wypowiedzi przechodzi potem do kolejnej krótkiej planszy"
        events, metadata = subtitler.build_subtitle_events_with_metadata(
            [{"start": 10.0, "end": 16.0, "text": text}],
            10.0,
            6.0,
            content_type_hint="podcast",
        )

        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[0]["start"], 0.0)
        self.assertAlmostEqual(events[-1]["end"], 6.0)
        self.assertEqual(metadata["word_timestamp_segments"], 0)
        self.assertEqual(metadata["segment_timestamp_fallbacks"], 1)

    def test_formatter_uses_at_most_two_lines(self):
        wrapped = subtitler.wrap_subtitle_text(
            "Najważniejsze ustawienia projektu pozostają zawsze łatwo dostępne"
        )

        self.assertLessEqual(wrapped.count("\n"), 1)
        self.assertEqual(len(wrapped.splitlines()), 2)

    def test_short_connector_is_not_left_alone_on_a_line(self):
        wrapped = subtitler.wrap_subtitle_text(
            "Bardzo długa pierwsza część zdania prowadzi do zakończenia i"
        )

        self.assertTrue(all(line.strip().casefold() != "i" for line in wrapped.splitlines()))

        wrapped_with_leading_connector = subtitler.wrap_subtitle_text(
            "i nadzwyczajnie długa dalsza część zdania"
        )
        self.assertTrue(all(line.strip().casefold() != "i" for line in wrapped_with_leading_connector.splitlines()))

    def test_short_connector_is_not_left_as_its_own_cue(self):
        words = [
            self._word("i", 0.0, 0.1),
            self._word("kolejna", 0.4, 0.7),
            self._word("ważna", 0.7, 1.0),
            self._word("myśl", 1.0, 1.3),
        ]

        events = subtitler.build_subtitle_events(
            [{"start": 0.0, "end": 1.7, "text": "fallback", "words": words}],
            0.0,
            1.7,
            content_type_hint="podcast",
        )

        self.assertTrue(events)
        self.assertTrue(all(event["text"].strip().casefold() != "i" for event in events))
        self.assertEqual(
            " ".join(event["text"].replace("\n", " ") for event in events).casefold(),
            "i kolejna ważna myśl",
        )

    def test_natural_pause_ends_current_cue(self):
        words = [
            self._word("pierwsze", 0.0, 0.3),
            self._word("trzy", 0.3, 0.6),
            self._word("słowa", 0.6, 0.9),
            self._word("kolejna", 1.6, 1.9),
            self._word("krótka", 1.9, 2.2),
            self._word("fraza", 2.2, 2.5),
        ]
        events = subtitler.build_subtitle_events(
            [{"start": 0.0, "end": 2.5, "text": "fallback", "words": words}],
            0.0,
            2.5,
            content_type_hint="podcast",
        )

        self.assertEqual(len(events), 2)
        self.assertLessEqual(events[0]["end"], 0.9)
        self.assertGreaterEqual(events[1]["start"], 1.6)

    def test_events_are_clipped_to_selected_clip_boundaries(self):
        words = [
            self._word(f"słowo{index}", 8.0 + index, 8.8 + index)
            for index in range(7)
        ]
        events = subtitler.build_subtitle_events(
            [{"start": 8.0, "end": 15.0, "text": "fallback", "words": words}],
            10.0,
            3.0,
            content_type_hint="podcast",
        )

        self.assertTrue(events)
        self.assertTrue(all(0.0 <= event["start"] < event["end"] <= 3.0 for event in events))
        rendered_text = " ".join(event["text"].replace("\n", " ") for event in events)
        self.assertNotIn("słowo0", rendered_text)
        self.assertNotIn("słowo6", rendered_text)

    def test_polish_text_spacing_and_capitalization_are_conservative(self):
        normalized = subtitler.normalize_subtitle_text(
            "  żółty   stół ,jest  duży !  ",
            capitalize=True,
        )

        self.assertEqual(normalized, "Żółty stół, jest duży!")

    def test_capitalization_follows_sentences_across_whisper_segments(self):
        events = subtitler.build_subtitle_events(
            [
                {"start": 0.0, "end": 1.0, "text": "to jest"},
                {"start": 1.0, "end": 2.0, "text": "dalszy ciąg."},
                {"start": 2.0, "end": 3.0, "text": "nowe zdanie"},
            ],
            0.0,
            3.0,
            content_type_hint="podcast",
        )

        self.assertEqual(
            [event["text"].replace("\n", " ") for event in events],
            ["To jest dalszy ciąg.", "Nowe zdanie"],
        )

    def test_orphan_o_ja_cue_merges_with_following_phrase(self):
        events = subtitler.merge_orphaned_subtitle_events(
            [
                {"start": 0.0, "end": 0.5, "text": "o, ja,", "speaker": "Speaker 0"},
                {"start": 0.6, "end": 1.8, "text": "teraz mówię to jasno.", "speaker": "Speaker 0"},
            ]
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["text"].replace("\n", " "), "o, ja, teraz mówię to jasno.")
        self.assertLessEqual(len(events[0]["text"].splitlines()), 2)

    def test_orphan_powiem_cue_merges_with_following_phrase(self):
        events = subtitler.merge_orphaned_subtitle_events(
            [
                {"start": 0.0, "end": 0.4, "text": "powiem,", "speaker": "Speaker 0"},
                {"start": 0.5, "end": 1.6, "text": "to jeszcze raz jasno", "speaker": "Speaker 0"},
            ]
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["text"].replace("\n", " "), "powiem, to jeszcze raz jasno")

    def test_long_pause_preserves_short_standalone_cue(self):
        events = subtitler.merge_orphaned_subtitle_events(
            [
                {"start": 0.0, "end": 0.3, "text": "powiem,", "speaker": "Speaker 0"},
                {"start": 1.0, "end": 2.0, "text": "to jeszcze raz jasno", "speaker": "Speaker 0"},
            ]
        )

        self.assertEqual([event["text"] for event in events], ["powiem,", "to jeszcze raz jasno"])

    def test_complete_short_utterance_remains_separate(self):
        events = subtitler.merge_orphaned_subtitle_events(
            [
                {"start": 0.0, "end": 0.3, "text": "Tak.", "speaker": "Speaker 0"},
                {"start": 0.4, "end": 1.5, "text": "To jest kolejna myśl.", "speaker": "Speaker 0"},
            ]
        )

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["text"], "Tak.")

    def test_numeric_range_uses_en_dash(self):
        normalized = subtitler.normalize_subtitle_text("wynik 30 - 40 punktów")

        self.assertEqual(normalized, "wynik 30\N{EN DASH}40 punktów")

    def test_orphan_merge_preserves_whisper_words(self):
        recognized = "zacz\u0119\u0142em"
        events = subtitler.merge_orphaned_subtitle_events(
            [
                {"start": 0.0, "end": 0.4, "text": f"{recognized},", "speaker": "Speaker 0"},
                {"start": 0.5, "end": 1.5, "text": "mówić o tym jasno", "speaker": "Speaker 0"},
            ]
        )

        rendered = " ".join(event["text"].replace("\n", " ") for event in events)
        self.assertIn(recognized, rendered)

    def test_merged_events_stay_monotonic_and_use_at_most_two_lines(self):
        events = subtitler.merge_orphaned_subtitle_events(
            [
                {"start": 0.0, "end": 0.4, "text": "o, ja,", "speaker": "Speaker 0"},
                {"start": 0.5, "end": 1.8, "text": "teraz wyjaśnię wszystko jasno.", "speaker": "Speaker 0"},
                {"start": 2.0, "end": 3.0, "text": "Kolejna pełna wypowiedź.", "speaker": "Speaker 0"},
            ]
        )

        self.assertTrue(all(len(event["text"].splitlines()) <= 2 for event in events))
        self.assertTrue(all(left["end"] <= right["start"] for left, right in zip(events, events[1:])))

    def test_ass_special_characters_are_escaped(self):
        ass = subtitler.create_ass_file(
            [{"start": 0.0, "end": 1.0, "text": r"C:\test {demo}", "speaker": "Default"}]
        )

        self.assertIn(r"C:\\test \{demo\}", ass)

    def test_low_importance_and_chaos_do_not_hide_valid_text(self):
        events = subtitler.build_subtitle_events(
            [{
                "start": 0.0,
                "end": 1.0,
                "text": "zwykła wypowiedź nadal musi pozostać widoczna",
                "importance": 1,
                "chaos": True,
            }],
            0.0,
            1.0,
            content_type_hint="podcast",
        )

        self.assertTrue(events)
        self.assertIn("Zwykła", events[0]["text"])

    def test_formatter_does_not_replace_recognized_words(self):
        recognized = ["oryginalne", "niedoskonałe", "rozpoznanie", "whispera"]
        events = subtitler.build_subtitle_events(
            [{
                "start": 0.0,
                "end": 2.0,
                "text": "fallback",
                "words": [
                    self._word(word, index * 0.5, (index + 1) * 0.5)
                    for index, word in enumerate(recognized)
                ],
            }],
            0.0,
            2.0,
            content_type_hint="podcast",
        )
        rendered_words = " ".join(event["text"].replace("\n", " ") for event in events).split()

        self.assertEqual([word.casefold() for word in rendered_words], recognized)

    def test_ass_uses_vertical_canvas_and_readable_bold_style(self):
        ass = subtitler.create_ass_file(
            [{"start": 0.0, "end": 1.0, "text": "Czytelny napis", "speaker": "Default"}]
        )

        self.assertIn("PlayResX: 1080", ass)
        self.assertIn("PlayResY: 1920", ass)
        self.assertIn(f"Default,{subtitler.DEFAULT_FONT},{subtitler.BASE_FONT_SIZE}", ass)
        self.assertIn(",-1,0,0,0,100,100", ass)
        default_style = next(line for line in ass.splitlines() if line.startswith("Style: Default,"))
        fields = default_style.removeprefix("Style: ").split(",")
        self.assertEqual(fields[5], "&H00000000")
        self.assertEqual(fields[16], str(subtitler.OUTLINE_WIDTH))
        self.assertEqual(fields[17], str(subtitler.SHADOW_SIZE))
        self.assertEqual(fields[19:22], [str(subtitler.MARGIN_H), str(subtitler.MARGIN_H), str(subtitler.MARGIN_V)])

    @patch("subtitler.subtitle_fonts_dir", return_value=None)
    @patch("subtitler.subprocess.run")
    def test_burn_in_uses_explicit_video_settings_and_copies_audio(self, run_mock, _fonts_mock):
        subtitler.add_subtitles_to_video(
            Path("input.mp4"),
            Path("output.mp4"),
            Path("captions.ass"),
        )

        command = run_mock.call_args.args[0]
        self.assertIn("libx264", command)
        self.assertIn("yuv420p", command)
        self.assertIn("+faststart", command)
        self.assertEqual(command[command.index("-preset") + 1], "fast")
        self.assertEqual(command[command.index("-crf") + 1], "20")
        self.assertEqual(command[command.index("-c:a") + 1], "copy")
        self.assertIn("0:a:0?", command)
        run_mock.assert_called_once_with(command, check=True)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for the subtitle burn-in smoke test")
    def test_small_ffmpeg_burn_in_smoke(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            input_video = root / "input.mp4"
            output_video = root / "output.mp4"
            ass_file = root / "captions.ass"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=180x320:d=0.25",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=48000:cl=stereo",
                    "-shortest",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    str(input_video),
                ],
                check=True,
                capture_output=True,
            )
            ass_file.write_text(
                subtitler.create_ass_file(
                    [{"start": 0.0, "end": 0.25, "text": "Test", "speaker": "Default"}]
                ),
                encoding="utf-8",
            )

            subtitler.add_subtitles_to_video(input_video, output_video, ass_file)

            self.assertTrue(output_video.is_file())
            self.assertGreater(output_video.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
