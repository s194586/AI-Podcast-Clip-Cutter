from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

from apps.api.db.database import session_scope
from apps.api.db.repositories import JobRepository
from diarization import (
    DEFAULT_DIARIZATION_MODEL_ID,
    DEFAULT_DIARIZATION_MODEL_REVISION,
    SUPPORTED_DIARIZATION_MODES,
)

from .cancellation import CancellationToken
from .config import PipelineConfig
from .context import PipelineContext
from .persistence import ProjectStateEventSink
from .profiles import project_pipeline_stages
from .reporting import StructuredEventPrinter
from .runner import PipelineRunner


DEFAULT_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _environment_text(name: str, default: str) -> str:
    return str(os.environ.get(name) or default).strip() or default


def _environment_optional_integer(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    return value.strip() or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one isolated podcast project pipeline.")
    parser.add_argument("--project-id", required=True, type=int)
    parser.add_argument("--job-id", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--workspace-dir", required=True)
    parser.add_argument("--repository-root", default=str(DEFAULT_REPOSITORY_ROOT), help=argparse.SUPPRESS)
    review_group = parser.add_mutually_exclusive_group()
    review_group.add_argument("--auto-review", dest="auto_review", action="store_true")
    review_group.add_argument("--no-auto-review", dest="auto_review", action="store_false")
    parser.set_defaults(auto_review=True)
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--skip-subtitle-checker", action="store_true")
    parser.add_argument("--force-subtitle-checker", action="store_true")
    parser.add_argument("--subtitle-checker-mode", default="local_only", choices=("off", "local_only", "limited", "full"))
    parser.add_argument("--subtitle-checker-ai-samples", type=int, default=8)
    parser.add_argument("--transcription-backend", default="faster_whisper")
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument("--transcription-device", default="auto", choices=("auto", "cuda", "cpu"))
    parser.add_argument("--transcription-compute-type", default="auto")
    parser.add_argument("--diarization-mode", choices=SUPPORTED_DIARIZATION_MODES, default=_environment_text("DIARIZATION_MODE", "pyannote"))
    parser.add_argument("--enable-diarization", dest="diarization_mode", action="store_const", const="pyannote")
    parser.add_argument("--disable-diarization", dest="diarization_mode", action="store_const", const="off")
    parser.add_argument("--diarization-model-id", default=_environment_text("DIARIZATION_MODEL_ID", DEFAULT_DIARIZATION_MODEL_ID))
    parser.add_argument("--diarization-model-revision", default=_environment_text("DIARIZATION_MODEL_REVISION", DEFAULT_DIARIZATION_MODEL_REVISION))
    parser.add_argument("--diarization-device", choices=("cpu",), default=_environment_text("DIARIZATION_DEVICE", "cpu"))
    parser.add_argument("--diarization-num-speakers", type=int, default=_environment_optional_integer("DIARIZATION_NUM_SPEAKERS"))
    parser.add_argument("--diarization-min-speakers", type=int, default=_environment_optional_integer("DIARIZATION_MIN_SPEAKERS"))
    parser.add_argument("--diarization-max-speakers", type=int, default=_environment_optional_integer("DIARIZATION_MAX_SPEAKERS"))
    parser.add_argument("--content-type", default="auto", choices=("auto", "podcast"))
    parser.add_argument("--layout-mode", default="auto", choices=("auto", "speaker_face_crop"))
    return parser


def create_context(args: argparse.Namespace) -> PipelineContext:
    config = PipelineConfig(
        skip_download=args.skip_download,
        skip_subtitle_checker=args.skip_subtitle_checker,
        force_subtitle_checker=args.force_subtitle_checker,
        ai_mode="local_only",
        subtitle_checker_mode=args.subtitle_checker_mode,
        subtitle_checker_ai_samples=args.subtitle_checker_ai_samples,
        transcription_backend=args.transcription_backend,
        whisper_model=args.whisper_model,
        transcription_device=args.transcription_device,
        transcription_compute_type=args.transcription_compute_type,
        diarization_mode=args.diarization_mode,
        diarization_model_id=args.diarization_model_id,
        diarization_model_revision=args.diarization_model_revision,
        diarization_device=args.diarization_device,
        diarization_num_speakers=args.diarization_num_speakers,
        diarization_min_speakers=args.diarization_min_speakers,
        diarization_max_speakers=args.diarization_max_speakers,
        content_type=args.content_type,
        layout_mode=args.layout_mode,
    )
    return PipelineContext(
        project_id=args.project_id,
        job_id=args.job_id,
        source_url=args.source_url,
        workspace_path=Path(args.workspace_dir),
        repository_root=Path(args.repository_root),
        auto_review=bool(args.auto_review),
        analysis_only=True,
        config=config,
        cancellation=CancellationToken(
            external_check=_database_cancellation_check(args.job_id)
            if args.job_id is not None
            else None
        ),
    )


def _database_cancellation_check(job_id: int):
    def is_cancelled() -> bool:
        try:
            with session_scope() as session:
                job = JobRepository(session).get(int(job_id))
                return bool(job is not None and (job.cancel_requested or job.status == "cancelled"))
        except Exception:
            # A transient status-read failure must not recategorise the review as cancelled.
            return False

    return is_cancelled


def run_project_pipeline(context: PipelineContext) -> int:
    _install_cancellation_handlers(context)
    runner = PipelineRunner(
        project_pipeline_stages(context),
        event_sinks=(ProjectStateEventSink(context), StructuredEventPrinter()),
    )
    return runner.run(context).exit_code


def _install_cancellation_handlers(context: PipelineContext) -> None:
    def request_cancellation(_signum, _frame) -> None:
        context.cancellation.cancel()

    for signal_name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        candidate = getattr(signal, signal_name, None)
        if candidate is not None:
            signal.signal(candidate, request_cancellation)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        context = create_context(args)
    except ValueError as exc:
        print(f"Pipeline configuration error: {exc}", file=sys.stderr)
        return 2
    return run_project_pipeline(context)


if __name__ == "__main__":
    raise SystemExit(main())
