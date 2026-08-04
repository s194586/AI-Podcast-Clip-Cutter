import copy
from dataclasses import dataclass
from pathlib import Path
import os
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from apps.pipeline.config import PipelineConfig
from diarization import (
    DEFAULT_DIARIZATION_MODEL_ID,
    DEFAULT_DIARIZATION_MODEL_REVISION,
    DiarizationConfig,
    PyannoteDiarizationBackend,
    PyannoteDiarizationError,
    SpeakerTurn,
)
from transcription.base import TranscriptSegment, TranscriptWord


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

    def __call__(self, audio_path, **kwargs):
        self.calls.append((audio_path, kwargs))
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
        self.assertEqual(pipeline.calls, [(audio_path, {})])
        self.assertIs(pipeline.calls[0][0], audio_path)

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


class DiarizationConfigTests(unittest.TestCase):
    def test_supported_modes_and_defaults(self):
        config = DiarizationConfig()
        self.assertEqual(config.mode, "pyannote")
        self.assertEqual(config.model_id, DEFAULT_DIARIZATION_MODEL_ID)
        self.assertEqual(config.model_revision, DEFAULT_DIARIZATION_MODEL_REVISION)
        self.assertEqual(config.device, "cpu")
        self.assertTrue(config.enabled)
        self.assertFalse(DiarizationConfig(mode="off").enabled)

    def test_rejects_unsupported_modes_and_non_cpu_devices(self):
        for mode in ("heuristic_cluster", "auto", ""):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "DIARIZATION_MODE"):
                DiarizationConfig(mode=mode)
        with self.assertRaisesRegex(ValueError, "CPU-only"):
            DiarizationConfig(device="cuda")

    def test_speaker_constraints_are_positive_and_consistent(self):
        self.assertEqual(DiarizationConfig(num_speakers="2").speaker_constraints(), {"num_speakers": 2})
        self.assertEqual(
            DiarizationConfig(min_speakers=2, max_speakers=4).speaker_constraints(),
            {"min_speakers": 2, "max_speakers": 4},
        )
        self.assertEqual(DiarizationConfig().speaker_constraints(), {})
        for value in (0, -1, "abc", True, 1.5):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "positive integer"):
                DiarizationConfig(num_speakers=value)
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            DiarizationConfig(num_speakers=2, max_speakers=3)
        with self.assertRaisesRegex(ValueError, "less than or equal"):
            DiarizationConfig(min_speakers=4, max_speakers=2)

    def test_pipeline_config_reads_non_secret_environment(self):
        environment = {
            "DIARIZATION_MODE": "off",
            "DIARIZATION_MODEL_ID": "example/model",
            "DIARIZATION_MODEL_REVISION": "fixed-revision",
            "DIARIZATION_DEVICE": "cpu",
            "DIARIZATION_NUM_SPEAKERS": "",
            "DIARIZATION_MIN_SPEAKERS": "2",
            "DIARIZATION_MAX_SPEAKERS": "5",
            "HF_TOKEN": "must-not-enter-config",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = PipelineConfig(ai_mode="local_only", subtitle_checker_mode="local_only")

        self.assertEqual(config.diarization_mode, "off")
        self.assertEqual(config.diarization_model_id, "example/model")
        self.assertEqual(config.diarization_model_revision, "fixed-revision")
        self.assertEqual(config.diarization_min_speakers, 2)
        self.assertEqual(config.diarization_max_speakers, 5)
        self.assertNotIn("must-not-enter-config", repr(config))
        self.assertNotIn("HF_TOKEN", config.__dict__)


class PyannoteProductionBackendTests(unittest.TestCase):
    @staticmethod
    def _segments():
        return [
            TranscriptSegment(
                start=0.0,
                end=2.0,
                text="Hello there",
                importance=5,
                chaos=True,
                words=[
                    TranscriptWord(0.0, 0.8, "Hello"),
                    TranscriptWord(1.0, 1.8, "there"),
                ],
            )
        ]

    @staticmethod
    def _output(items):
        return FakeOutput(FakeAnnotation(items))

    def test_loader_is_lazy_and_uses_pinned_model_revision_and_token(self):
        calls = []
        pipeline = FakePipeline(self._output([]))

        def factory(model_id, revision, token):
            calls.append((model_id, revision, token))
            return pipeline

        config = DiarizationConfig()
        backend = PyannoteDiarizationBackend(
            config,
            pipeline_factory=factory,
            environment={"HF_TOKEN": "test-secret"},
        )
        self.assertEqual(calls, [])

        backend.speaker_turns(Path("audio.wav"))

        self.assertEqual(calls, [(config.model_id, config.model_revision, "test-secret")])
        self.assertNotIn("pyannote.audio", sys.modules)

    def test_audio_is_preloaded_as_mono_float32_at_16khz(self):
        decoded = SimpleNamespace(stdout=struct.pack("<3f", 0.0, 0.5, -0.5))

        with patch(
            "diarization.pyannote_backend.subprocess.run",
            return_value=decoded,
        ) as run:
            model_input = PyannoteDiarizationBackend._preload_audio_file(Path("audio.mp3"))

        self.assertEqual(model_input["sample_rate"], 16000)
        self.assertEqual(tuple(model_input["waveform"].shape), (1, 3))
        self.assertEqual(str(model_input["waveform"].dtype), "torch.float32")
        command = run.call_args.args[0]
        self.assertIn("f32le", command)
        self.assertEqual(command[command.index("-ac") + 1], "1")
        self.assertEqual(command[command.index("-ar") + 1], "16000")
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertTrue(run.call_args.kwargs["check"])

    def test_missing_token_fails_before_factory_or_import(self):
        called = []
        backend = PyannoteDiarizationBackend(
            DiarizationConfig(),
            pipeline_factory=lambda *_args: called.append(True),
            environment={},
        )
        with self.assertRaisesRegex(PyannoteDiarizationError, "HF_TOKEN"):
            backend.speaker_turns("audio.wav")
        self.assertEqual(called, [])

    def test_external_errors_are_sanitized(self):
        secret = "hf_super_secret_value"

        def failing_factory(_model_id, _revision, token):
            raise RuntimeError(f"access rejected for {token}")

        backend = PyannoteDiarizationBackend(
            DiarizationConfig(),
            pipeline_factory=failing_factory,
            environment={"HF_TOKEN": secret},
        )
        with self.assertRaises(PyannoteDiarizationError) as raised:
            backend.speaker_turns("audio.wav")
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(secret, repr(raised.exception))

    def test_constraints_are_forwarded_and_model_is_called_once(self):
        pipeline = FakePipeline(self._output([(FakeSegment(0.0, 2.0), "track", "SPEAKER_00")]))
        backend = PyannoteDiarizationBackend(
            DiarizationConfig(min_speakers=1, max_speakers=3),
            pipeline=pipeline,
        )

        backend.assign_speakers(Path("audio.wav"), self._segments())

        self.assertEqual(pipeline.calls, [(Path("audio.wav"), {"min_speakers": 1, "max_speakers": 3})])

    def test_assign_speakers_uses_merger_and_preserves_segment_contract(self):
        pipeline = FakePipeline(
            self._output(
                [
                    (FakeSegment(0.0, 0.9), "track-0", "raw-b"),
                    (FakeSegment(0.9, 2.0), "track-1", "raw-a"),
                ]
            )
        )
        segments = self._segments()
        backend = PyannoteDiarizationBackend(DiarizationConfig(), pipeline=pipeline)

        result = backend.assign_speakers(Path("audio.wav"), segments)

        self.assertEqual([segment.text for segment in segments], ["Hello", "there"])
        self.assertEqual([segment.speaker for segment in segments], ["Speaker 0", "Speaker 1"])
        self.assertEqual([segment.importance for segment in segments], [5, 5])
        self.assertEqual([segment.chaos for segment in segments], [True, True])
        self.assertEqual(result.status, "applied")
        self.assertFalse(result.used_fallback)
        self.assertEqual(result.extra_metadata["speaker_label_map"], {"raw-b": "Speaker 0", "raw-a": "Speaker 1"})
        self.assertEqual(result.extra_metadata["diarization_turn_count"], 2)

    def test_missing_word_timestamps_fails_before_model_call(self):
        pipeline = FakePipeline(self._output([]))
        backend = PyannoteDiarizationBackend(DiarizationConfig(), pipeline=pipeline)
        segments = [TranscriptSegment(0.0, 1.0, "spoken text", words=[])]

        with self.assertRaisesRegex(PyannoteDiarizationError, "word timestamps"):
            backend.assign_speakers(Path("audio.wav"), segments)

        self.assertEqual(pipeline.calls, [])

    def test_empty_asr_and_empty_diarization_is_no_speech(self):
        pipeline = FakePipeline(self._output([]))
        backend = PyannoteDiarizationBackend(DiarizationConfig(), pipeline=pipeline)
        segments = []

        result = backend.assign_speakers(Path("audio.wav"), segments)

        self.assertEqual(result.status, "no_speech")
        self.assertEqual(result.speaker_count, 0)
        self.assertEqual(result.extra_metadata["diarization_turn_count"], 0)
        self.assertEqual(pipeline.calls, [])

    def test_nonempty_asr_without_turns_is_an_error_without_fallback(self):
        pipeline = FakePipeline(self._output([]))
        backend = PyannoteDiarizationBackend(DiarizationConfig(), pipeline=pipeline)
        segments = self._segments()

        with self.assertRaisesRegex(PyannoteDiarizationError, "speaker turn list is empty"):
            backend.assign_speakers(Path("audio.wav"), segments)

        self.assertEqual(segments[0].speaker, "")


if __name__ == "__main__":
    unittest.main()
