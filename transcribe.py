#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

from gemini_transport import bootstrap_ssl_certificates

from diarization import (
    DEFAULT_DIARIZATION_MODEL_ID,
    DEFAULT_DIARIZATION_MODEL_REVISION,
    SUPPORTED_DIARIZATION_MODES,
    DiarizationConfig,
    OffDiarizationBackend,
    PyannoteDiarizationBackend,
)
from transcription import TranscriptionConfig
from transcription.base import TranscriptSegment, TranscriptWord, TranscriptionResult, parse_time_to_seconds
from transcription.segment_identity import TRANSCRIPT_SCHEMA_VERSION


SUPPORTED_TRANSCRIPTION_BACKENDS = ("faster_whisper",)
SUPPORTED_TRANSCRIPTION_DEVICES = ("auto", "cuda", "cpu")
ASR_CHECKPOINT_SCHEMA_VERSION = 1


def get_duration(path: Path) -> float:
    import subprocess

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip() or 0.0)


def _environment_text(name: str, default: str) -> str:
    return str(os.environ.get(name) or default).strip() or default


def _environment_optional_integer(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    return value.strip() or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local transcription pipeline for Podcast Shorts Cutter")
    parser.add_argument("--file", required=True, help="Input audio/video file")
    parser.add_argument("--out", default="transcripts/final_transcript.json", help="Output transcript JSON path")
    parser.add_argument(
        "--backend",
        default="faster_whisper",
        choices=SUPPORTED_TRANSCRIPTION_BACKENDS,
        help="Local transcription backend",
    )
    parser.add_argument("--whisper-model", default="small", help="faster-whisper model name")
    parser.add_argument("--language", default=None, help="Language hint, e.g. pl or en")
    parser.add_argument(
        "--device",
        default=os.environ.get("TRANSCRIPTION_DEVICE", "auto"),
        choices=SUPPORTED_TRANSCRIPTION_DEVICES,
        help="Transcription device: auto, cuda or cpu. Defaults to TRANSCRIPTION_DEVICE or auto.",
    )
    parser.add_argument(
        "--compute-type",
        default=os.environ.get("TRANSCRIPTION_COMPUTE_TYPE", "auto"),
        help="faster-whisper compute type. Defaults to TRANSCRIPTION_COMPUTE_TYPE or auto.",
    )
    parser.add_argument("--beam-size", type=int, default=5, help="Beam size for faster-whisper decoding")
    parser.add_argument("--disable-vad", action="store_true", help="Disable faster-whisper VAD filtering")
    parser.add_argument("--disable-word-timestamps", action="store_true", help="Disable word timestamps")
    parser.add_argument(
        "--diarization-mode",
        choices=SUPPORTED_DIARIZATION_MODES,
        default=_environment_text("DIARIZATION_MODE", "pyannote"),
        help="Speaker diarization mode: pyannote or off.",
    )
    parser.add_argument(
        "--enable-diarization",
        dest="diarization_mode",
        action="store_const",
        const="pyannote",
        help="Compatibility alias for --diarization-mode pyannote.",
    )
    parser.add_argument(
        "--disable-diarization",
        dest="diarization_mode",
        action="store_const",
        const="off",
        help="Compatibility alias for --diarization-mode off.",
    )
    parser.add_argument(
        "--diarization-model-id",
        default=_environment_text("DIARIZATION_MODEL_ID", DEFAULT_DIARIZATION_MODEL_ID),
    )
    parser.add_argument(
        "--diarization-model-revision",
        default=_environment_text("DIARIZATION_MODEL_REVISION", DEFAULT_DIARIZATION_MODEL_REVISION),
    )
    parser.add_argument(
        "--diarization-device",
        default=_environment_text("DIARIZATION_DEVICE", "cpu"),
        choices=("cpu",),
    )
    parser.add_argument(
        "--diarization-num-speakers",
        type=int,
        default=_environment_optional_integer("DIARIZATION_NUM_SPEAKERS"),
    )
    parser.add_argument(
        "--diarization-min-speakers",
        type=int,
        default=_environment_optional_integer("DIARIZATION_MIN_SPEAKERS"),
    )
    parser.add_argument(
        "--diarization-max-speakers",
        "--max-speakers",
        dest="diarization_max_speakers",
        type=int,
        default=_environment_optional_integer("DIARIZATION_MAX_SPEAKERS"),
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def build_transcription_backend(args: argparse.Namespace):
    from transcription.faster_whisper_backend import FasterWhisperBackend

    config = TranscriptionConfig(
        backend=args.backend,
        model=args.whisper_model,
        language=args.language,
        device=args.device,
        compute_type=args.compute_type,
        beam_size=max(1, int(args.beam_size)),
        vad_filter=not args.disable_vad,
        word_timestamps=not args.disable_word_timestamps,
    )
    return FasterWhisperBackend(config), config


def build_diarization_backend(args: argparse.Namespace):
    config = DiarizationConfig(
        mode=args.diarization_mode,
        model_id=args.diarization_model_id,
        model_revision=args.diarization_model_revision,
        device=args.diarization_device,
        num_speakers=args.diarization_num_speakers,
        min_speakers=args.diarization_min_speakers,
        max_speakers=args.diarization_max_speakers,
    )
    if config.mode == "off":
        return OffDiarizationBackend(config), config
    return PyannoteDiarizationBackend(config), config


def transcribe_file(
    audio_path: Path | str,
    output_path: Path | str,
    *,
    backend: str = "faster_whisper",
    whisper_model: str = "small",
    language: str | None = None,
    device: str = "auto",
    compute_type: str = "auto",
    beam_size: int = 5,
    vad_filter: bool = True,
    word_timestamps: bool = True,
    diarization_mode: str = "pyannote",
    diarization_model_id: str = DEFAULT_DIARIZATION_MODEL_ID,
    diarization_model_revision: str = DEFAULT_DIARIZATION_MODEL_REVISION,
    diarization_device: str = "cpu",
    diarization_num_speakers: int | str | None = None,
    diarization_min_speakers: int | str | None = None,
    diarization_max_speakers: int | str | None = None,
    enable_diarization: bool | None = None,
    diarization_backend: str | None = None,
    max_speakers: int | None = None,
    _transcription_backend: Any | None = None,
    _diarization_backend: Any | None = None,
) -> dict[str, Any]:
    audio_path = Path(audio_path)
    output_path = Path(output_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Input file not found: {audio_path}")

    # Compatibility aliases remain deterministic and never accept the retired heuristic backend.
    if diarization_backend is not None:
        normalized_backend = str(diarization_backend).strip().lower()
        if normalized_backend not in SUPPORTED_DIARIZATION_MODES:
            raise ValueError("diarization_backend is retired; use diarization_mode=pyannote or off.")
        diarization_mode = normalized_backend
    if enable_diarization is not None:
        diarization_mode = "pyannote" if enable_diarization else "off"
    if max_speakers is not None and diarization_max_speakers is None:
        diarization_max_speakers = max_speakers

    args = argparse.Namespace(
        backend=backend,
        whisper_model=whisper_model,
        language=language,
        device=device,
        compute_type=compute_type,
        beam_size=beam_size,
        disable_vad=not vad_filter,
        disable_word_timestamps=not word_timestamps,
        diarization_mode=diarization_mode,
        diarization_model_id=diarization_model_id,
        diarization_model_revision=diarization_model_revision,
        diarization_device=diarization_device,
        diarization_num_speakers=diarization_num_speakers,
        diarization_min_speakers=diarization_min_speakers,
        diarization_max_speakers=diarization_max_speakers,
    )
    diarization, diarization_config = build_diarization_backend(args)
    if diarization_config.enabled and not word_timestamps:
        raise ValueError("Pyannote diarization requires Faster-Whisper word timestamps.")

    bootstrap_ssl_certificates(quiet=True)
    print("Starting local transcription...")
    print(f"  Audio file: {audio_path}")
    print(f"  Backend: {args.backend}")
    print(f"  Whisper model: {args.whisper_model}")
    print(f"  Device: {args.device} | Compute type: {args.compute_type}")
    print(f"  Diarization mode: {diarization_config.mode}")

    transcription_config = TranscriptionConfig(
        backend=backend,
        model=whisper_model,
        language=language,
        device=device,
        compute_type=compute_type,
        beam_size=max(1, int(beam_size)),
        vad_filter=vad_filter,
        word_timestamps=word_timestamps,
    )
    if _diarization_backend is not None:
        diarization = _diarization_backend

    total_started_at = time.perf_counter()
    checkpoint_path = output_path.parent / "asr_checkpoint.json"
    transcription_result = _load_asr_checkpoint(checkpoint_path, audio_path, transcription_config)
    checkpoint_reused = transcription_result is not None
    if transcription_result is not None:
        print(f"ASR checkpoint reused: {checkpoint_path}")
    else:
        if _transcription_backend is None:
            transcription = build_transcription_backend(args)[0]
        else:
            transcription = _transcription_backend
        try:
            transcription_result = transcription.transcribe(audio_path)
            if not transcription_result.duration_seconds:
                transcription_result.duration_seconds = get_duration(audio_path)
        finally:
            transcription.release_resources()

    print(
        f"  Transcription finished in {transcription_result.transcription_seconds:.1f}s "
        f"with {len(transcription_result.segments)} segments"
    )
    print(
        f"  Effective transcription device: {transcription_result.device} "
        f"({transcription_result.compute_type})"
    )

    if not checkpoint_reused:
        _write_asr_checkpoint(checkpoint_path, audio_path, transcription_config, transcription_result)

    diarization_result = diarization.assign_speakers(audio_path, transcription_result.segments)
    print(
        f"  Diarization status: {diarization_result.status} | "
        f"speakers: {diarization_result.speaker_count} | fallback: no"
    )

    payload = transcription_result.to_dict()
    payload.setdefault("metadata", {})
    payload["metadata"].update(
        {
            "transcription_backend": transcription_config.backend,
            "transcription_requested_device": transcription_config.device,
            "transcription_requested_compute_type": transcription_config.compute_type,
            "diarization_mode": diarization_config.mode,
            "diarization_backend": diarization_result.backend,
            "diarization_status": diarization_result.status,
            "diarization_model_id": diarization_config.model_id,
            "diarization_model_revision": diarization_config.model_revision,
            "diarization_device": diarization_config.device,
            "diarization_num_speakers": diarization_config.num_speakers,
            "diarization_min_speakers": diarization_config.min_speakers,
            "diarization_max_speakers": diarization_config.max_speakers,
            "diarization_exclusive": diarization_config.enabled,
            "speaker_count": diarization_result.speaker_count,
            "diarization_seconds": round(diarization_result.diarization_seconds, 3),
            "diarization_turn_count": 0,
            "speaker_label_map": {},
            "non_overlapping_word_count": 0,
            "max_non_overlap_distance_seconds": 0.0,
            "diarization_used_fallback": False,
            "pipeline_seconds": round(time.perf_counter() - total_started_at, 3),
        }
    )
    payload["metadata"].update(diarization_result.extra_metadata)
    _write_json_atomically(output_path, payload)

    print(f"Transcript saved to: {output_path}")
    return payload


def _write_json_atomically(output_path: Path, payload: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file_handle:
            temporary_path = Path(file_handle.name)
            json.dump(payload, file_handle, ensure_ascii=False, indent=2)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _audio_fingerprint(audio_path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with audio_path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "size": audio_path.stat().st_size}


def _checkpoint_config(config: TranscriptionConfig) -> dict[str, Any]:
    return {
        "backend": config.backend,
        "model": config.model,
        "language": config.language,
        "device": config.device,
        "compute_type": config.compute_type,
        "beam_size": config.beam_size,
        "vad_filter": config.vad_filter,
        "word_timestamps": config.word_timestamps,
    }


def _write_asr_checkpoint(
    checkpoint_path: Path,
    audio_path: Path,
    config: TranscriptionConfig,
    result: TranscriptionResult,
) -> None:
    payload = result.to_dict()
    payload.update(
        {
            "asr_checkpoint_schema_version": ASR_CHECKPOINT_SCHEMA_VERSION,
            "source_audio_fingerprint": _audio_fingerprint(audio_path),
            "transcription_config": _checkpoint_config(config),
            "checkpoint_backend": {
                "backend": result.backend,
                "model": result.model,
                "language": result.language,
                "device": result.device,
                "compute_type": result.compute_type,
            },
        }
    )
    _write_json_atomically(checkpoint_path, payload)
    print(f"ASR checkpoint saved to: {checkpoint_path}")


def _load_asr_checkpoint(
    checkpoint_path: Path,
    audio_path: Path,
    config: TranscriptionConfig,
) -> TranscriptionResult | None:
    if not checkpoint_path.exists():
        return None
    try:
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("checkpoint root is not an object")
        if payload.get("asr_checkpoint_schema_version") != ASR_CHECKPOINT_SCHEMA_VERSION:
            raise ValueError("checkpoint schema version mismatch")
        if payload.get("source_audio_fingerprint") != _audio_fingerprint(audio_path):
            raise ValueError("source audio fingerprint mismatch")
        if payload.get("transcription_config") != _checkpoint_config(config):
            raise ValueError("transcription configuration mismatch")
        metadata = payload.get("metadata")
        raw_segments = payload.get("segments")
        backend = payload.get("checkpoint_backend")
        if not isinstance(metadata, dict) or not isinstance(raw_segments, list) or not isinstance(backend, dict):
            raise ValueError("checkpoint is incomplete")
        if payload.get("transcript_schema_version") != TRANSCRIPT_SCHEMA_VERSION:
            raise ValueError("transcript schema version mismatch")
        segments = [_segment_from_checkpoint(item) for item in raw_segments]
        return TranscriptionResult(
            backend=str(backend["backend"]),
            model=str(backend["model"]),
            audio_path=audio_path,
            language=str(backend["language"]),
            duration_seconds=float(metadata["duration_seconds"]),
            transcription_seconds=float(metadata["transcription_seconds"]),
            segments=segments,
            device=str(backend["device"]),
            compute_type=str(backend["compute_type"]),
            extra_metadata={
                key: value
                for key, value in metadata.items()
                if key not in {"backend", "model", "audio", "language", "duration_seconds", "transcription_seconds", "device", "compute_type"}
            },
        )
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError, OverflowError) as exc:
        print(f"ASR checkpoint ignored: {checkpoint_path} ({exc})")
        return None


def _segment_from_checkpoint(payload: Any) -> TranscriptSegment:
    if not isinstance(payload, dict):
        raise ValueError("checkpoint segment is not an object")
    start = parse_time_to_seconds(payload["start"])
    end = parse_time_to_seconds(payload["end"])
    words = [
        TranscriptWord(
            start=parse_time_to_seconds(word["start"]),
            end=parse_time_to_seconds(word["end"]),
            text=str(word["text"]),
        )
        for word in payload.get("words", [])
    ]
    segment = TranscriptSegment(
        start=start,
        end=end,
        text=str(payload["text"]),
        speaker=str(payload.get("speaker", "")),
        importance=int(payload.get("importance", 3)),
        chaos=bool(payload.get("chaos", False)),
        words=words,
        extra_fields={key: value for key, value in payload.items() if key not in {"segment_id", "start", "end", "text", "speaker", "importance", "chaos", "words"}},
    )
    if payload.get("segment_id") != segment.to_dict()["segment_id"]:
        raise ValueError("checkpoint segment id mismatch")
    return segment


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    try:
        transcribe_file(
            args.file,
            args.out,
            backend=args.backend,
            whisper_model=args.whisper_model,
            language=args.language,
            device=args.device,
            compute_type=args.compute_type,
            beam_size=args.beam_size,
            vad_filter=not args.disable_vad,
            word_timestamps=not args.disable_word_timestamps,
            diarization_mode=args.diarization_mode,
            diarization_model_id=args.diarization_model_id,
            diarization_model_revision=args.diarization_model_revision,
            diarization_device=args.diarization_device,
            diarization_num_speakers=args.diarization_num_speakers,
            diarization_min_speakers=args.diarization_min_speakers,
            diarization_max_speakers=args.diarization_max_speakers,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
