from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from transcription.base import TranscriptSegment, TranscriptWord, compact_text
from transcription.segment_identity import (
    SegmentIdentityError,
    canonical_time_range,
    centiseconds_to_seconds,
    parse_time_to_decimal,
    time_to_centiseconds,
)

from .base import SpeakerTurn


class DiarizationMergeError(ValueError):
    """Raised when diarization turns cannot be merged without corrupting the transcript."""


@dataclass
class DiarizationMergeResult:
    """Merged segments plus speaker normalization and no-overlap diagnostics."""

    segments: list[TranscriptSegment]
    speaker_label_map: dict[str, str]
    non_overlapping_word_count: int = 0
    max_non_overlap_distance_seconds: float = 0.0


@dataclass(frozen=True)
class _ValidatedTurn:
    start: Decimal
    end: Decimal
    raw_speaker: str
    normalized_speaker: str
    speaker_number: int


@dataclass(frozen=True)
class _AssignedWord:
    source_index: int
    word: TranscriptWord
    speaker: str
    text_start: int
    text_end: int


class _TurnSweep:
    def __init__(self, turns: list[_ValidatedTurn]):
        self.turns = sorted(
            turns,
            key=lambda turn: (
                turn.start,
                turn.end,
                turn.speaker_number,
                turn.raw_speaker,
            ),
        )
        self.next_index = 0
        self.active: list[_ValidatedTurn] = []
        self.latest_preceding_end: Decimal | None = None
        self.latest_preceding_turn: _ValidatedTurn | None = None
        self.cached_following_index: int | None = None
        self.cached_following_turn: _ValidatedTurn | None = None

    def assign(self, word: TranscriptWord) -> tuple[str, Decimal | None]:
        word_start = _as_decimal(word.start, "Word start")
        word_end = _as_decimal(word.end, "Word end")
        word_midpoint = (word_start + word_end) / 2

        while self.next_index < len(self.turns):
            turn = self.turns[self.next_index]
            if turn.start >= word_end:
                break
            self.active.append(turn)
            self.next_index += 1

        still_active: list[_ValidatedTurn] = []
        for turn in self.active:
            if turn.end <= word_start:
                self._record_preceding(turn)
            else:
                still_active.append(turn)
        self.active = still_active

        if self.active:
            selected = min(
                self.active,
                key=lambda turn: (
                    -_word_turn_overlap(word_start, word_end, turn),
                    0 if turn.start <= word_midpoint < turn.end else 1,
                    abs(word_midpoint - ((turn.start + turn.end) / 2)),
                    turn.start,
                    turn.speaker_number,
                    turn.end,
                    turn.raw_speaker,
                ),
            )
            return selected.normalized_speaker, None

        nearest: list[tuple[_ValidatedTurn, Decimal, bool]] = []
        if self.latest_preceding_turn is not None:
            turn = self.latest_preceding_turn
            nearest.append((turn, word_start - turn.end, True))
        following_turn = self._nearest_following_turn()
        if following_turn is not None:
            nearest.append((following_turn, following_turn.start - word_end, False))

        if not nearest:
            raise DiarizationMergeError("Cannot find a speaker turn for an ASR word.")

        selected, distance, _preceding = min(
            nearest,
            key=lambda item: (
                item[1],
                0 if item[2] else 1,
                item[0].start,
                item[0].speaker_number,
                item[0].end,
                item[0].raw_speaker,
            ),
        )
        return selected.normalized_speaker, distance

    def _record_preceding(self, turn: _ValidatedTurn) -> None:
        if self.latest_preceding_end is None or turn.end > self.latest_preceding_end:
            self.latest_preceding_end = turn.end
            self.latest_preceding_turn = turn
        elif (
            turn.end == self.latest_preceding_end
            and self.latest_preceding_turn is not None
            and _nearest_technical_key(turn) < _nearest_technical_key(self.latest_preceding_turn)
        ):
            self.latest_preceding_turn = turn

    def _nearest_following_turn(self) -> _ValidatedTurn | None:
        if self.next_index >= len(self.turns):
            return None
        if self.cached_following_index == self.next_index:
            return self.cached_following_turn

        next_start = self.turns[self.next_index].start
        index = self.next_index
        group: list[_ValidatedTurn] = []
        while index < len(self.turns) and self.turns[index].start == next_start:
            group.append(self.turns[index])
            index += 1
        self.cached_following_index = self.next_index
        self.cached_following_turn = min(group, key=_nearest_technical_key)
        return self.cached_following_turn


