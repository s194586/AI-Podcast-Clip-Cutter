from __future__ import annotations

from apps.pipeline.events import (
    STAGE_MESSAGES,
    STAGE_PROGRESS,
    PipelineEvent,
    message_for_stage,
    parse_pipeline_event,
)


def progress_for_stage(stage: str) -> float:
    return float(STAGE_PROGRESS.get(stage, 0.0))


def parse_structured_pipeline_event(line: str) -> PipelineEvent | None:
    return parse_pipeline_event(line)


def parse_manager_stage(line: str) -> str | None:
    text = str(line or "").casefold()
    if not text.strip():
        return None

    if any(marker in text for marker in ("analysis-only", "podcast clip analysis", "analiza podcast", "analyze_virals")):
        return "generating_candidates"
    if any(marker in text for marker in ("ai subtitler checker", "subtitle checker", "sprawdzane audio", "validating transcript")):
        return "validating_transcript"
    if any(marker in text for marker in ("transkrypc", "transcrib", "faster-whisper")):
        return "transcribing"
    if any(marker in text for marker in ("pobier", "download", "yt-dlp")):
        return "downloading"
    if any(marker in text for marker in ("podcast profile", "content classifier", "klasyfikacja", "content type")):
        return "generating_candidates"
    if any(marker in text for marker in ("importing candidate", "import candidates", "sqlite import")):
        return "importing_candidates"
    if any(marker in text for marker in ("gemini review", "reviewing boundaries", "reviewing with ai")):
        return "reviewing_with_ai"
    if any(marker in text for marker in ("workflow uko", "workflow completed", "ready")):
        return "ready"
    if any(marker in text for marker in ("cancelled", "anulowano")):
        return "cancelled"
    if any(marker in text for marker in ("unexpected error", "nieoczekiwany", " błąd", " blad", "error")):
        return "failed"
    return None
