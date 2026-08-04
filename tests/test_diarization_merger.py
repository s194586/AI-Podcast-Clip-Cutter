import copy
import itertools
import unittest
from pathlib import Path
from unittest.mock import patch

import diarization.merger as merger_module
from diarization import (
    DiarizationMergeError,
    SpeakerTurn,
    merge_speaker_turns,
)
from transcription.base import TranscriptSegment, TranscriptWord, TranscriptionResult
from transcription.segment_identity import canonical_segment_id, canonical_time_range


def _word(start: float, end: float, text: str) -> TranscriptWord:
    return TranscriptWord(start=start, end=end, text=text)


def _segment(
    words: list[TranscriptWord],
    *,
    start: float | None = None,
    end: float | None = None,
    text: str | None = None,
    importance: int = 3,
    chaos: bool = False,
) -> TranscriptSegment:
    return TranscriptSegment(
        start=words[0].start if start is None else start,
        end=words[-1].end if end is None else end,
        text=" ".join(word.text for word in words) if text is None else text,
        importance=importance,
        chaos=chaos,
        words=words,
    )


class DiarizationMergerTests(unittest.TestCase):
    def test_one_speaker_preserves_text_and_punctuation(self):
        segment = _segment(
            [_word(0.2, 0.8, "Hello,"), _word(1.0, 1.6, "world!")],
            start=0.0,
            end=2.0,
            text="Hello, world!",
        )

        result = merge_speaker_turns([segment], [SpeakerTurn(0.0, 2.0, "raw-a")])

        self.assertEqual(len(result.segments), 1)
        self.assertEqual(result.segments[0].speaker, "Speaker 0")
        self.assertEqual(result.segments[0].text, "Hello, world!")

    def test_speaker_split_preserves_custom_segment_fields(self):
        segment = _segment(
            [_word(0.0, 0.8, "Left"), _word(1.2, 2.0, "right")],
            start=0.0,
            end=2.0,
            text="Left right",
        )
        segment.extra_fields = {"source_confidence": 0.91, "editor_note": "keep"}

        result = merge_speaker_turns(
            [segment],
            [SpeakerTurn(0.0, 0.8, "a"), SpeakerTurn(1.2, 2.0, "b")],
        )

        self.assertEqual(
            [item.extra_fields for item in result.segments],
            [segment.extra_fields, segment.extra_fields],
        )
        self.assertEqual(
            [item.to_dict()["editor_note"] for item in result.segments],
            ["keep", "keep"],
        )

    def test_two_speakers_do_not_merge_across_whisper_segments(self):
        first = _segment([_word(0.0, 1.0, "First.")])
        second = _segment([_word(1.0, 2.0, "Second.")])
        turns = [SpeakerTurn(0.0, 1.0, "a"), SpeakerTurn(1.0, 2.0, "b")]

        result = merge_speaker_turns([first, second], turns)

        self.assertEqual([segment.speaker for segment in result.segments], ["Speaker 0", "Speaker 1"])
        self.assertEqual(len(result.segments), 2)

    def test_overlapping_parent_segments_raise_clear_error(self):
        segments = [
            _segment([_word(0.0, 0.5, "a"), _word(1.5, 2.0, "b")], start=0.0, end=2.0),
            _segment([_word(1.0, 1.5, "c"), _word(2.5, 3.0, "d")], start=1.0, end=3.0),
        ]
        turns = [
            SpeakerTurn(0.0, 0.5, "a"),
            SpeakerTurn(1.0, 1.5, "c"),
            SpeakerTurn(1.5, 2.0, "b"),
            SpeakerTurn(2.5, 3.0, "d"),
        ]

        with self.assertRaisesRegex(DiarizationMergeError, "overlaps the previous"):
            merge_speaker_turns(segments, turns)

    def test_out_of_order_parent_segments_raise_clear_error(self):
        segments = [
            _segment([_word(2.0, 3.0, "later")]),
            _segment([_word(0.0, 1.0, "earlier")]),
        ]

        with self.assertRaisesRegex(DiarizationMergeError, "not chronological"):
            merge_speaker_turns(segments, [SpeakerTurn(0.0, 3.0, "a")])

    def test_speaker_change_inside_whisper_segment_splits_at_gap_midpoint(self):
        segment = _segment(
            [_word(0.0, 0.8, "Left."), _word(1.2, 2.0, "Right.")],
            start=0.0,
            end=2.0,
        )
        turns = [SpeakerTurn(0.0, 0.8, "a"), SpeakerTurn(1.2, 2.0, "b")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual([(item.start, item.end) for item in result.segments], [(0.0, 1.0), (1.0, 2.0)])
        self.assertEqual([item.speaker for item in result.segments], ["Speaker 0", "Speaker 1"])

    def test_compacted_faster_whisper_tokens_reconstruct_parent_text_per_speaker(self):
        segment = _segment(
            [
                _word(0.0, 0.4, "Ala"),
                _word(0.5, 0.9, "ma"),
                _word(1.0, 1.5, "kota."),
            ],
            start=0.0,
            end=1.5,
            text="Ala ma kota.",
        )
        turns = [SpeakerTurn(0.0, 0.9, "a"), SpeakerTurn(1.0, 1.5, "b")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual([item.text for item in result.segments], ["Ala ma", "kota."])
        self.assertEqual([item.speaker for item in result.segments], ["Speaker 0", "Speaker 1"])

    def test_multiple_consecutive_speaker_changes_keep_ordered_boundaries(self):
        segment = _segment(
            [
                _word(0.0, 0.5, "A"),
                _word(1.0, 1.5, "B"),
                _word(2.0, 2.5, "C"),
                _word(3.0, 4.0, "Again"),
            ],
            start=0.0,
            end=4.0,
        )
        turns = [
            SpeakerTurn(0.0, 0.5, "a"),
            SpeakerTurn(1.0, 1.5, "b"),
            SpeakerTurn(2.0, 2.5, "c"),
            SpeakerTurn(3.0, 4.0, "a"),
        ]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual(
            [(item.start, item.end) for item in result.segments],
            [(0.0, 0.75), (0.75, 1.75), (1.75, 2.75), (2.75, 4.0)],
        )
        self.assertEqual(
            [item.speaker for item in result.segments],
            ["Speaker 0", "Speaker 1", "Speaker 2", "Speaker 0"],
        )

    def test_largest_overlap_wins(self):
        segment = _segment([_word(1.0, 3.0, "Word")])
        turns = [SpeakerTurn(0.0, 1.5, "a"), SpeakerTurn(1.5, 4.0, "b")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual(result.segments[0].speaker, "Speaker 1")

    def test_equal_overlap_uses_remaining_tie_breaks(self):
        segment = _segment([_word(1.0, 3.0, "Word")])
        turns = [SpeakerTurn(1.5, 3.5, "b"), SpeakerTurn(0.5, 2.5, "a")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual(result.segments[0].speaker, "Speaker 0")

    def test_fully_tied_turns_use_lower_normalized_speaker_number(self):
        segment = _segment([_word(1.0, 2.0, "Tied")])
        turns = [SpeakerTurn(0.0, 3.0, "zeta"), SpeakerTurn(0.0, 3.0, "alpha")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual(result.speaker_label_map["alpha"], "Speaker 0")
        self.assertEqual(result.segments[0].speaker, "Speaker 0")

    def test_word_midpoint_on_boundary_belongs_to_later_turn(self):
        segment = _segment([_word(1.0, 3.0, "Boundary")])
        turns = [SpeakerTurn(0.0, 2.0, "a"), SpeakerTurn(2.0, 4.0, "b")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual(result.segments[0].speaker, "Speaker 1")

    def test_word_without_overlap_uses_nearest_turn_and_reports_distance(self):
        segment = _segment([_word(5.0, 6.0, "Later")])

        result = merge_speaker_turns([segment], [SpeakerTurn(0.0, 4.0, "a")])

        self.assertEqual(result.segments[0].speaker, "Speaker 0")
        self.assertEqual(result.non_overlapping_word_count, 1)
        self.assertEqual(result.max_non_overlap_distance_seconds, 1.0)

    def test_equal_nearest_distance_prefers_preceding_turn(self):
        segment = _segment([_word(5.0, 6.0, "Middle")])
        turns = [SpeakerTurn(7.0, 8.0, "later"), SpeakerTurn(3.0, 4.0, "earlier")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual(result.segments[0].speaker, "Speaker 0")
        self.assertEqual(result.max_non_overlap_distance_seconds, 1.0)

    def test_no_overlap_preceding_tie_group_preserves_technical_tie_breaks(self):
        segment = _segment([_word(5.0, 6.0, "Later")])
        turns = [SpeakerTurn(2.0, 4.0, "later-start"), SpeakerTurn(0.0, 4.0, "earlier-start")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual(result.segments[0].speaker, "Speaker 0")
        self.assertEqual(result.max_non_overlap_distance_seconds, 1.0)

    def test_no_overlap_following_tie_group_prefers_lower_normalized_speaker(self):
        segment = _segment([_word(0.0, 1.0, "Earlier")])
        turns = [SpeakerTurn(2.0, 3.0, "zeta"), SpeakerTurn(2.0, 4.0, "alpha")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual(result.speaker_label_map["alpha"], "Speaker 0")
        self.assertEqual(result.segments[0].speaker, "Speaker 0")
        self.assertEqual(result.max_non_overlap_distance_seconds, 1.0)

    def test_maximum_no_overlap_distance_is_computed_across_all_words(self):
        segment = _segment(
            [_word(0.0, 1.0, "far"), _word(4.0, 5.0, "middle"), _word(8.0, 9.0, "near")],
            start=0.0,
            end=9.0,
        )

        result = merge_speaker_turns([segment], [SpeakerTurn(10.0, 11.0, "a")])

        self.assertEqual(result.non_overlapping_word_count, 3)
        self.assertEqual(result.max_non_overlap_distance_seconds, 9.0)

    def test_silence_gap_uses_gap_midpoint_without_overlapping_fragments(self):
        segment = _segment(
            [_word(0.0, 1.0, "Before"), _word(3.0, 4.0, "After")],
            start=0.0,
            end=5.0,
        )
        turns = [SpeakerTurn(0.0, 1.0, "a"), SpeakerTurn(3.0, 4.0, "b")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual([(item.start, item.end) for item in result.segments], [(0.0, 2.0), (2.0, 5.0)])

    def test_overlapping_turns_use_midpoint_distance_then_earlier_start(self):
        segment = _segment([_word(1.0, 3.0, "Overlap")])
        turns = [SpeakerTurn(1.0, 4.0, "b"), SpeakerTurn(0.0, 3.0, "a")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual(result.segments[0].speaker, "Speaker 0")

    def test_raw_labels_are_mapped_by_first_chronological_appearance(self):
        segment = _segment(
            [_word(1.0, 2.0, "Early"), _word(6.0, 7.0, "Late")],
            start=0.0,
            end=8.0,
        )
        turns = [SpeakerTurn(5.0, 8.0, "z"), SpeakerTurn(0.0, 4.0, "a")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual(result.speaker_label_map, {"a": "Speaker 0", "z": "Speaker 1"})
        self.assertEqual([item.speaker for item in result.segments], ["Speaker 0", "Speaker 1"])

    def test_turn_input_permutations_produce_identical_results(self):
        segment = _segment(
            [_word(0.0, 0.5, "A"), _word(1.0, 1.5, "B"), _word(2.0, 2.5, "C")],
            start=0.0,
            end=2.5,
        )
        turns = [
            SpeakerTurn(0.0, 0.5, "a"),
            SpeakerTurn(1.0, 1.5, "b"),
            SpeakerTurn(2.0, 2.5, "a"),
        ]

        fingerprints = []
        for permutation in itertools.permutations(turns):
            result = merge_speaker_turns([segment], list(permutation))
            fingerprints.append(
                (
                    result.speaker_label_map,
                    [
                        (item.start, item.end, item.speaker, item.text)
                        for item in result.segments
                    ],
                )
            )

        self.assertTrue(all(item == fingerprints[0] for item in fingerprints[1:]))

    def test_repeated_raw_label_across_multiple_turns_maps_to_one_speaker(self):
        segment = _segment(
            [_word(0.0, 0.5, "A"), _word(1.0, 1.5, "B"), _word(2.0, 2.5, "A2")],
            start=0.0,
            end=2.5,
        )
        turns = [
            SpeakerTurn(0.0, 0.5, "same"),
            SpeakerTurn(1.0, 1.5, "other"),
            SpeakerTurn(2.0, 2.5, "same"),
        ]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual(result.speaker_label_map, {"same": "Speaker 0", "other": "Speaker 1"})
        self.assertEqual(
            [item.speaker for item in result.segments],
            ["Speaker 0", "Speaker 1", "Speaker 0"],
        )

    def test_identical_first_start_uses_raw_label_codepoint_order(self):
        segment = _segment(
            [_word(0.1, 0.9, "Alpha"), _word(1.1, 1.9, "Zeta")],
            start=0.0,
            end=2.0,
        )
        turns = [SpeakerTurn(0.0, 2.0, "zeta"), SpeakerTurn(0.0, 1.0, "alpha")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual(result.speaker_label_map, {"alpha": "Speaker 0", "zeta": "Speaker 1"})
        self.assertEqual([item.speaker for item in result.segments], ["Speaker 0", "Speaker 1"])

    def test_asr_words_with_no_turns_raise_without_speaker_zero_fallback(self):
        with self.assertRaisesRegex(DiarizationMergeError, "speaker turn list is empty"):
            merge_speaker_turns([_segment([_word(0.0, 1.0, "Word")])], [])

    def test_text_without_word_timestamps_raises(self):
        segment = TranscriptSegment(start=0.0, end=1.0, text="Missing timestamps")

        with self.assertRaisesRegex(DiarizationMergeError, "has no word timestamps"):
            merge_speaker_turns([segment], [SpeakerTurn(0.0, 1.0, "a")])

    def test_invalid_word_and_turn_ranges_raise_clear_errors(self):
        invalid_word_segment = TranscriptSegment(
            start=0.0,
            end=1.0,
            text="Invalid",
            words=[_word(0.5, 0.5, "Invalid")],
        )
        with self.assertRaisesRegex(DiarizationMergeError, "Word 0.*end must be after start"):
            merge_speaker_turns([invalid_word_segment], [SpeakerTurn(0.0, 1.0, "a")])

        with self.assertRaisesRegex(DiarizationMergeError, "Speaker turn 0 end must be after start"):
            merge_speaker_turns([], [SpeakerTurn(1.0, 1.0, "a")])

    def test_nonfinite_negative_and_outside_parent_timestamps_raise(self):
        invalid_words = [
            _word(float("nan"), 0.5, "nan"),
            _word(0.0, float("inf"), "inf"),
            _word(-0.1, 0.5, "negative"),
            _word(0.5, 1.1, "outside"),
        ]
        for word in invalid_words:
            with self.subTest(word=word):
                segment = TranscriptSegment(start=0.0, end=1.0, text=word.text, words=[word])
                with self.assertRaises(DiarizationMergeError):
                    merge_speaker_turns([segment], [SpeakerTurn(0.0, 1.0, "a")])

        invalid_turns = [
            SpeakerTurn(float("nan"), 1.0, "a"),
            SpeakerTurn(0.0, float("inf"), "a"),
            SpeakerTurn(-0.1, 1.0, "a"),
        ]
        for turn in invalid_turns:
            with self.subTest(turn=turn):
                with self.assertRaises(DiarizationMergeError):
                    merge_speaker_turns([], [turn])

    def test_nonchronological_words_raise_instead_of_being_sorted(self):
        segment = _segment(
            [_word(2.0, 3.0, "later"), _word(0.0, 1.0, "earlier")],
            start=0.0,
            end=4.0,
        )

        with self.assertRaisesRegex(DiarizationMergeError, "chronological timestamp order"):
            merge_speaker_turns(
                [segment],
                [SpeakerTurn(0.0, 1.0, "a"), SpeakerTurn(2.0, 3.0, "b")],
            )

    def test_centisecond_boundary_collision_raises_instead_of_merging_speakers(self):
        segment = _segment(
            [_word(0.001, 0.004, "A"), _word(0.005, 0.009, "B")],
            start=0.0,
            end=0.03,
        )
        turns = [SpeakerTurn(0.001, 0.004, "a"), SpeakerTurn(0.005, 0.009, "b")]

        with self.assertRaisesRegex(DiarizationMergeError, "cannot be split.*centisecond"):
            merge_speaker_turns([segment], turns)

    def test_split_fragments_use_existing_canonical_id_generator(self):
        segment = _segment(
            [_word(0.0, 0.8, "Left"), _word(1.2, 2.0, "Right")],
            start=0.0,
            end=2.0,
        )
        turns = [SpeakerTurn(0.0, 0.8, "a"), SpeakerTurn(1.2, 2.0, "b")]

        result = merge_speaker_turns([segment], turns)
        ids = [item.to_dict()["segment_id"] for item in result.segments]

        self.assertEqual(ids, [canonical_segment_id(0.0, 1.0), canonical_segment_id(1.0, 2.0)])

    def test_merged_ranges_and_canonical_ids_are_globally_unique(self):
        segments = [
            _segment([_word(0.0, 0.8, "A"), _word(1.2, 2.0, "B")], start=0.0, end=2.0),
            _segment([_word(2.0, 2.8, "C"), _word(3.2, 4.0, "D")], start=2.0, end=4.0),
        ]
        turns = [
            SpeakerTurn(0.0, 0.8, "a"),
            SpeakerTurn(1.2, 2.0, "b"),
            SpeakerTurn(2.0, 2.8, "a"),
            SpeakerTurn(3.2, 4.0, "b"),
        ]

        result = merge_speaker_turns(segments, turns)
        ranges = [canonical_time_range(item.start, item.end) for item in result.segments]
        ids = [item.to_dict()["segment_id"] for item in result.segments]

        self.assertEqual(len(ranges), len(set(ranges)))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(right[0] >= left[1] for left, right in zip(ranges, ranges[1:])))

    def test_valid_merged_result_serializes_through_transcription_contract(self):
        segments = [
            _segment([_word(0.0, 0.8, "A"), _word(1.2, 2.0, "B")], start=0.0, end=2.0),
            _segment([_word(2.0, 3.0, "C")], start=2.0, end=3.0),
        ]
        turns = [SpeakerTurn(0.0, 0.8, "a"), SpeakerTurn(1.2, 3.0, "b")]

        merged = merge_speaker_turns(segments, turns)
        payload = TranscriptionResult(
            backend="test",
            model="test",
            audio_path=Path("input.wav"),
            language="en",
            duration_seconds=3.0,
            transcription_seconds=0.1,
            segments=merged.segments,
            device="cpu",
            compute_type="int8",
        ).to_dict()

        self.assertEqual(len(payload["segments"]), len(merged.segments))
        self.assertEqual(
            len({item["segment_id"] for item in payload["segments"]}),
            len(payload["segments"]),
        )

    def test_unsplit_segment_preserves_time_range_and_canonical_id(self):
        segment = _segment(
            [_word(0.123, 2.345, "Stable")],
            start=0.123,
            end=2.345,
        )
        original_id = segment.to_dict()["segment_id"]

        result = merge_speaker_turns([segment], [SpeakerTurn(0.0, 3.0, "a")])

        self.assertEqual((result.segments[0].start, result.segments[0].end), (0.123, 2.345))
        self.assertEqual(result.segments[0].to_dict()["segment_id"], original_id)

    def test_split_fragments_inherit_importance_and_chaos(self):
        segment = _segment(
            [_word(0.0, 0.8, "Left"), _word(1.2, 2.0, "Right")],
            start=0.0,
            end=2.0,
            importance=5,
            chaos=True,
        )
        turns = [SpeakerTurn(0.0, 0.8, "a"), SpeakerTurn(1.2, 2.0, "b")]

        result = merge_speaker_turns([segment], turns)

        self.assertTrue(all(item.importance == 5 and item.chaos for item in result.segments))

    def test_each_word_appears_exactly_once(self):
        words = [_word(0.0, 0.5, "one"), _word(0.6, 1.0, "two"), _word(1.1, 1.5, "three")]
        segment = _segment(words, start=0.0, end=1.5)
        turns = [SpeakerTurn(0.0, 0.5, "a"), SpeakerTurn(0.6, 1.0, "b"), SpeakerTurn(1.1, 1.5, "a")]

        result = merge_speaker_turns([segment], turns)
        output_words = [word for item in result.segments for word in item.words]

        self.assertEqual([word.text for word in output_words], ["one", "two", "three"])
        self.assertEqual(len(output_words), len(words))

    def test_word_order_and_timestamps_remain_identical(self):
        words = [_word(0.0, 0.7, "first"), _word(0.5, 1.2, "second"), _word(1.3, 2.0, "third")]
        segment = _segment(words, start=0.0, end=2.0)
        turns = [SpeakerTurn(0.0, 0.8, "a"), SpeakerTurn(0.8, 2.0, "b")]

        result = merge_speaker_turns([segment], turns)
        actual = [(word.start, word.end, word.text) for item in result.segments for word in item.words]

        self.assertEqual(actual, [(word.start, word.end, word.text) for word in words])

    def test_separate_punctuation_token_uses_parent_text_spacing(self):
        segment = _segment(
            [_word(0.0, 0.4, "Hello"), _word(0.4, 0.8, ","), _word(1.0, 1.5, "world!")],
            start=0.0,
            end=1.5,
            text="Hello, world!",
        )
        turns = [SpeakerTurn(0.0, 0.8, "a"), SpeakerTurn(1.0, 1.5, "b")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual([item.text for item in result.segments], ["Hello,", "world!"])

    def test_original_token_whitespace_is_compacted_without_new_spaces(self):
        words = [
            _word(0.0, 0.4, "Hello"),
            _word(0.4, 0.8, "   world"),
            _word(1.0, 1.5, " next"),
        ]
        segment = _segment(words, start=0.0, end=1.5, text="Hello world next")
        turns = [SpeakerTurn(0.0, 0.8, "a"), SpeakerTurn(1.0, 1.5, "b")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual([item.text for item in result.segments], ["Hello world", "next"])
        self.assertEqual(
            [word.text for item in result.segments for word in item.words],
            [word.text for word in words],
        )

    def test_no_space_language_tokens_do_not_gain_spaces(self):
        segment = _segment(
            [
                _word(0.0, 0.4, "你"),
                _word(0.4, 0.8, "好"),
                _word(1.0, 1.5, "世"),
            ],
            start=0.0,
            end=1.5,
            text="你好世",
        )
        turns = [SpeakerTurn(0.0, 0.8, "a"), SpeakerTurn(1.0, 1.5, "b")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual([item.text for item in result.segments], ["你好", "世"])

    def test_repeated_words_are_matched_sequentially(self):
        segment = _segment(
            [_word(0.0, 0.4, "echo"), _word(0.5, 0.9, "echo"), _word(1.0, 1.4, "again")],
            start=0.0,
            end=1.4,
            text="echo echo again",
        )
        turns = [SpeakerTurn(0.0, 0.4, "a"), SpeakerTurn(0.5, 1.4, "b")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual([item.text for item in result.segments], ["echo", "echo again"])

    def test_unmatchable_word_text_raises_without_reconstruction_fallback(self):
        segment = _segment(
            [_word(0.0, 0.4, "Ala"), _word(0.5, 0.9, "pies")],
            start=0.0,
            end=0.9,
            text="Ala ma kota.",
        )

        with self.assertRaisesRegex(DiarizationMergeError, "cannot be matched sequentially"):
            merge_speaker_turns([segment], [SpeakerTurn(0.0, 0.9, "a")])

    def test_uncovered_text_between_tokens_raises_without_speaker_assignment(self):
        segment = _segment(
            [_word(0.0, 0.4, "Hello"), _word(0.5, 0.9, "world")],
            start=0.0,
            end=0.9,
            text="Hello EXTRA world",
        )
        turns = [SpeakerTurn(0.0, 0.4, "a"), SpeakerTurn(0.5, 0.9, "b")]

        with self.assertRaisesRegex(DiarizationMergeError, "non-whitespace text"):
            merge_speaker_turns([segment], turns)

    def test_first_token_cannot_match_inside_uncovered_text(self):
        segment = _segment(
            [_word(0.0, 0.4, "he"), _word(0.5, 0.9, "he")],
            start=0.0,
            end=0.9,
            text="the he",
        )

        with self.assertRaisesRegex(DiarizationMergeError, "non-whitespace text"):
            merge_speaker_turns(
                [segment],
                [SpeakerTurn(0.0, 0.4, "a"), SpeakerTurn(0.5, 0.9, "b")],
            )

    def test_overlapping_word_timestamps_split_by_word_midpoints(self):
        segment = _segment(
            [_word(0.0, 1.2, "Left"), _word(0.8, 2.0, "Right")],
            start=0.0,
            end=2.0,
        )
        turns = [SpeakerTurn(0.0, 0.8, "a"), SpeakerTurn(0.8, 2.0, "b")]

        result = merge_speaker_turns([segment], turns)

        self.assertEqual([(item.start, item.end) for item in result.segments], [(0.0, 1.0), (1.0, 2.0)])

    def test_inputs_and_nested_words_are_not_mutated_or_shared(self):
        segments = [
            _segment(
                [_word(0.0, 0.8, "Hello"), _word(1.2, 2.0, " world")],
                start=0.0,
                end=2.0,
            )
        ]
        turns = [SpeakerTurn(1.2, 2.0, "b"), SpeakerTurn(0.0, 0.8, "a")]
        original_segments = copy.deepcopy(segments)
        original_turns = copy.deepcopy(turns)

        result = merge_speaker_turns(segments, turns)

        self.assertEqual(segments, original_segments)
        self.assertEqual(turns, original_turns)
        self.assertIsNot(result.segments[0], segments[0])
        self.assertIsNot(result.segments[0].words[0], segments[0].words[0])
        result.segments[0].words[0].text = "changed"
        self.assertEqual(segments[0].words[0].text, "Hello")

    def test_sparse_turn_search_does_not_compare_every_word_with_every_turn(self):
        item_count = 1000
        segments = [
            _segment([_word(float(index), float(index) + 0.5, str(index))])
            for index in range(item_count)
        ]
        turns = [
            SpeakerTurn(float(index), float(index) + 0.5, f"speaker-{index % 2}")
            for index in range(item_count)
        ]
        overlap_calls = 0
        original_overlap = merger_module._word_turn_overlap

        def counted_overlap(*args):
            nonlocal overlap_calls
            overlap_calls += 1
            return original_overlap(*args)

        with patch.object(merger_module, "_word_turn_overlap", side_effect=counted_overlap):
            result = merge_speaker_turns(segments, turns)

        self.assertEqual(len(result.segments), item_count)
        self.assertLessEqual(overlap_calls, item_count * 2)

    def test_empty_transcript_is_deterministic_and_has_no_speaker_labels(self):
        result = merge_speaker_turns([], [SpeakerTurn(0.0, 1.0, "a")])

        self.assertEqual(result.segments, [])
        self.assertEqual(result.speaker_label_map, {})
        self.assertEqual(result.non_overlapping_word_count, 0)
        self.assertEqual(result.max_non_overlap_distance_seconds, 0.0)


if __name__ == "__main__":
    unittest.main()