def merge_speaker_turns(
    segments: list[TranscriptSegment],
    turns: list[SpeakerTurn],
) -> DiarizationMergeResult:
    """Return copied transcript segments split at deterministic speaker boundaries."""

    validated_turn_ranges = [_validate_turn(turn, index) for index, turn in enumerate(turns)]
    if not segments:
        return DiarizationMergeResult(segments=[], speaker_label_map={})

    speaker_label_map = _normalize_speaker_labels(validated_turn_ranges)
    validated_turns = [
        _ValidatedTurn(
            start=start,
            end=end,
            raw_speaker=raw_speaker,
            normalized_speaker=speaker_label_map[raw_speaker],
            speaker_number=int(speaker_label_map[raw_speaker].split()[-1]),
        )
        for start, end, raw_speaker in validated_turn_ranges
    ]

    source_words: list[TranscriptWord] = []
    segment_words: list[list[_AssignedWord]] = []
    next_source_index = 0
    previous_segment_end: Decimal | None = None
    for segment_index, segment in enumerate(segments):
        segment_start, segment_end = _validate_segment(segment, segment_index)
        if previous_segment_end is not None and segment_start < previous_segment_end:
            raise DiarizationMergeError(
                f"Transcript segment {segment_index} is not chronological or overlaps "
                "the previous transcript segment."
            )
        previous_segment_end = segment_end
        if not segment.words:
            if compact_text(segment.text):
                raise DiarizationMergeError(
                    f"Transcript segment {segment_index} contains text but has no word timestamps."
                )
            segment_words.append([])
            continue

        validated_words: list[TranscriptWord] = []
        previous_word_range: tuple[Decimal, Decimal, Decimal] | None = None
        for word_index, word in enumerate(segment.words):
            word_start, word_end = _validate_word(word, segment, segment_index, word_index)
            word_midpoint = (word_start + word_end) / 2
            if previous_word_range is not None:
                previous_start, previous_end, previous_midpoint = previous_word_range
                if (
                    word_start < previous_start
                    or word_end < previous_end
                    or word_midpoint < previous_midpoint
                ):
                    raise DiarizationMergeError(
                        f"Word {word_index} in transcript segment {segment_index} is not "
                        "in chronological timestamp order."
                    )
            previous_word_range = (word_start, word_end, word_midpoint)
            validated_words.append(word)

        text_ranges = _match_word_text_ranges(segment, segment_index)
        assigned: list[_AssignedWord] = []
        for word, (text_start, text_end) in zip(validated_words, text_ranges):
            source_words.append(word)
            assigned.append(
                _AssignedWord(
                    source_index=next_source_index,
                    word=word,
                    speaker="",
                    text_start=text_start,
                    text_end=text_end,
                )
            )
            next_source_index += 1
        segment_words.append(assigned)

    if source_words and not validated_turns:
        raise DiarizationMergeError("Cannot assign ASR words because the speaker turn list is empty.")

    output_segments: list[TranscriptSegment] = []
    output_source_indices: list[int] = []
    non_overlapping_word_count = 0
    max_non_overlap_distance = Decimal(0)
    turn_sweep = _TurnSweep(validated_turns)

    for segment_index, (segment, words) in enumerate(zip(segments, segment_words)):
        if not words:
            output_segments.append(_copy_segment(segment))
            continue

        assigned_words: list[_AssignedWord] = []
        for assigned_word in words:
            speaker, distance = turn_sweep.assign(assigned_word.word)
            if distance is not None:
                non_overlapping_word_count += 1
                max_non_overlap_distance = max(max_non_overlap_distance, distance)
            assigned_words.append(
                _AssignedWord(
                    source_index=assigned_word.source_index,
                    word=assigned_word.word,
                    speaker=speaker,
                    text_start=assigned_word.text_start,
                    text_end=assigned_word.text_end,
                )
            )

        runs = _speaker_runs(assigned_words)
        if len(runs) == 1:
            output_segments.append(_copy_segment(segment, speaker=runs[0][0].speaker))
            output_source_indices.extend(item.source_index for item in runs[0])
            continue

        boundaries = _split_boundaries(segment, runs, segment_index)
        for run_index, run in enumerate(runs):
            run_words = [item.word for item in run]
            output_segments.append(
                TranscriptSegment(
                    start=centiseconds_to_seconds(boundaries[run_index]),
                    end=centiseconds_to_seconds(boundaries[run_index + 1]),
                    text=_words_to_text(segment.text, run),
                    speaker=run[0].speaker,
                    importance=segment.importance,
                    chaos=segment.chaos,
                    words=[_copy_word(word) for word in run_words],
                    extra_fields=dict(segment.extra_fields),
                )
            )
            output_source_indices.extend(item.source_index for item in run)

    expected_source_indices = list(range(len(source_words)))
    if output_source_indices != expected_source_indices:
        raise DiarizationMergeError(
            "Diarization merger lost, duplicated, or reordered transcript words."
        )
    _verify_word_integrity(source_words, output_segments)
    _validate_output_segments(output_segments)

    return DiarizationMergeResult(
        segments=output_segments,
        speaker_label_map=speaker_label_map,
        non_overlapping_word_count=non_overlapping_word_count,
        max_non_overlap_distance_seconds=float(max_non_overlap_distance),
    )


