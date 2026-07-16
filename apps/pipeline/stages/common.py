from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TypeVar

from ..context import PipelineContext
from ..exceptions import PipelineError


MEDIA_AUDIO_EXTENSIONS = (".mp3", ".m4a", ".wav", ".aac")
MEDIA_VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".webm")
ErrorType = TypeVar("ErrorType", bound=PipelineError)


def subprocess_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["UV_NATIVE_TLS"] = "1"
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def run_stage_command(
    context: PipelineContext,
    command: list[str],
    *,
    description: str,
    error_type: type[ErrorType],
    max_attempts: int = 1,
) -> None:
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        print(f"\n{description}")
        if attempt > 1:
            print(f"  Attempt {attempt}/{attempts}")
        try:
            subprocess.run(
                command,
                check=True,
                cwd=context.repository_root,
                env=subprocess_environment(),
            )
            return
        except subprocess.CalledProcessError as exc:
            if attempt >= attempts:
                raise error_type(
                    f"{description} failed with exit code {exc.returncode}."
                ) from exc
            time.sleep(2 ** (attempt - 1))
        except OSError as exc:
            raise error_type(f"{description} could not start: {exc}") from exc


class MediaLocator:
    def __init__(self, context: PipelineContext) -> None:
        self.context = context

    def probe_streams(self, path: Path, stream_type: str) -> int:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            stream_type,
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ]
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                cwd=self.context.repository_root,
                env=subprocess_environment(),
            )
        except (OSError, subprocess.CalledProcessError):
            return 0
        return len([line for line in result.stdout.splitlines() if line.strip()])

    def has_audio(self, path: Path) -> bool:
        return self.probe_streams(path, "a") > 0

    def has_video(self, path: Path) -> bool:
        return self.probe_streams(path, "v") > 0

    def latest_video(self) -> Path | None:
        candidates = self._files_with_suffixes(MEDIA_VIDEO_EXTENSIONS)
        for candidate in sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True):
            if self.has_video(candidate) and self.has_audio(candidate):
                return candidate
        return None

    def latest_audio(self) -> Path | None:
        audio_candidates = self._files_with_suffixes(MEDIA_AUDIO_EXTENSIONS)
        if audio_candidates:
            return max(audio_candidates, key=lambda item: item.stat().st_mtime)

        containers = self._files_with_suffixes(MEDIA_VIDEO_EXTENSIONS)
        for candidate in sorted(containers, key=lambda item: item.stat().st_mtime, reverse=True):
            if self.has_audio(candidate):
                return self.extract_audio(candidate)
        return None

    def extract_audio(self, media_path: Path) -> Path | None:
        output_path = self.context.input_dir / f"{media_path.stem}.mp3"
        if output_path.exists():
            return output_path
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(media_path), "-q:a", "9", str(output_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=self.context.repository_root,
                env=subprocess_environment(),
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return output_path

    def _files_with_suffixes(self, suffixes: tuple[str, ...]) -> list[Path]:
        if not self.context.input_dir.exists():
            return []
        return [
            path
            for path in self.context.input_dir.iterdir()
            if path.is_file() and path.suffix.lower() in suffixes
        ]


def python_script(context: PipelineContext, filename: str) -> list[str]:
    return [sys.executable, str(context.repository_root / filename)]
