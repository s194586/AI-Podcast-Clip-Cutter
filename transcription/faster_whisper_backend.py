from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
from urllib.parse import quote

from faster_whisper import WhisperModel

from .base import (
    TranscriptSegment,
    TranscriptWord,
    TranscriptionConfig,
    TranscriptionResult,
    compact_text,
    estimate_chaos,
    estimate_importance,
)


GAP_SPLIT_SECONDS = 0.65
MAX_SEGMENT_DURATION = 7.5
MAX_SEGMENT_WORDS = 18
MAX_SEGMENT_CHARS = 180
TERMINAL_PUNCTUATION = (".", "!", "?", "…")
MIN_SEGMENT_DURATION = 0.08
SHORT_SEGMENT_MERGE_GAP = 0.25
REQUIRED_MODEL_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")


@dataclass
class _BufferedWord:
    start: float
    end: float
    text: str


class FasterWhisperBackend:
    name = "faster_whisper"

    def __init__(self, config: TranscriptionConfig):
        self.config = config
        self.resolved_device = self._resolve_device(config.device)
        self.resolved_compute_type = self._resolve_compute_type(self.resolved_device, config.compute_type)
        self.model_reference = self._resolve_model_reference(config.model)
        self.model = WhisperModel(
            self.model_reference,
            device=self.resolved_device,
            compute_type=self.resolved_compute_type,
            download_root=str(config.cache_dir),
        )

    def _resolve_device(self, requested: str) -> str:
        if requested and requested != "auto":
            return requested
        try:
            import ctranslate2

            return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            return "cpu"

    def _resolve_compute_type(self, device: str, compute_type: str) -> str:
        if compute_type and compute_type != "auto":
            return compute_type
        return "float16" if device == "cuda" else "int8"

    def _resolve_model_reference(self, requested_model: str) -> str:
        model_text = str(requested_model or "").strip() or self.config.model
        model_path = Path(model_text)
        if model_path.exists():
            return str(model_path)
        if "\\" in model_text or ":" in model_text or model_text.startswith("."):
            return str(model_path)
        local_model_dir = self._ensure_local_model_dir(model_text)
        return str(local_model_dir)

    def _ensure_local_model_dir(self, model_name: str) -> Path:
        repo_id = model_name if "/" in model_name else f"Systran/faster-whisper-{model_name}"
        target_dir = self.config.cache_dir / repo_id.replace("/", "--")
        required_paths = [target_dir / filename for filename in REQUIRED_MODEL_FILES]
        if all(path.exists() for path in required_paths):
            return target_dir

        target_dir.mkdir(parents=True, exist_ok=True)
        manifest = self._fetch_model_manifest(repo_id)
        siblings = manifest.get("siblings") or []
        available_files = {item.get("rfilename") for item in siblings if isinstance(item, dict)}
        files_to_download = [filename for filename in REQUIRED_MODEL_FILES if filename in available_files]
        if len(files_to_download) != len(REQUIRED_MODEL_FILES):
            missing = sorted(set(REQUIRED_MODEL_FILES) - set(files_to_download))
            raise RuntimeError(f"Model manifest for {repo_id} is missing required files: {', '.join(missing)}")

        for filename in files_to_download:
            destination = target_dir / filename
            if destination.exists():
                continue
            file_url = f"https://huggingface.co/{repo_id}/resolve/main/{quote(filename)}?download=1"
            self._curl_download(file_url, destination)

        manifest_path = target_dir / "_model_info.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return target_dir

    def _fetch_model_manifest(self, repo_id: str) -> dict[str, Any]:
        api_url = f"https://huggingface.co/api/models/{repo_id}"
        output = self._curl_fetch_text(api_url)
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Could not parse Hugging Face model metadata for {repo_id}") from exc

    def _curl_fetch_text(self, url: str) -> str:
        curl_binary = shutil.which("curl.exe") or shutil.which("curl")
        if not curl_binary:
            raise RuntimeError("curl is required to download faster-whisper models into the local cache.")
        cmd = [
            curl_binary,
            "--silent",
            "--show-error",
            "--location",
            "--ssl-no-revoke",
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "").strip() or f"curl failed for {url}")
        return result.stdout

    def _curl_download(self, url: str, destination: Path) -> None:
        curl_binary = shutil.which("curl.exe") or shutil.which("curl")
        if not curl_binary:
            raise RuntimeError("curl is required to download faster-whisper models into the local cache.")
        cmd = [
            curl_binary,
            "--silent",
            "--show-error",
            "--location",
            "--ssl-no-revoke",
            "--output",
            str(destination),
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "").strip() or f"curl download failed for {url}")

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        started_at = time.perf_counter()
        segments_iter, info = self.model.transcribe(
            str(audio_path),
            language=self.config.language,
            beam_size=max(1, int(self.config.beam_size)),
            vad_filter=bool(self.config.vad_filter),
            word_timestamps=bool(self.config.word_timestamps),
        )
        raw_segments = list(segments_iter)
        normalized_segments = []
        for raw_segment in raw_segments:
            normalized_segments.extend(self._split_segment(raw_segment))
        normalized_segments = self._merge_short_segments(normalized_segments)
        elapsed = time.perf_counter() - started_at

        language = getattr(info, "language", None) or self.config.language or "auto"
        duration_seconds = float(getattr(info, "duration", 0.0) or 0.0)
        return TranscriptionResult(
            backend=self.name,
            model=self.config.model,
            audio_path=audio_path,
            language=language,
            duration_seconds=duration_seconds,
            transcription_seconds=elapsed,
            segments=normalized_segments,
            device=self.resolved_device,
            compute_type=self.resolved_compute_type,
            extra_metadata={
                "vad_filter": bool(self.config.vad_filter),
                "word_timestamps": bool(self.config.word_timestamps),
                "beam_size": int(self.config.beam_size),
                "model_source": self.model_reference,
            },
        )

    def _split_segment(self, raw_segment: Any) -> list[TranscriptSegment]:
        words = self._normalize_words(getattr(raw_segment, "words", None))
        if not words:
            text = compact_text(getattr(raw_segment, "text", ""))
            if not text:
                return []
            duration = float(raw_segment.end) - float(raw_segment.start)
            return [
                TranscriptSegment(
                    start=float(raw_segment.start),
                    end=float(raw_segment.end),
                    text=text,
                    importance=estimate_importance(text, duration),
                    chaos=estimate_chaos(text, duration),
                    words=[],
                )
            ]

        grouped_segments: list[TranscriptSegment] = []
        buffer: list[_BufferedWord] = []

        def flush_buffer() -> None:
            nonlocal buffer
            if not buffer:
                return
            text = compact_text("".join(word.text for word in buffer))
            if not text:
                buffer = []
                return
            start = buffer[0].start
            end = buffer[-1].end
            duration = end - start
            grouped_segments.append(
                TranscriptSegment(
                    start=start,
                    end=end,
                    text=text,
                    importance=estimate_importance(text, duration),
                    chaos=estimate_chaos(text, duration),
                    words=[TranscriptWord(start=word.start, end=word.end, text=compact_text(word.text)) for word in buffer],
                )
            )
            buffer = []

        for index, word in enumerate(words):
            buffer.append(word)
            buffered_text = compact_text("".join(item.text for item in buffer))
            buffered_duration = buffer[-1].end - buffer[0].start
            next_word = words[index + 1] if index + 1 < len(words) else None
            current_token = word.text.strip()
            split_here = False

            if current_token.endswith(TERMINAL_PUNCTUATION):
                split_here = True
            elif next_word is not None and next_word.start - word.end >= GAP_SPLIT_SECONDS:
                split_here = True
            elif buffered_duration >= MAX_SEGMENT_DURATION:
                split_here = True
            elif len(buffer) >= MAX_SEGMENT_WORDS:
                split_here = True
            elif len(buffered_text) >= MAX_SEGMENT_CHARS:
                split_here = True

            if split_here:
                flush_buffer()

        flush_buffer()
        return grouped_segments

    def _merge_short_segments(self, segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
        if not segments:
            return segments

        merged: list[TranscriptSegment] = []
        pending = list(segments)
        index = 0
        while index < len(pending):
            current = pending[index]
            duration = current.end - current.start
            if duration < MIN_SEGMENT_DURATION:
                if merged and current.start - merged[-1].end <= SHORT_SEGMENT_MERGE_GAP:
                    merged[-1] = self._merge_pair(merged[-1], current)
                    index += 1
                    continue
                if index + 1 < len(pending) and pending[index + 1].start - current.end <= SHORT_SEGMENT_MERGE_GAP:
                    pending[index + 1] = self._merge_pair(current, pending[index + 1])
                    index += 1
                    continue
                current.end = current.start + MIN_SEGMENT_DURATION
            merged.append(current)
            index += 1
        return merged

    def _merge_pair(self, left: TranscriptSegment, right: TranscriptSegment) -> TranscriptSegment:
        merged_text = compact_text(f"{left.text} {right.text}")
        merged_words = list(left.words) + list(right.words)
        return TranscriptSegment(
            start=min(left.start, right.start),
            end=max(left.end, right.end),
            text=merged_text,
            speaker=left.speaker or right.speaker,
            importance=max(int(left.importance), int(right.importance)),
            chaos=bool(left.chaos or right.chaos),
            words=merged_words,
        )

    def _normalize_words(self, raw_words: Any) -> list[_BufferedWord]:
        normalized: list[_BufferedWord] = []
        if not raw_words:
            return normalized

        for raw_word in raw_words:
            start = getattr(raw_word, "start", None)
            end = getattr(raw_word, "end", None)
            text = getattr(raw_word, "word", None) or getattr(raw_word, "text", None)
            if start is None or end is None or text is None:
                continue
            text = str(text)
            if not text.strip():
                continue
            start = float(start)
            end = float(end)
            if end <= start:
                continue
            normalized.append(_BufferedWord(start=start, end=end, text=text))
        return normalized