def _validate_turn(turn: SpeakerTurn, index: int) -> tuple[Decimal, Decimal, str]:
    start, end = _validate_range(turn.start, turn.end, f"Speaker turn {index}")
    if not isinstance(turn.raw_speaker, str) or not turn.raw_speaker.strip():
        raise DiarizationMergeError(f"Speaker turn {index} has an empty raw speaker label.")
    return start, end, turn.raw_speaker


def _validate_segment(segment: TranscriptSegment, index: int) -> tuple[Decimal, Decimal]:
    start, end = _validate_range(segment.start, segment.end, f"Transcript segment {index}")
    try:
        canonical_time_range(segment.start, segment.end)
    except SegmentIdentityError as exc:
        raise DiarizationMergeError(
            f"Transcript segment {index} has no positive range after centisecond quantization."
        ) from exc
    return start, end


def _validate_word(
    word: TranscriptWord,
    segment: TranscriptSegment,
    segment_index: int,
    word_index: int,
) -> tuple[Decimal, Decimal]:
    label = f"Word {word_index} in transcript segment {segment_index}"
    start, end = _validate_range(word.start, word.end, label)
    segment_start = _as_decimal(segment.start, f"Transcript segment {segment_index} start")
    segment_end = _as_decimal(segment.end, f"Transcript segment {segment_index} end")
    if start < segment_start or end > segment_end:
        raise DiarizationMergeError(f"{label} lies outside its parent segment range.")
    return start, end


def _validate_range(start: Any, end: Any, label: str) -> tuple[Decimal, Decimal]:
    start_decimal = _as_decimal(start, f"{label} start")
    end_decimal = _as_decimal(end, f"{label} end")
    if start_decimal < 0:
        raise DiarizationMergeError(f"{label} start must not be negative.")
    if end_decimal <= start_decimal:
        raise DiarizationMergeError(f"{label} end must be after start.")
    return start_decimal, end_decimal


def _as_decimal(value: Any, label: str) -> Decimal:
    try:
        return parse_time_to_decimal(value)
    except SegmentIdentityError as exc:
        raise DiarizationMergeError(f"{label} must be a finite numeric timestamp.") from exc


def _normalize_speaker_labels(
    turns: list[tuple[Decimal, Decimal, str]],
) -> dict[str, str]:
    first_starts: dict[str, Decimal] = {}
    for start, _end, raw_speaker in turns:
        first_starts[raw_speaker] = min(start, first_starts.get(raw_speaker, start))
    ordered_labels = sorted(first_starts, key=lambda label: (first_starts[label], label))
    return {raw_speaker: f"Speaker {index}" for index, raw_speaker in enumerate(ordered_labels)}


def _word_turn_overlap(
    word_start: Decimal,
    word_end: Decimal,
    turn: _ValidatedTurn,
) -> Decimal:
    return max(Decimal(0), min(word_end, turn.end) - max(word_start, turn.start))


def _nearest_technical_key(turn: _ValidatedTurn) -> tuple[Decimal, int, Decimal, str]:
    return (turn.start, turn.speaker_number, turn.end, turn.raw_speaker)


def _speaker_runs(words: list[_AssignedWord]) -> list[list[_AssignedWord]]:
    runs: list[list[_AssignedWord]] = []
    for word in words:
        if not runs or runs[-1][0].speaker != word.speaker:
            runs.append([word])
        else:
            runs[-1].append(word)
    return runs


def _match_word_text_ranges(
    segment: TranscriptSegment,
    segment_index: int,
) -> list[tuple[int, int]]:
    """Map each timestamped word to its next literal occurrence in the parent text."""

    if not isinstance(segment.text, str):
        raise DiarizationMergeError(
            f"Transcript segment {segment_index} text must be a string."
        )

    ranges: list[tuple[int, int]] = []
    cursor = 0
    for word_index, word in enumerate(segment.words):
        token = compact_text(word.text)
        if not token:
            raise DiarizationMergeError(
                f"Word {word_index} in transcript segment {segment_index} has no text to match."
            )

        text_start = segment.text.find(token, cursor)
        if text_start < 0:
            raise DiarizationMergeError(
                f"Word {word_index} in transcript segment {segment_index} cannot be matched "
                "sequentially to its parent transcript text."
            )
        _validate_unmatched_text(
            segment.text[cursor:text_start],
            segment_index,
        )
        text_end = text_start + len(token)
        ranges.append((text_start, text_end))
        cursor = text_end

    _validate_unmatched_text(segment.text[cursor:], segment_index)
    return ranges


