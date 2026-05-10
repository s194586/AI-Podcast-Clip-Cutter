#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from gemini_transport import bootstrap_ssl_certificates

from diarization import DiarizationConfig, HeuristicDiarizationBackend
from transcription import FasterWhisperBackend, TranscriptionConfig


SUPPORTED_TRANSCRIPTION_BACKENDS = ("faster_whisper",)
SUPPORTED_DIARIZATION_BACKENDS = ("heuristic_cluster",)


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local transcription pipeline for Viral Cutter AI")
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
    parser.add_argument("--device", default="auto", help="cpu, cuda or auto")
    parser.add_argument("--compute-type", default="auto", help="float16, int8 or auto")
    parser.add_argument("--beam-size", type=int, default=5, help="Beam size for faster-whisper decoding")
    parser.add_argument("--disable-vad", action="store_true", help="Disable faster-whisper VAD filtering")
    parser.add_argument("--disable-word-timestamps", action="store_true", help="Disable word timestamps")
    parser.add_argument(
        "--enable-diarization",
        dest="enable_diarization",
        action="store_true",
        default=True,
        help="Enable local speaker attribution",
    )
    parser.add_argument(
        "--disable-diarization",
        dest="enable_diarization",
        action="store_false",
        help="Disable local speaker attribution and fallback to Speaker 0",
    )
    parser.add_argument(
        "--diarization-backend",
        default="heuristic_cluster",
        choices=SUPPORTED_DIARIZATION_BACKENDS,
        help="Local diarization backend",
    )
    parser.add_argument("--max-speakers", type=int, default=4, help="Maximum number of speakers to assign locally")
    parser.add_argument(
        "--diarization-threshold",
        type=float,
        default=0.8,
        help="Cosine similarity threshold for the heuristic diarizer",
    )
    return parser.parse_args()


def build_transcription_backend(args: argparse.Namespace):
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
        backend=args.diarization_backend,
        enabled=bool(args.enable_diarization),
        max_speakers=max(1, int(args.max_speakers)),
        similarity_threshold=float(args.diarization_threshold),
    )
    return HeuristicDiarizationBackend(config), config


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    audio_path = Path(args.file)
    output_path = Path(args.out)

    if not audio_path.exists():
        print(f"Input file not found: {audio_path}", file=sys.stderr)
        sys.exit(2)

    bootstrap_ssl_certificates(quiet=True)

    print("Starting local transcription...")
    print(f"  Audio file: {audio_path}")
    print(f"  Backend: {args.backend}")
    print(f"  Whisper model: {args.whisper_model}")
    print(f"  Device: {args.device} | Compute type: {args.compute_type}")
    print(f"  Diarization: {'enabled' if args.enable_diarization else 'disabled'} ({args.diarization_backend})")

    transcription_backend, transcription_config = build_transcription_backend(args)
    diarization_backend, diarization_config = build_diarization_backend(args)

    total_started_at = time.perf_counter()
    transcription_result = transcription_backend.transcribe(audio_path)
    if not transcription_result.duration_seconds:
        transcription_result.duration_seconds = get_duration(audio_path)

    print(
        f"  Transcription finished in {transcription_result.transcription_seconds:.1f}s "
        f"with {len(transcription_result.segments)} segments"
    )

    diarization_result = diarization_backend.assign_speakers(audio_path, transcription_result.segments)
    print(
        f"  Diarization status: {diarization_result.status} | "
        f"speakers: {diarization_result.speaker_count} | "
        f"fallback: {'yes' if diarization_result.used_fallback else 'no'}"
    )

    payload = transcription_result.to_dict()
    payload.setdefault("metadata", {})
    payload["metadata"].update(
        {
            "transcription_backend": transcription_config.backend,
            "diarization_enabled": diarization_config.enabled,
            "diarization_backend": diarization_result.backend,
            "diarization_status": diarization_result.status,
            "speaker_count": diarization_result.speaker_count,
            "diarization_seconds": round(diarization_result.diarization_seconds, 3),
            "diarization_used_fallback": diarization_result.used_fallback,
            "pipeline_seconds": round(time.perf_counter() - total_started_at, 3),
        }
    )
    payload["metadata"].update(diarization_result.extra_metadata)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, ensure_ascii=False, indent=2)

    print(f"Transcript saved to: {output_path}")


if __name__ == "__main__":
    main()
