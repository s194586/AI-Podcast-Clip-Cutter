from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from apps.pipeline.entrypoint import build_parser as build_pipeline_parser
from apps.pipeline.stages.transcribe import _can_reuse_transcript
from diarization import DiarizationConfig, DiarizationResult, PyannoteDiarizationBackend
from manager import parse_args as parse_manager_args
from transcribe import parse_args, transcribe_file
from transcription.base import TranscriptSegment, TranscriptWord, TranscriptionResult
from transcription.segment_identity import (
    SEGMENT_ID_SCHEME,
    SEGMENT_ID_VERSION,
    TRANSCRIPT_SCHEMA_VERSION,
    canonical_segment_id,
)


class FakeTranscriptionBackend:
    def __init__(self, segments):
        self.segments = segments
        self.released = False
        self.calls = []

    def transcribe(self, audio_path):
        self.calls.append(audio_path)
        return TranscriptionResult(
            backend="faster_whisper",
            model="small",
            audio_path=audio_path,
            language="en",
            duration_seconds=2.0,
            transcription_seconds=0.1,
            segments=self.segments,
            device="cpu",
            compute_type="int8",
            extra_metadata={"word_timestamps": True},
        )

    def release_resources(self):
        self.released = True


class FakeDiarizationBackend:
    def __init__(self, transcription_backend, *, fail=False):
        self.transcription_backend = transcription_backend
        self.fail = fail
        self.calls = []

    def assign_speakers(self, audio_path, segments):
        if not self.transcription_backend.released:
            raise AssertionError("Whisper resources must be released before diarization.")
        self.calls.append((audio_path, segments))
        if self.fail:
            raise RuntimeError("controlled diarization failure")
        return DiarizationResult(
            backend="pyannote",
            enabled=True,
            status="applied" if segments else "no_speech",
            speaker_count=1 if segments else 0,
            diarization_seconds=0.2,
            used_fallback=False,
            extra_metadata={
                "diarization_turn_count": 1 if segments else 0,
                "speaker_label_map": {"raw": "Speaker 0"} if segments else {},
                "non_overlapping_word_count": 0,
                "max_non_overlap_distance_seconds": 0.0,
            },
        )


def transcript_segments():
    return [
        TranscriptSegment(
            0.0,
            2.0,
            "hello world",
            speaker="Speaker 0",
            importance=4,
            chaos=False,
            words=[TranscriptWord(0.0, 0.8, "hello"), TranscriptWord(1.0, 1.8, "world")],
        )
    ]


class TranscriptionDiarizationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.audio = self.root / "audio.wav"
        self.audio.write_bytes(b"test-audio-placeholder")
        self.output = self.root / "final_transcript.json"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_whisper_is_released_before_diarization_and_metadata_is_complete(self):
        transcription = FakeTranscriptionBackend(transcript_segments())
        diarization = FakeDiarizationBackend(transcription)

        payload = transcribe_file(
            self.audio,
            self.output,
            _transcription_backend=transcription,
            _diarization_backend=diarization,
        )

        self.assertTrue(transcription.released)
        self.assertEqual(len(diarization.calls), 1)
        metadata = payload["metadata"]
        self.assertEqual(metadata["diarization_mode"], "pyannote")
        self.assertEqual(metadata["diarization_backend"], "pyannote")
        self.assertEqual(metadata["diarization_status"], "applied")
        self.assertEqual(metadata["diarization_device"], "cpu")
        self.assertTrue(metadata["diarization_exclusive"])
        self.assertEqual(metadata["speaker_count"], 1)
        self.assertEqual(metadata["diarization_turn_count"], 1)
        self.assertEqual(metadata["speaker_label_map"], {"raw": "Speaker 0"})
        self.assertFalse(metadata["diarization_used_fallback"])
        self.assertIsNone(metadata["diarization_num_speakers"])
        self.assertIsNone(metadata["diarization_min_speakers"])
        self.assertIsNone(metadata["diarization_max_speakers"])

    def test_diarization_constraints_are_normalized_in_metadata(self):
        transcription = FakeTranscriptionBackend(transcript_segments())
        payload = transcribe_file(
            self.audio,
            self.output,
            diarization_min_speakers="2",
            diarization_max_speakers="4",
            _transcription_backend=transcription,
            _diarization_backend=FakeDiarizationBackend(transcription),
        )

        self.assertEqual(payload["metadata"]["diarization_num_speakers"], None)
        self.assertEqual(payload["metadata"]["diarization_min_speakers"], 2)
        self.assertEqual(payload["metadata"]["diarization_max_speakers"], 4)

    def test_final_json_uses_atomic_replace(self):
        transcription = FakeTranscriptionBackend(transcript_segments())
        diarization = FakeDiarizationBackend(transcription)

        with patch("transcribe.os.replace", wraps=os.replace) as replace:
            transcribe_file(
                self.audio,
                self.output,
                _transcription_backend=transcription,
                _diarization_backend=diarization,
            )

        replace.assert_called_once()
        temporary_path, final_path = replace.call_args.args
        self.assertEqual(Path(temporary_path).parent, self.output.parent)
        self.assertEqual(Path(final_path), self.output)
        self.assertTrue(self.output.exists())

    def test_diarization_failure_preserves_existing_transcript(self):
        original = b'{"existing": true}\n'
        self.output.write_bytes(original)
        transcription = FakeTranscriptionBackend(transcript_segments())
        diarization = FakeDiarizationBackend(transcription, fail=True)

        with self.assertRaisesRegex(RuntimeError, "controlled diarization failure"):
            transcribe_file(
                self.audio,
                self.output,
                _transcription_backend=transcription,
                _diarization_backend=diarization,
            )

        self.assertEqual(self.output.read_bytes(), original)

    def test_off_mode_does_not_emit_a_placeholder_speaker(self):
        segments = [
            TranscriptSegment(
                0.0,
                1.0,
                "hello",
                words=[TranscriptWord(0.0, 1.0, "hello")],
            )
        ]
        transcription = FakeTranscriptionBackend(segments)

        payload = transcribe_file(
            self.audio,
            self.output,
            diarization_mode="off",
            _transcription_backend=transcription,
        )

        self.assertNotIn("speaker", payload["segments"][0])
        self.assertEqual(payload["metadata"]["diarization_backend"], "off")
        self.assertFalse(payload["metadata"]["diarization_exclusive"])
        self.assertFalse(payload["metadata"]["diarization_used_fallback"])

    def test_retired_mode_is_rejected_by_cli_and_compatibility_argument(self):
        with self.assertRaises(SystemExit):
            parse_args(["--file", str(self.audio), "--diarization-mode", "heuristic_cluster"])
        with self.assertRaisesRegex(ValueError, "retired"):
            transcribe_file(
                self.audio,
                self.output,
                diarization_backend="heuristic_cluster",
                _transcription_backend=FakeTranscriptionBackend([]),
            )

    def test_cli_normalizes_empty_and_nonempty_diarization_constraint_environment(self):
        environment = {
            "DIARIZATION_NUM_SPEAKERS": "   ",
            "DIARIZATION_MIN_SPEAKERS": "",
            "DIARIZATION_MAX_SPEAKERS": "\t",
        }
        with patch.dict(os.environ, environment, clear=True):
            args = parse_args(["--file", str(self.audio)])
        self.assertIsNone(args.diarization_num_speakers)
        self.assertIsNone(args.diarization_min_speakers)
        self.assertIsNone(args.diarization_max_speakers)

        environment.update(
            {
                "DIARIZATION_NUM_SPEAKERS": "2",
                "DIARIZATION_MIN_SPEAKERS": "3",
                "DIARIZATION_MAX_SPEAKERS": "5",
            }
        )
        with patch.dict(os.environ, environment, clear=True):
            args = parse_args(["--file", str(self.audio)])
        self.assertEqual(args.diarization_num_speakers, 2)
        self.assertEqual(args.diarization_min_speakers, 3)
        self.assertEqual(args.diarization_max_speakers, 5)

    def test_pipeline_clis_normalize_whitespace_diarization_constraint_environment(self):
        environment = {
            "DIARIZATION_NUM_SPEAKERS": "   ",
            "DIARIZATION_MIN_SPEAKERS": "\t",
            "DIARIZATION_MAX_SPEAKERS": "\r\n",
        }
        with patch.dict(os.environ, environment, clear=True):
            manager_args = parse_manager_args([])
            pipeline_args = build_pipeline_parser().parse_args(
                [
                    "--project-id",
                    "1",
                    "--source-url",
                    "https://example.com/podcast",
                    "--workspace-dir",
                    str(self.root),
                ]
            )

        for args in (manager_args, pipeline_args):
            self.assertIsNone(args.diarization_num_speakers)
            self.assertIsNone(args.diarization_min_speakers)
            self.assertIsNone(args.diarization_max_speakers)

    def test_transcription_failure_releases_resources_without_starting_diarization(self):
        transcription = FakeTranscriptionBackend(transcript_segments())
        transcription.transcribe = lambda _audio_path: (_ for _ in ()).throw(RuntimeError("controlled failure"))
        diarization = FakeDiarizationBackend(transcription)

        with self.assertRaisesRegex(RuntimeError, "controlled failure"):
            transcribe_file(
                self.audio,
                self.output,
                _transcription_backend=transcription,
                _diarization_backend=diarization,
            )

        self.assertTrue(transcription.released)
        self.assertEqual(diarization.calls, [])


class TranscriptResumeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "final_transcript.json"
        self._set_context()

    def _set_context(self, mode="pyannote"):
        self.config = DiarizationConfig(mode=mode, min_speakers=2, max_speakers=4)
        self.context = SimpleNamespace(
            transcript_file=self.path,
            config=SimpleNamespace(
                diarization_mode=self.config.mode,
                diarization_model_id=self.config.model_id,
                diarization_model_revision=self.config.model_revision,
                diarization_device=self.config.device,
                diarization_num_speakers=self.config.num_speakers,
                diarization_min_speakers=self.config.min_speakers,
                diarization_max_speakers=self.config.max_speakers,
            ),
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _matching_metadata(self, *, status=None):
        if status is None:
            status = "applied" if self.config.mode == "pyannote" else "disabled"
        has_speakers = self.config.mode == "pyannote" and status == "applied"
        return {
            "diarization_mode": self.config.mode,
            "diarization_backend": self.config.mode,
            "diarization_model_id": self.config.model_id,
            "diarization_model_revision": self.config.model_revision,
            "diarization_device": self.config.device,
            "diarization_num_speakers": self.config.num_speakers,
            "diarization_min_speakers": self.config.min_speakers,
            "diarization_max_speakers": self.config.max_speakers,
            "diarization_used_fallback": False,
            "diarization_exclusive": self.config.mode == "pyannote",
            "diarization_status": status,
            "speaker_count": 1 if has_speakers else 0,
            "diarization_turn_count": 1 if has_speakers else 0,
            "speaker_label_map": {"raw": "Speaker 0"} if has_speakers else {},
            "non_overlapping_word_count": 0,
            "max_non_overlap_distance_seconds": 0.0,
        }

    def _write(self, metadata, *, segments=None, include_header=True):
        payload = {"segments": [] if segments is None else segments, "metadata": metadata}
        if include_header:
            payload.update(
                {
                    "transcript_schema_version": TRANSCRIPT_SCHEMA_VERSION,
                    "segment_id_scheme": SEGMENT_ID_SCHEME,
                    "segment_id_version": SEGMENT_ID_VERSION,
                }
            )
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def _segment(*, speaker=None):
        segment = {
            "segment_id": canonical_segment_id(0.0, 1.0),
            "start": "00:00.00",
            "end": "00:01.00",
            "text": "hello",
            "importance": 3,
            "chaos": False,
            "words": [{"start": "00:00.00", "end": "00:01.00", "text": "hello"}],
        }
        if speaker is not None:
            segment["speaker"] = speaker
        return segment

    def test_matching_pyannote_metadata_can_be_reused(self):
        self._write(self._matching_metadata(), segments=[self._segment(speaker="Speaker 0")])
        self.assertTrue(_can_reuse_transcript(self.context))

    def test_pyannote_no_speech_requires_zero_speaker_metadata(self):
        for field, value in (
            ("speaker_count", 1),
            ("speaker_label_map", {"raw": "Speaker 0"}),
            ("diarization_turn_count", 1),
        ):
            with self.subTest(field=field):
                metadata = self._matching_metadata(status="no_speech")
                metadata[field] = value
                self._write(metadata, segments=[])
                self.assertFalse(_can_reuse_transcript(self.context))

    def test_pyannote_applied_requires_actual_speaker_data(self):
        metadata = self._matching_metadata(status="applied")
        metadata.update(
            speaker_count=0,
            speaker_label_map={},
            diarization_turn_count=0,
        )
        self._write(metadata, segments=[self._segment()])
        self.assertFalse(_can_reuse_transcript(self.context))

    def test_complete_pyannote_applied_contract_is_reusable(self):
        self._write(
            self._matching_metadata(status="applied"),
            segments=[self._segment(speaker="Speaker 0")],
        )
        self.assertTrue(_can_reuse_transcript(self.context))

    def test_complete_pyannote_no_speech_contract_is_reusable(self):
        self._write(self._matching_metadata(status="no_speech"), segments=[])
        self.assertTrue(_can_reuse_transcript(self.context))

    def test_complete_off_contracts_are_reusable(self):
        self._set_context("off")
        for status, segments in (
            ("disabled", [self._segment()]),
            ("no_speech", []),
        ):
            with self.subTest(status=status):
                self._write(self._matching_metadata(status=status), segments=segments)
                self.assertTrue(_can_reuse_transcript(self.context))

    def test_heuristic_or_mismatched_transcript_is_not_reused_as_pyannote(self):
        for key, value in (("diarization_mode", "off"), ("diarization_model_revision", "different")):
            with self.subTest(key=key):
                metadata = self._matching_metadata()
                metadata[key] = value
                self._write(metadata, segments=[{}])
                self.assertFalse(_can_reuse_transcript(self.context))

    def test_changed_speaker_constraints_invalidate_reuse(self):
        for key, value in (
            ("diarization_num_speakers", 1),
            ("diarization_min_speakers", 3),
            ("diarization_max_speakers", 5),
        ):
            with self.subTest(key=key):
                metadata = self._matching_metadata()
                metadata[key] = value
                self._write(metadata, segments=[{}])
                self.assertFalse(_can_reuse_transcript(self.context))

    def test_missing_constraint_metadata_invalidate_reuse(self):
        for key in (
            "diarization_num_speakers",
            "diarization_min_speakers",
            "diarization_max_speakers",
        ):
            with self.subTest(key=key):
                metadata = self._matching_metadata()
                del metadata[key]
                self._write(metadata, segments=[{}])
                self.assertFalse(_can_reuse_transcript(self.context))

    def test_missing_schema_fields_or_segments_invalidate_reuse(self):
        for missing_key in (
            "transcript_schema_version",
            "segment_id_scheme",
            "segment_id_version",
            "segments",
        ):
            with self.subTest(missing_key=missing_key):
                payload = {
                    "transcript_schema_version": TRANSCRIPT_SCHEMA_VERSION,
                    "segment_id_scheme": SEGMENT_ID_SCHEME,
                    "segment_id_version": SEGMENT_ID_VERSION,
                    "segments": [{}],
                    "metadata": self._matching_metadata(),
                }
                del payload[missing_key]
                self.path.write_text(json.dumps(payload), encoding="utf-8")
                self.assertFalse(_can_reuse_transcript(self.context))

    def test_fallback_wrong_status_or_exclusive_invalidate_reuse(self):
        for key, value in (
            ("diarization_used_fallback", True),
            ("diarization_status", "disabled"),
            ("diarization_exclusive", False),
        ):
            with self.subTest(key=key):
                metadata = self._matching_metadata()
                metadata[key] = value
                self._write(metadata, segments=[{}])
                self.assertFalse(_can_reuse_transcript(self.context))

    def test_invalid_utf8_transcript_is_not_reused(self):
        self.path.write_bytes(b"\xff")
        self.assertFalse(_can_reuse_transcript(self.context))

    def test_valid_status_segment_combinations_can_be_reused(self):
        for mode, status, segments in (
            ("pyannote", "applied", [self._segment(speaker="Speaker 0")]),
            ("pyannote", "no_speech", []),
            ("off", "disabled", [self._segment()]),
            ("off", "no_speech", []),
        ):
            with self.subTest(mode=mode, status=status, segments=segments):
                self._set_context(mode)
                self._write(self._matching_metadata(status=status), segments=segments)
                self.assertTrue(_can_reuse_transcript(self.context))

    def test_inverse_status_segment_combinations_are_not_reused(self):
        for mode, status, segments in (
            ("pyannote", "applied", []),
            ("pyannote", "no_speech", [{}]),
            ("off", "disabled", []),
            ("off", "no_speech", [{}]),
        ):
            with self.subTest(mode=mode, status=status, segments=segments):
                self._set_context(mode)
                self._write(self._matching_metadata(status=status), segments=segments)
                self.assertFalse(_can_reuse_transcript(self.context))

    def test_missing_speaker_metadata_invalidates_reuse(self):
        for key in (
            "speaker_count",
            "diarization_turn_count",
            "speaker_label_map",
            "non_overlapping_word_count",
            "max_non_overlap_distance_seconds",
        ):
            with self.subTest(key=key):
                metadata = self._matching_metadata()
                del metadata[key]
                self._write(metadata, segments=[{}])
                self.assertFalse(_can_reuse_transcript(self.context))

    def test_inconsistent_speaker_count_and_label_map_invalidates_reuse(self):
        metadata = self._matching_metadata()
        metadata["speaker_count"] = 2
        self._write(metadata, segments=[{}])
        self.assertFalse(_can_reuse_transcript(self.context))

    def test_boolean_counters_invalidate_reuse(self):
        for field in (
            "speaker_count",
            "diarization_turn_count",
            "non_overlapping_word_count",
        ):
            with self.subTest(field=field):
                metadata = self._matching_metadata()
                metadata[field] = True
                self._write(metadata, segments=[self._segment(speaker="Speaker 0")])
                self.assertFalse(_can_reuse_transcript(self.context))

    def test_applied_speaker_labels_and_non_overlap_metrics_must_be_consistent(self):
        for field, value, segment in (
            (
                "speaker_label_map",
                {"raw": "Speaker 1"},
                self._segment(speaker="Speaker 0"),
            ),
            (
                "non_overlapping_word_count",
                2,
                self._segment(speaker="Speaker 0"),
            ),
            (
                "max_non_overlap_distance_seconds",
                1.0,
                self._segment(speaker="Speaker 0"),
            ),
        ):
            with self.subTest(field=field):
                metadata = self._matching_metadata()
                metadata[field] = value
                self._write(metadata, segments=[segment])
                self.assertFalse(_can_reuse_transcript(self.context))


if __name__ == "__main__":
    unittest.main()