def _validate_unmatched_text(text: str, segment_index: int) -> None:
    if any(not character.isspace() for character in text):
        raise DiarizationMergeError(
            f"Transcript segment {segment_index} contains non-whitespace text that is not "
            "covered by timestamped words."
        )


def _split_boundaries(
    segment: TranscriptSegment,
    runs: list[list[_AssignedWord]],
    segment_index: int,
) -> list[int]:
    start_centiseconds, end_centiseconds = canonical_time_range(segment.start, segment.end)
    boundaries = [start_centiseconds]
    for left_run, right_run in zip(runs, runs[1:]):
        left_word = left_run[-1].word
        right_word = right_run[0].word
        left_start, left_end = _validate_range(left_word.start, left_word.end, "Left boundary word")
        right_start, right_end = _validate_range(right_word.start, right_word.end, "Right boundary word")
        if right_start >= left_end:
            boundary = (left_end + right_start) / 2
        else:
            left_midpoint = (left_start + left_end) / 2
            right_midpoint = (right_start + right_end) / 2
            boundary = (left_midpoint + right_midpoint) / 2
        boundaries.append(time_to_centiseconds(boundary))
    boundaries.append(end_centiseconds)

    if any(right <= left for left, right in zip(boundaries, boundaries[1:])):
        raise DiarizationMergeError(
            f"Transcript segment {segment_index} cannot be split into positive, unique "
            "centisecond ranges after quantization."
        )
    return boundaries


def _words_to_text(parent_text: str, words: list[_AssignedWord]) -> str:
    return parent_text[words[0].text_start : words[-1].text_end]


def _copy_word(word: TranscriptWord) -> TranscriptWord:
    return TranscriptWord(start=word.start, end=word.end, text=word.text)


def _copy_segment(segment: TranscriptSegment, *, speaker: str | None = None) -> TranscriptSegment:
    return TranscriptSegment(
        start=segment.start,
        end=segment.end,
        text=segment.text,
        speaker=segment.speaker if speaker is None else speaker,
        importance=segment.importance,
        chaos=segment.chaos,
        words=[_copy_word(word) for word in segment.words],
        extra_fields=dict(segment.extra_fields),
    )


def _verify_word_integrity(
    source_words: list[TranscriptWord],
    output_segments: list[TranscriptSegment],
) -> None:
    expected = [(word.start, word.end, word.text) for word in source_words]
    actual = [
        (word.start, word.end, word.text)
        for segment in output_segments
        for word in segment.words
    ]
    if actual != expected:
        raise DiarizationMergeError(
            "Diarization merger lost, duplicated, reordered, or changed transcript words."
        )


def _validate_output_segments(segments: list[TranscriptSegment]) -> None:
    previous_end: Decimal | None = None
    previous_end_centiseconds: int | None = None
    canonical_ranges: set[tuple[int, int]] = set()
    canonical_ids: set[str] = set()

    for index, segment in enumerate(segments):
        start, end = _validate_range(segment.start, segment.end, f"Merged segment {index}")
        try:
            start_centiseconds, end_centiseconds = canonical_time_range(segment.start, segment.end)
            serialized = segment.to_dict()
        except (SegmentIdentityError, ValueError) as exc:
            raise DiarizationMergeError(
                f"Merged segment {index} does not satisfy the transcript serialization contract."
            ) from exc

        if previous_end is not None and start < previous_end:
            raise DiarizationMergeError(
                f"Merged segment {index} is not chronological or overlaps the previous result."
            )
        if (
            previous_end_centiseconds is not None
            and start_centiseconds < previous_end_centiseconds
        ):
            raise DiarizationMergeError(
                f"Merged segment {index} overlaps the previous result after centisecond quantization."
            )

        canonical_range = (start_centiseconds, end_centiseconds)
        canonical_id = serialized["segment_id"]
        if canonical_range in canonical_ranges or canonical_id in canonical_ids:
            raise DiarizationMergeError(
                f"Merged segment {index} has a duplicate canonical time range or segment ID."
            )

        canonical_ranges.add(canonical_range)
        canonical_ids.add(canonical_id)
        previous_end = end
        previous_end_centiseconds = end_centiseconds
