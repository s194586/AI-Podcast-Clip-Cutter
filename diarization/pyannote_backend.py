from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from transcription.segment_identity import SegmentIdentityError, parse_time_to_decimal

from .base import SpeakerTurn


class PyannoteDiarizationError(ValueError):
    """Raised when a Pyannote result cannot be represented as speaker turns."""


class PyannotePipeline(Protocol):
    """The minimal callable surface used from an injected Pyannote pipeline."""

    def __call__(self, audio_path: Path | str) -> Any: ...


class PyannoteDiarizationBackend:
    """Adapt an injected Pyannote pipeline result into model-independent turns."""

    name = "pyannote"

    def __init__(self, pipeline: PyannotePipeline):
        self._pipeline = pipeline

    def speaker_turns(self, audio_path: Path | str) -> list[SpeakerTurn]:
        """Return turns from Pyannote's exclusive diarization annotation unchanged."""

        output = self._pipeline(audio_path)
        exclusive_diarization = self._exclusive_diarization(output)
        itertracks = getattr(exclusive_diarization, "itertracks", None)
        if not callable(itertracks):
            raise PyannoteDiarizationError(
                "Pyannote exclusive_speaker_diarization does not provide itertracks()."
            )

        speaker_turns: list[SpeakerTurn] = []
        for index, item in enumerate(itertracks(yield_label=True)):
            segment, raw_speaker = self._parse_item(item, index)
            start, end = self._parse_range(segment, index)
            self._validate_speaker(raw_speaker, index)
            speaker_turns.append(SpeakerTurn(start=start, end=end, raw_speaker=raw_speaker))
        return speaker_turns

    @staticmethod
    def _exclusive_diarization(output: Any) -> Any:
        try:
            exclusive_diarization = output.exclusive_speaker_diarization
        except AttributeError as exc:
            raise PyannoteDiarizationError(
                "Pyannote output is missing exclusive_speaker_diarization."
            ) from exc
        if exclusive_diarization is None:
            raise PyannoteDiarizationError(
                "Pyannote output is missing exclusive_speaker_diarization."
            )
        return exclusive_diarization

    @staticmethod
    def _parse_item(item: Any, index: int) -> tuple[Any, str]:
        try:
            segment, _track, raw_speaker = item
        except (TypeError, ValueError) as exc:
            raise PyannoteDiarizationError(
                f"Pyannote speaker turn {index} must contain segment, track, and speaker label."
            ) from exc
        return segment, raw_speaker

    @staticmethod
    def _parse_range(segment: Any, index: int) -> tuple[Any, Any]:
        try:
            start = segment.start
            end = segment.end
        except AttributeError as exc:
            raise PyannoteDiarizationError(
                f"Pyannote speaker turn {index} is missing start or end."
            ) from exc

        try:
            start_decimal = parse_time_to_decimal(start)
            end_decimal = parse_time_to_decimal(end)
        except SegmentIdentityError as exc:
            raise PyannoteDiarizationError(
                f"Pyannote speaker turn {index} must have finite numeric timestamps."
            ) from exc

        if start_decimal < 0:
            raise PyannoteDiarizationError(
                f"Pyannote speaker turn {index} start must not be negative."
            )
        if end_decimal <= start_decimal:
            raise PyannoteDiarizationError(
                f"Pyannote speaker turn {index} end must be after start."
            )
        return start, end

    @staticmethod
    def _validate_speaker(raw_speaker: Any, index: int) -> None:
        if not isinstance(raw_speaker, str) or not raw_speaker.strip():
            raise PyannoteDiarizationError(
                f"Pyannote speaker turn {index} has an empty speaker label."
            )
