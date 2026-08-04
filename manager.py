#!/usr/bin/env python3
"""Backwards-compatible CLI for the reusable podcast pipeline services."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from apps.pipeline.config import PipelineConfig
from apps.pipeline.context import PipelineContext
from apps.pipeline.profiles import legacy_cli_stages
from apps.pipeline.reporting import HumanReadableEventPrinter, print_legacy_summary
from apps.pipeline.runner import PipelineRunner
from apps.pipeline.content_mode import VALID_CONTENT_TYPE_MODES
from layout import VALID_LAYOUT_MODES
from diarization import (
    DEFAULT_DIARIZATION_MODEL_ID,
    DEFAULT_DIARIZATION_MODEL_REVISION,
    SUPPORTED_DIARIZATION_MODES,
)
from pipeline_modes import VALID_AI_MODES, VALID_SUBTITLE_CHECKER_MODES

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


class ManagerError(RuntimeError):
    """Compatibility error raised by the legacy manager facade."""


def _environment_text(name: str, default: str) -> str:
    return str(os.environ.get(name) or default).strip() or default


def _environment_optional_integer(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    return value.strip() or None


class WorkflowManager:
    """Compatibility facade that delegates all work to PipelineRunner."""

    def __init__(
        self,
        url: str | None,
        cleanup: bool = False,
        skip_download: bool = False,
        skip_subtitle_checker: bool = False,
        skip_smart_context: bool = False,
        force_subtitle_checker: bool = False,
        auto_fix_subtitles: bool = True,
        ai_mode: str = "gemini_optional",
        subtitle_checker_mode: str | None = None,
        subtitle_checker_ai_samples: int = 8,
        transcription_backend: str = "faster_whisper",
        whisper_model: str = "small",
        transcription_device: str = "auto",
        transcription_compute_type: str = "auto",
        diarization_mode: str = "pyannote",
        diarization_model_id: str = DEFAULT_DIARIZATION_MODEL_ID,
        diarization_model_revision: str = DEFAULT_DIARIZATION_MODEL_REVISION,
        diarization_device: str = "cpu",
        diarization_num_speakers: int | None = None,
        diarization_min_speakers: int | None = None,
        diarization_max_speakers: int | None = None,
        content_type: str = "auto",
        layout_mode: str = "auto",
        workspace_dir: str | None = None,
        analysis_only: bool = False,
    ) -> None:
        self.script_dir = Path(__file__).resolve().parent
        self.runtime_dir = Path(workspace_dir).resolve() if workspace_dir else self.script_dir
        self.analysis_only = bool(analysis_only)
        self.config = PipelineConfig(
            cleanup=cleanup,
            skip_download=skip_download,
            skip_subtitle_checker=skip_subtitle_checker,
            skip_smart_context=skip_smart_context,
            force_subtitle_checker=force_subtitle_checker,
            auto_fix_subtitles=auto_fix_subtitles,
            ai_mode=ai_mode,
            subtitle_checker_mode=subtitle_checker_mode,
            subtitle_checker_ai_samples=subtitle_checker_ai_samples,
            transcription_backend=transcription_backend,
            whisper_model=whisper_model,
            transcription_device=transcription_device,
            transcription_compute_type=transcription_compute_type,
            diarization_mode=diarization_mode,
            diarization_model_id=diarization_model_id,
            diarization_model_revision=diarization_model_revision,
            diarization_device=diarization_device,
            diarization_num_speakers=diarization_num_speakers,
            diarization_min_speakers=diarization_min_speakers,
            diarization_max_speakers=diarization_max_speakers,
            content_type=content_type,
            layout_mode=layout_mode,
        )
        self.context = PipelineContext.for_legacy_cli(
            source_url=url,
            repository_root=self.script_dir,
            workspace_path=self.runtime_dir,
            analysis_only=self.analysis_only,
            config=self.config,
        )

    def run(self) -> int:
        print("\nVIRAL CUTTER AI - WORKFLOW MANAGER")
        print(f"Source URL configured: {'yes' if self.context.source_url else 'no'}")
        print(f"Workspace: {self.runtime_dir}")
        print(f"Analysis only: {'yes' if self.analysis_only else 'no'}")
        print(f"Candidate selection mode: {self.config.ai_mode}")
        runner = PipelineRunner(
            legacy_cli_stages(self.context),
            event_sinks=(HumanReadableEventPrinter(),),
        )
        result = runner.run(self.context)
        print_legacy_summary(self.context, result)
        return result.exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Podcast workflow: download -> transcribe -> validate -> candidates -> optional render",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url", required=False, help="Source video URL; optional when input media already exists.")
    parser.add_argument("--cleanup", action="store_true", help="Remove input media after a successful workflow.")
    parser.add_argument("--skip-download", action="store_true", help="Reuse input media when it already exists.")
    parser.add_argument("--skip-subtitle-checker", action="store_true")
    parser.add_argument("--force-subtitle-checker", action="store_true")
    parser.add_argument("--skip-smart-context", action="store_true")
    parser.add_argument("--workspace-dir", default=None, help="Runtime workspace; defaults to the repository root.")
    parser.add_argument("--analysis-only", action="store_true", help="Stop after candidate generation.")
    parser.add_argument("--ai-mode", choices=VALID_AI_MODES, default="gemini_optional")
    parser.add_argument("--subtitle-checker-mode", choices=VALID_SUBTITLE_CHECKER_MODES, default=None)
    parser.add_argument("--subtitle-checker-ai-samples", type=int, default=8)
    parser.add_argument("--transcription-backend", default="faster_whisper")
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument(
        "--transcription-device",
        default=os.environ.get("TRANSCRIPTION_DEVICE", "auto"),
        choices=("auto", "cuda", "cpu"),
    )
    parser.add_argument(
        "--transcription-compute-type",
        default=os.environ.get("TRANSCRIPTION_COMPUTE_TYPE", "auto"),
    )
    parser.add_argument(
        "--diarization-mode",
        choices=SUPPORTED_DIARIZATION_MODES,
        default=_environment_text("DIARIZATION_MODE", "pyannote"),
    )
    parser.add_argument("--enable-diarization", dest="diarization_mode", action="store_const", const="pyannote")
    parser.add_argument("--disable-diarization", dest="diarization_mode", action="store_const", const="off")
    parser.add_argument(
        "--diarization-model-id",
        default=_environment_text("DIARIZATION_MODEL_ID", DEFAULT_DIARIZATION_MODEL_ID),
    )
    parser.add_argument(
        "--diarization-model-revision",
        default=_environment_text("DIARIZATION_MODEL_REVISION", DEFAULT_DIARIZATION_MODEL_REVISION),
    )
    parser.add_argument("--diarization-device", choices=("cpu",), default=_environment_text("DIARIZATION_DEVICE", "cpu"))
    parser.add_argument("--diarization-num-speakers", type=int, default=_environment_optional_integer("DIARIZATION_NUM_SPEAKERS"))
    parser.add_argument("--diarization-min-speakers", type=int, default=_environment_optional_integer("DIARIZATION_MIN_SPEAKERS"))
    parser.add_argument("--diarization-max-speakers", type=int, default=_environment_optional_integer("DIARIZATION_MAX_SPEAKERS"))
    parser.add_argument("--content-type", choices=VALID_CONTENT_TYPE_MODES, default="auto")
    parser.add_argument("--layout-mode", choices=VALID_LAYOUT_MODES, default="auto")
    parser.set_defaults(auto_fix_subtitles=True)
    parser.add_argument("--auto-fix-subtitles", dest="auto_fix_subtitles", action="store_true")
    parser.add_argument("--no-auto-fix-subtitles", dest="auto_fix_subtitles", action="store_false")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _configure_console() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    root = Path(__file__).resolve().parent
    if load_dotenv is not None and (root / ".env").exists():
        load_dotenv(root / ".env")
    args = parse_args(argv)
    manager = WorkflowManager(
        url=args.url,
        cleanup=args.cleanup,
        skip_download=args.skip_download,
        skip_subtitle_checker=args.skip_subtitle_checker,
        skip_smart_context=args.skip_smart_context,
        force_subtitle_checker=args.force_subtitle_checker,
        auto_fix_subtitles=args.auto_fix_subtitles,
        ai_mode=args.ai_mode,
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
        workspace_dir=args.workspace_dir,
        analysis_only=args.analysis_only,
    )
    return manager.run()


if __name__ == "__main__":
    raise SystemExit(main())
