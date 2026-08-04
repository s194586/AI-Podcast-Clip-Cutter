from __future__ import annotations

import json
import math

from transcribe import transcribe_file
from transcription.segment_identity import (
    SEGMENT_ID_SCHEME,
    SEGMENT_ID_VERSION,
    TRANSCRIPT_SCHEMA_VERSION,
    SegmentIdentityError,
    canonical_segment_id,
)

from ..context import PipelineContext
from ..exceptions import TranscriptionStageError
from ..results import PipelineStageResult
from .common import MediaLocator


class TranscribeAudioStage:
    stage = "transcribing"

    def run(self, context: PipelineContext) -> PipelineStageResult:
        if _can_reuse_transcript(context):
            return PipelineStageResult(
                stage=self.stage,
                success=True,
                message="Existing transcript reused.",
                produced_artifacts=(context.safe_artifact(context.transcript_file),),
                metadata={"reused": True},
            )

        audio_path = MediaLocator(context).latest_audio()
        if audio_path is None:
            raise TranscriptionStageError("No usable audio stream was found in the project workspace.")
        try:
            payload = transcribe_file(
                audio_path,
                context.transcript_file,
                backend=context.config.transcription_backend,
                whisper_model=context.config.whisper_model,
                device=context.config.transcription_device,
                compute_type=context.config.transcription_compute_type,
                diarization_mode=context.config.diarization_mode,
                diarization_model_id=context.config.diarization_model_id,
                diarization_model_revision=context.config.diarization_model_revision,
                diarization_device=context.config.diarization_device,
                diarization_num_speakers=context.config.diarization_num_speakers,
                diarization_min_speakers=context.config.diarization_min_speakers,
                diarization_max_speakers=context.config.diarization_max_speakers,
            )
        except Exception as exc:
            raise TranscriptionStageError(f"Audio transcription failed: {exc}") from exc
        segment_count = len(payload.get("segments") or []) if isinstance(payload, dict) else 0
        return PipelineStageResult(
            stage=self.stage,
            success=True,
            message="Audio transcription completed.",
            produced_artifacts=(context.safe_artifact(context.transcript_file),),
            metadata={"reused": False, "segment_count": segment_count},
        )


def _can_reuse_transcript(context: PipelineContext) -> bool:
    path = context.transcript_file
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        return False
    if (
        payload.get("transcript_schema_version") != TRANSCRIPT_SCHEMA_VERSION
        or payload.get("segment_id_scheme") != SEGMENT_ID_SCHEME
        or payload.get("segment_id_version") != SEGMENT_ID_VERSION
    ):
        return False
    metadata = payload.get("metadata") if isinstance(payload, dict) else None
    if not isinstance(metadata, dict):
        return False
    requested = context.config
    expected_metadata = {
        "diarization_mode": requested.diarization_mode,
        "diarization_backend": requested.diarization_mode,
        "diarization_model_id": requested.diarization_model_id,
        "diarization_model_revision": requested.diarization_model_revision,
        "diarization_device": requested.diarization_device,
        "diarization_num_speakers": requested.diarization_num_speakers,
        "diarization_min_speakers": requested.diarization_min_speakers,
        "diarization_max_speakers": requested.diarization_max_speakers,
    }
    if any(key not in metadata or metadata[key] != value for key, value in expected_metadata.items()):
        return False
    if metadata.get("diarization_used_fallback") is not False:
        return False
    counter_fields = (
        "speaker_count",
        "diarization_turn_count",
        "non_overlapping_word_count",
    )
    if any(
        isinstance(metadata.get(field), bool)
        or not isinstance(metadata.get(field), int)
        or metadata[field] < 0
        for field in counter_fields
    ):
        return False
    speaker_label_map = metadata.get("speaker_label_map")
    if not isinstance(speaker_label_map, dict):
        return False
    if metadata["speaker_count"] != len(speaker_label_map):
        return False
    max_non_overlap_distance = metadata.get("max_non_overlap_distance_seconds")
    if (
        isinstance(max_non_overlap_distance, bool)
        or not isinstance(max_non_overlap_distance, (int, float))
        or max_non_overlap_distance < 0
    ):
        return False
    try:
        if not math.isfinite(max_non_overlap_distance):
            return False
    except OverflowError:
        return False

    segments = payload["segments"]
    segment_speakers: set[str] = set()
    segment_ids: set[str] = set()
    labeled_segment_count = 0
    word_count = 0
    for segment in segments:
        if not isinstance(segment, dict):
            return False
        try:
            expected_segment_id = canonical_segment_id(segment.get("start"), segment.get("end"))
        except (SegmentIdentityError, TypeError, ValueError):
            return False
        segment_id = segment.get("segment_id")
        if segment_id != expected_segment_id or segment_id in segment_ids:
            return False
        segment_ids.add(segment_id)
        speaker = segment.get("speaker")
        if speaker is not None:
            if not isinstance(speaker, str) or not speaker.strip():
                return False
            segment_speakers.add(speaker)
            labeled_segment_count += 1
        words = segment.get("words", [])
        if not isinstance(words, list):
            return False
        word_count += len(words)

    if metadata["non_overlapping_word_count"] > word_count:
        return False
    if metadata["non_overlapping_word_count"] == 0 and max_non_overlap_distance != 0:
        return False

    normalized_labels = set()
    for raw_label, normalized_label in speaker_label_map.items():
        if (
            not isinstance(raw_label, str)
            or not raw_label.strip()
            or not isinstance(normalized_label, str)
            or not normalized_label.strip()
            or normalized_label in normalized_labels
        ):
            return False
        normalized_labels.add(normalized_label)

    has_segments = bool(segments)
    if requested.diarization_mode == "pyannote":
        expected_status = "applied" if has_segments else "no_speech"
        if (
            metadata.get("diarization_exclusive") is not True
            or metadata.get("diarization_status") != expected_status
        ):
            return False
        if not has_segments:
            return (
                metadata["speaker_count"] == 0
                and not speaker_label_map
                and metadata["diarization_turn_count"] == 0
                and metadata["non_overlapping_word_count"] == 0
                and max_non_overlap_distance == 0
            )
        return (
            metadata["speaker_count"] > 0
            and metadata["diarization_turn_count"] >= metadata["speaker_count"]
            and labeled_segment_count == len(segments)
            and bool(segment_speakers)
            and segment_speakers.issubset(normalized_labels)
        )

    expected_status = "disabled" if has_segments else "no_speech"
    return (
        metadata.get("diarization_exclusive") is False
        and metadata.get("diarization_status") == expected_status
        and metadata["speaker_count"] == 0
        and not speaker_label_map
        and metadata["diarization_turn_count"] == 0
        and metadata["non_overlapping_word_count"] == 0
        and max_non_overlap_distance == 0
        and not segment_speakers
    )
