from __future__ import annotations

STAGE_PROGRESS = {
    "waiting": 0.0,
    "downloading": 10.0,
    "transcribing": 30.0,
    "validating_transcript": 45.0,
    "generating_candidates": 60.0,
    "importing_candidates": 75.0,
    "reviewing_with_ai": 85.0,
    "ready": 100.0,
    "failed": 100.0,
    "cancelled": 100.0,
}

STAGE_MESSAGES = {
    "waiting": "Waiting to start",
    "downloading": "Downloading source media",
    "transcribing": "Transcribing podcast",
    "validating_transcript": "Validating transcript",
    "generating_candidates": "Generating candidate clips",
    "importing_candidates": "Importing candidate clips",
    "reviewing_with_ai": "Reviewing boundaries with AI",
    "ready": "Ready for review",
    "failed": "Failed",
    "cancelled": "Cancelled",
}


def progress_for_stage(stage: str) -> float:
    return STAGE_PROGRESS.get(stage, 0.0)


def message_for_stage(stage: str) -> str:
    return STAGE_MESSAGES.get(stage, stage.replace("_", " ").title())


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
