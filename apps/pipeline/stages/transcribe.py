from __future__ import annotations

from transcribe import transcribe_file

from ..context import PipelineContext
from ..exceptions import TranscriptionStageError
from ..results import PipelineStageResult
from .common import MediaLocator


class TranscribeAudioStage:
    stage = "transcribing"

    def run(self, context: PipelineContext) -> PipelineStageResult:
        if context.transcript_file.exists():
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
                enable_diarization=context.config.enable_diarization,
                diarization_backend=context.config.diarization_backend,
                max_speakers=context.config.diarization_max_speakers,
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
