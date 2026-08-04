from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Mapping, Protocol

from transcription.base import TranscriptSegment, compact_text
from transcription.segment_identity import SegmentIdentityError, parse_time_to_decimal

from .base import DiarizationBackend, DiarizationConfig, DiarizationResult, SpeakerTurn
from .merger import DiarizationMergeError, merge_speaker_turns


class PyannoteDiarizationError(RuntimeError):
    """Raised when a Pyannote result cannot be represented as speaker turns."""


class PyannotePipeline(Protocol):
    """The minimal callable surface used from an injected Pyannote pipeline."""

    def __call__(self, audio_path: Path | str, **kwargs: int) -> Any: ...


PipelineFactory = Callable[[str, str, str], PyannotePipeline]


class PyannoteDiarizationBackend(DiarizationBackend):
    """Lazily run Community-1 and merge its exclusive turns into ASR segments."""

    name = "pyannote"

    def __init__(
        self,
        config: DiarizationConfig | PyannotePipeline | None = None,
        *,
        pipeline: PyannotePipeline | None = None,
        pipeline_factory: PipelineFactory | None = None,
        environment: Mapping[str, str] | None = None,
    ):
        # The positional injected pipeline keeps the accepted adapter's test/public contract.
        if config is not None and not isinstance(config, DiarizationConfig):
            if pipeline is not None:
                raise TypeError("Provide an injected Pyannote pipeline only once.")
            pipeline = config
            config = None
        self.config = config or DiarizationConfig()
        if self.config.mode != "pyannote":
            raise ValueError("PyannoteDiarizationBackend requires DIARIZATION_MODE=pyannote.")
        self._preload_audio = pipeline is None and pipeline_factory is None
        self._pipeline = pipeline
        self._pipeline_factory = pipeline_factory or self._load_default_pipeline
        self._environment = os.environ if environment is None else environment

    def assign_speakers(
        self,
        audio_path: Path,
        segments: list[TranscriptSegment],
    ) -> DiarizationResult:
        self._validate_word_timestamps(segments)
        started_at = time.perf_counter()
        turns = self.speaker_turns(audio_path) if segments else []
        try:
            merged = merge_speaker_turns(segments, turns)
        except DiarizationMergeError as exc:
            raise PyannoteDiarizationError(f"Diarization merger rejected the transcript: {exc}") from None

        segments[:] = merged.segments
        speaker_count = len(merged.speaker_label_map)
        status = "no_speech" if not segments else "applied"
        elapsed = time.perf_counter() - started_at
        return DiarizationResult(
            backend=self.name,
            enabled=True,
            status=status,
            speaker_count=speaker_count,
            diarization_seconds=elapsed,
            used_fallback=False,
            extra_metadata={
                "diarization_mode": self.config.mode,
                "diarization_model_id": self.config.model_id,
                "diarization_model_revision": self.config.model_revision,
                "diarization_device": self.config.device,
                "diarization_exclusive": True,
                "diarization_turn_count": len(turns),
                "speaker_label_map": merged.speaker_label_map,
                "non_overlapping_word_count": merged.non_overlapping_word_count,
                "max_non_overlap_distance_seconds": merged.max_non_overlap_distance_seconds,
            },
        )

    def speaker_turns(self, audio_path: Path | str) -> list[SpeakerTurn]:
        """Return turns from Pyannote's exclusive diarization annotation unchanged."""

        pipeline = self._get_pipeline()
        try:
            model_input = self._preload_audio_file(audio_path) if self._preload_audio else audio_path
            output = pipeline(model_input, **self.config.speaker_constraints())
        except Exception:
            raise PyannoteDiarizationError(
                "Pyannote diarization failed. Verify model access, HF_TOKEN, cache, and offline settings."
            ) from None
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

    def _get_pipeline(self) -> PyannotePipeline:
        if self._pipeline is not None:
            return self._pipeline
        token = str(self._environment.get("HF_TOKEN") or "").strip()
        if not token:
            raise PyannoteDiarizationError(
                "HF_TOKEN is required for DIARIZATION_MODE=pyannote."
            )
        try:
            self._pipeline = self._pipeline_factory(
                self.config.model_id,
                self.config.model_revision,
                token,
            )
        except Exception:
            raise PyannoteDiarizationError(
                "Pyannote model loading failed. Verify Community-1 access, HF_TOKEN, cache, and offline settings."
            ) from None
        return self._pipeline

    @staticmethod
    def _preload_audio_file(audio_path: Path | str) -> dict[str, Any]:
        """Decode through the existing FFmpeg runtime, avoiding TorchCodec platform DLL coupling."""

        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "pipe:1",
        ]
        decoded = subprocess.run(command, capture_output=True, check=True)
        import numpy as np
        import torch

        samples = np.frombuffer(decoded.stdout, dtype=np.float32).copy()
        waveform = torch.from_numpy(samples).unsqueeze(0)
        return {"waveform": waveform, "sample_rate": 16000}

    @staticmethod
    def _load_default_pipeline(model_id: str, revision: str, token: str) -> PyannotePipeline:
        # Imports stay inside the loader so API startup, DAG parsing, and off mode remain lightweight.
        from pyannote.audio import Pipeline
        import torch

        pipeline = Pipeline.from_pretrained(model_id, token=token, revision=revision)
        if pipeline is None:
            raise RuntimeError("Pyannote returned no pipeline.")
        pipeline.to(torch.device("cpu"))
        return pipeline

    @staticmethod
    def _validate_word_timestamps(segments: list[TranscriptSegment]) -> None:
        for index, segment in enumerate(segments):
            if compact_text(segment.text) and not segment.words:
                raise PyannoteDiarizationError(
                    f"Transcript segment {index} has text but no word timestamps; "
                    "Pyannote mode requires Faster-Whisper word timestamps."
                )

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
