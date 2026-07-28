from __future__ import annotations

from typing import Any

from transcription.segment_identity import (
    SEGMENT_ID_SCHEME,
    SEGMENT_ID_VERSION,
    TRANSCRIPT_SCHEMA_VERSION,
    SegmentIdentityError,
    canonical_segment_id_from_centiseconds,
    canonical_time_range,
    centiseconds_to_seconds,
)


class TranscriptSegmentValidationError(ValueError):
    """Raised when a transcript cannot satisfy its segment-ID contract."""


def normalize_transcript_segments(transcript: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize legacy and v2 transcripts into canonical review segments.

    Legacy documents derive IDs in memory. Version-2 documents must prove that
    their persisted IDs match the canonical time range exactly.
    """

    is_document = isinstance(transcript, dict)
    schema_version = transcript.get("transcript_schema_version") if is_document else None
    is_v2 = schema_version == TRANSCRIPT_SCHEMA_VERSION
    if is_document and schema_version is not None and not is_v2:
        raise TranscriptSegmentValidationError(f"Unsupported transcript schema version: {schema_version!r}.")
    if is_v2:
        _validate_v2_header(transcript)

    raw_segments = transcript.get("segments", []) if is_document else transcript
    if raw_segments is None:
        raw_segments = []
    if not isinstance(raw_segments, list):
        raise TranscriptSegmentValidationError("Transcript segments must be a list.")

    normalized: list[dict[str, Any]] = []
    segment_ids: set[str] = set()
    for index, item in enumerate(raw_segments):
        if not isinstance(item, dict):
            if is_v2:
                raise TranscriptSegmentValidationError(f"Version-2 segment {index} must be an object.")
            continue
        try:
            start_centiseconds, end_centiseconds = canonical_time_range(item.get("start"), item.get("end"))
        except SegmentIdentityError as exc:
            if is_v2:
                raise TranscriptSegmentValidationError(f"Invalid version-2 segment {index}: {exc}") from exc
            continue
        segment_id = canonical_segment_id_from_centiseconds(start_centiseconds, end_centiseconds)
        if is_v2:
            persisted_id = item.get("segment_id")
            if not isinstance(persisted_id, str) or not persisted_id:
                raise TranscriptSegmentValidationError(f"Version-2 segment {index} is missing segment_id.")
            if persisted_id != segment_id:
                raise TranscriptSegmentValidationError(f"Version-2 segment {index} has a mismatched segment_id.")
        if segment_id in segment_ids:
            raise TranscriptSegmentValidationError("Transcript contains duplicate canonical segment time ranges.")
        segment_ids.add(segment_id)
        try:
            importance = int(item.get("importance", 3) or 3)
        except (TypeError, ValueError) as exc:
            if is_v2:
                raise TranscriptSegmentValidationError(f"Invalid version-2 segment {index} importance.") from exc
            importance = 3
        normalized.append(
            {
                "segment_id": segment_id,
                "start": centiseconds_to_seconds(start_centiseconds),
                "end": centiseconds_to_seconds(end_centiseconds),
                "text": " ".join(str(item.get("text", "")).split()),
                "speaker": str(item.get("speaker") or item.get("speaker_id") or item.get("speakerId") or ""),
                "importance": importance,
                "chaos": bool(item.get("chaos", False)),
            }
        )
    return sorted(normalized, key=lambda item: (item["start"], item["end"]))


def _validate_v2_header(transcript: dict[str, Any]) -> None:
    if transcript.get("segment_id_scheme") != SEGMENT_ID_SCHEME:
        raise TranscriptSegmentValidationError("Version-2 transcript has an unsupported segment_id_scheme.")
    if transcript.get("segment_id_version") != SEGMENT_ID_VERSION:
        raise TranscriptSegmentValidationError("Version-2 transcript has an unsupported segment_id_version.")
