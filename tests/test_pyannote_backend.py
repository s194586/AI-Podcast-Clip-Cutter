import copy
from dataclasses import dataclass
from pathlib import Path
import unittest

from diarization import PyannoteDiarizationBackend, PyannoteDiarizationError, SpeakerTurn


@dataclass
class FakeSegment:
    start: float
    end: float


@dataclass
class FakeAnnotation:
    items: list

    def itertracks(self, *, yield_label):
        if yield_label is not True:
            raise AssertionError("Pyannote annotation must be read with yield_label=True.")
        yield from self.items


@dataclass
class FakeOutput:
    exclusive_speaker_diarization: FakeAnnotation


class MissingExclusiveOutput:
    pass


class FakePipeline:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def __call__(self, audio_path):
        self.calls.append(audio_path)
        return self.output


class PyannoteDiarizationBackendTests(unittest.TestCase):
    def _backend(self, items):
        annotation = FakeAnnotation(items)
        pipeline = FakePipeline(FakeOutput(annotation))
        return PyannoteDiarizationBackend(pipeline), pipeline, annotation

    def test_calls_pipeline_once_with_the_exact_audio_path(self):
        backend, pipeline, _annotation = self._backend([])
        audio_path = Path("input/podcast.wav")

        turns = backend.speaker_turns(audio_path)

        self.assertEqual(turns, [])
        self.assertEqual(pipeline.calls, [audio_path])
        self.assertIs(pipeline.calls[0], audio_path)

    def test_converts_multiple_turns_without_merging_or_relabeling(self):
        items = [
            (FakeSegment(0.0, 1.25), "track-0", "SPEAKER_01"),
            (FakeSegment(1.25, 2.5), "track-1", "SPEAKER_00"),
            (FakeSegment(2.5, 4.0), "track-2", "SPEAKER_01"),
        ]
        backend, _pipeline, annotation = self._backend(items)

        turns = backend.speaker_turns("input/podcast.wav")

        self.assertEqual(
            turns,
            [
                SpeakerTurn(0.0, 1.25, "SPEAKER_01"),
                SpeakerTurn(1.25, 2.5, "SPEAKER_00"),
                SpeakerTurn(2.5, 4.0, "SPEAKER_01"),
            ],
        )
        self.assertEqual(annotation.items, items)

    def test_preserves_exact_timestamp_values(self):
        start = 0.123456789
        end = 1.987654321
        backend, _pipeline, _annotation = self._backend(
            [(FakeSegment(start, end), "track-0", "raw-label")]
        )

        turn = backend.speaker_turns("input/podcast.wav")[0]

        self.assertIs(turn.start, start)
        self.assertIs(turn.end, end)

    def test_empty_annotation_returns_an_empty_list(self):
        backend, _pipeline, _annotation = self._backend([])

        self.assertEqual(backend.speaker_turns("input/podcast.wav"), [])

    def test_missing_exclusive_diarization_raises_a_domain_error(self):
        backend = PyannoteDiarizationBackend(FakePipeline(MissingExclusiveOutput()))

        with self.assertRaisesRegex(
            PyannoteDiarizationError,
            "missing exclusive_speaker_diarization",
        ):
            backend.speaker_turns("input/podcast.wav")

    def test_invalid_range_raises_a_domain_error(self):
        backend, _pipeline, _annotation = self._backend(
            [(FakeSegment(1.0, 1.0), "track-0", "SPEAKER_00")]
        )

        with self.assertRaisesRegex(PyannoteDiarizationError, "end must be after start"):
            backend.speaker_turns("input/podcast.wav")

    def test_empty_speaker_label_raises_a_domain_error(self):
        backend, _pipeline, _annotation = self._backend(
            [(FakeSegment(0.0, 1.0), "track-0", "")]
        )

        with self.assertRaisesRegex(PyannoteDiarizationError, "empty speaker label"):
            backend.speaker_turns("input/podcast.wav")

    def test_does_not_mutate_the_pyannote_output(self):
        items = [
            (FakeSegment(0.0, 1.0), "track-0", "SPEAKER_00"),
            (FakeSegment(1.0, 2.0), "track-1", "SPEAKER_01"),
        ]
        backend, pipeline, annotation = self._backend(items)
        original_output = copy.deepcopy(pipeline.output)

        backend.speaker_turns("input/podcast.wav")

        self.assertEqual(pipeline.output, original_output)
        self.assertEqual(annotation.items, original_output.exclusive_speaker_diarization.items)


if __name__ == "__main__":
    unittest.main()
