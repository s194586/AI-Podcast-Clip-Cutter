from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from apps.api.db.database import init_database, session_scope
from apps.api.db.models import ClipEvaluation
from apps.api.db.repositories import ClipEvaluationRepository, ClipRepository, ProjectRepository
from apps.api.services.clips import validate_adjusted_bounds

try:
    from local_scoring import normalize_transcript_segments
except Exception:  # pragma: no cover - defensive fallback for isolated imports
    normalize_transcript_segments = None


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
PESEL_RE = re.compile(r"(?<!\d)\d{11}(?!\d)")
CREDIT_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)")
ADDRESS_RE = re.compile(
    r"\b(?:address|adres|street|st\.|ul\.|ulica|avenue|ave\.|road|rd\.|"
    r"mieszka(?:m|sz)?\s+(?:przy|na)|lives?\s+at)\b.{0,80}",
    re.IGNORECASE,
)
SENSITIVE_KEYWORDS = {
    "medical",
    "diagnosis",
    "therapy",
    "illness",
    "hospital",
    "medication",
    "legal",
    "lawsuit",
    "lawyer",
    "attorney",
    "financial",
    "bank account",
    "credit score",
    "debt",
    "diagnoza",
    "choroba",
    "lekarz",
    "terapia",
    "szpital",
    "prawnik",
    "pozew",
    "konto bankowe",
    "kredyt",
    "dług",
    "dlug",
}


def _parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_segments(payload: Any) -> list[dict[str, Any]]:
    if normalize_transcript_segments is not None:
        return normalize_transcript_segments(payload)

    raw_segments = payload.get("segments", []) if isinstance(payload, dict) else payload
    normalized: list[dict[str, Any]] = []
    for item in raw_segments or []:
        if not isinstance(item, dict):
            continue
        start = _parse_float(item.get("start"))
        end = _parse_float(item.get("end"), start)
        if end <= start:
            continue
        normalized.append(
            {
                "start": start,
                "end": end,
                "text": " ".join(str(item.get("text") or "").split()),
                "speaker": str(item.get("speaker") or item.get("speaker_id") or ""),
            }
        )
    return sorted(normalized, key=lambda item: item["start"])


def load_transcript_segments(transcript_path: Path | str | None) -> list[dict[str, Any]]:
    if not transcript_path:
        return []
    path = Path(transcript_path)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8-sig") as file_handle:
        payload = json.load(file_handle)
    return _normalize_segments(payload)


def _mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    masked_local = f"{local[:2]}***" if len(local) > 2 else "***"
    domain_parts = domain.split(".")
    if domain_parts:
        domain_parts[0] = f"{domain_parts[0][:2]}***" if len(domain_parts[0]) > 2 else "***"
    return f"{masked_local}@{'.'.join(domain_parts)}"


def _mask_digits(value: str) -> str:
    digits = [char for char in value if char.isdigit()]
    if len(digits) <= 4:
        return "***"
    return f"{''.join(digits[:2])}***{''.join(digits[-2:])}"


def _excerpt(value: str, limit: int = 56) -> str:
    compact = " ".join(str(value or "").split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3].rstrip()}..."


def check_sensitive_patterns(text: str) -> dict[str, Any]:
    content = str(text or "")
    matches: list[dict[str, str]] = []

    for match in EMAIL_RE.finditer(content):
        matches.append({"type": "email", "text": _mask_email(match.group(0)), "severity": "medium"})

    for match in CREDIT_CARD_RE.finditer(content):
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19:
            matches.append({"type": "credit_card_like", "text": _mask_digits(match.group(0)), "severity": "high"})

    for match in PESEL_RE.finditer(content):
        matches.append({"type": "pesel_like", "text": _mask_digits(match.group(0)), "severity": "high"})

    for match in PHONE_RE.finditer(content):
        digits = re.sub(r"\D", "", match.group(0))
        if 9 <= len(digits) <= 15:
            matches.append({"type": "phone", "text": _mask_digits(match.group(0)), "severity": "medium"})

    for match in ADDRESS_RE.finditer(content):
        matches.append({"type": "address_like", "text": _excerpt(match.group(0)), "severity": "medium"})

    lowered = content.lower()
    for keyword in sorted(SENSITIVE_KEYWORDS):
        if keyword in lowered:
            matches.append({"type": "sensitive_keyword", "text": keyword, "severity": "medium"})

    severities = {match["severity"] for match in matches}
    privacy_risk = "low"
    if "high" in severities:
        privacy_risk = "high"
    elif "medium" in severities:
        privacy_risk = "medium"

    if len([match for match in matches if match["severity"] == "medium"]) >= 3 and privacy_risk == "medium":
        privacy_risk = "high"

    return {"privacy_risk": privacy_risk, "matches": matches}


def evaluation_to_dict(evaluation: ClipEvaluation) -> dict[str, Any]:
    raw_result = dict(evaluation.raw_result_json or {})
    provider = str(getattr(evaluation, "provider", None) or raw_result.get("provider") or "local_stub")
    reviewed_start = getattr(evaluation, "reviewed_start", None)
    reviewed_end = getattr(evaluation, "reviewed_end", None)
    if reviewed_start is None:
        reviewed_start = evaluation.suggested_start
    if reviewed_end is None:
        reviewed_end = evaluation.suggested_end
    result = {
        "project_id": evaluation.project_id,
        "clip_id": evaluation.external_clip_id,
        "database_clip_id": evaluation.clip_id,
        "evaluation_id": evaluation.id,
        "provider": provider,
        "model": getattr(evaluation, "model", None) or raw_result.get("model") or "local_stub",
        "decision": evaluation.decision,
        "recommended_action": evaluation.recommended_action,
        "needs_more_context": evaluation.needs_more_context,
        "selected_start_option_index": raw_result.get("selected_start_option_index"),
        "selected_end_option_index": raw_result.get("selected_end_option_index"),
        "selected_start_segment_id": getattr(evaluation, "selected_start_segment_id", None)
        or raw_result.get("selected_start_segment_id"),
        "selected_end_segment_id": getattr(evaluation, "selected_end_segment_id", None)
        or raw_result.get("selected_end_segment_id"),
        "suggested_start": evaluation.suggested_start,
        "suggested_end": evaluation.suggested_end,
        "reviewed_start": reviewed_start,
        "reviewed_end": reviewed_end,
        "start_delta_seconds": getattr(evaluation, "start_delta_seconds", None) or raw_result.get("start_delta_seconds"),
        "end_delta_seconds": getattr(evaluation, "end_delta_seconds", None) or raw_result.get("end_delta_seconds"),
        "reasoning_summary": getattr(evaluation, "reasoning_summary", None)
        or raw_result.get("reasoning_summary")
        or _first_reason(evaluation.reasons_json or []),
        "start_reason": getattr(evaluation, "start_reason", None) or raw_result.get("start_reason") or "",
        "end_reason": getattr(evaluation, "end_reason", None) or raw_result.get("end_reason") or "",
        "reasons": list(evaluation.reasons_json or []),
        "warnings": list(evaluation.warnings_json or []),
        "context_expansions": int(raw_result.get("context_expansions") or 0),
        "context_seconds": getattr(evaluation, "context_seconds", None) or raw_result.get("context_seconds"),
        "failed": bool(raw_result.get("failed")),
        "failure_reason": raw_result.get("failure_reason"),
        "retry_used": bool(raw_result.get("retry_used")),
        "provider_attempt_count": int(raw_result.get("provider_attempt_count") or 1),
        "first_attempt_validation_error": raw_result.get("first_attempt_validation_error"),
        "final_validation_error": raw_result.get("final_validation_error"),
        "raw_result": raw_result,
        "created_at": evaluation.created_at.isoformat() if evaluation.created_at else None,
    }
    if provider != "gemini":
        result.update(
            {
                "quality_score": evaluation.quality_score,
                "context_score": evaluation.context_score,
                "hook_score": evaluation.hook_score,
                "payoff_score": evaluation.payoff_score,
                "boundary_score": evaluation.boundary_score,
                "privacy_risk": evaluation.privacy_risk,
                "crop_advice": evaluation.crop_advice,
            }
        )
    return result


def save_evaluation(result: dict[str, Any]) -> dict[str, Any]:
    init_database()
    with session_scope() as session:
        project_id = int(result["project_id"])
        external_clip_id = str(result["clip_id"])
        project = ProjectRepository(session).get(project_id)
        if project is None:
            raise ValueError(f"Unknown project_id: {project_id}")
        clip = ClipRepository(session).get_by_external_id(project_id, external_clip_id)
        raw_result = dict(result.get("raw_result") or result)
        raw_result.setdefault("context_expansions", result.get("context_expansions", 0))
        provider = str(result.get("provider") or raw_result.get("provider") or "local_stub")
        model = str(result.get("model") or raw_result.get("model") or provider)
        raw_result.setdefault("provider", provider)
        raw_result.setdefault("model", model)
        reviewed_start = result.get("reviewed_start", result.get("suggested_start"))
        reviewed_end = result.get("reviewed_end", result.get("suggested_end"))
        evaluation = ClipEvaluationRepository(session).create(
            project_id=project_id,
            clip_id=clip.id if clip is not None else None,
            external_clip_id=external_clip_id,
            provider=provider,
            model=model,
            decision=str(result.get("decision") or "reviewed"),
            quality_score=_parse_float(result.get("quality_score")),
            context_score=_parse_float(result.get("context_score")),
            hook_score=_parse_float(result.get("hook_score")),
            payoff_score=_parse_float(result.get("payoff_score")),
            boundary_score=_parse_float(result.get("boundary_score")),
            privacy_risk=str(result.get("privacy_risk") or "low"),
            recommended_action=str(result.get("recommended_action") or "manual_review"),
            selected_start_segment_id=_optional_text(result.get("selected_start_segment_id")),
            selected_end_segment_id=_optional_text(result.get("selected_end_segment_id")),
            suggested_start=reviewed_start,
            suggested_end=reviewed_end,
            reviewed_start=reviewed_start,
            reviewed_end=reviewed_end,
            start_delta_seconds=_parse_optional_float(result.get("start_delta_seconds")),
            end_delta_seconds=_parse_optional_float(result.get("end_delta_seconds")),
            reasoning_summary=str(result.get("reasoning_summary") or ""),
            start_reason=str(result.get("start_reason") or ""),
            end_reason=str(result.get("end_reason") or ""),
            context_seconds=_parse_optional_float(result.get("context_seconds")),
            crop_advice=str(result.get("crop_advice") or ""),
            needs_more_context=bool(result.get("needs_more_context")),
            reasons_json=list(result.get("reasons") or []),
            warnings_json=list(result.get("warnings") or []),
            raw_result_json=raw_result,
        )
        if clip is not None:
            _apply_review_to_clip(
                session=session,
                clip=clip,
                project=project,
                result=result,
                reviewed_start=reviewed_start,
                reviewed_end=reviewed_end,
            )
        return evaluation_to_dict(evaluation)


def _parse_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return _parse_float(value)


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value not in (None, "") else ""
    return text or None


def _first_reason(reasons: list[Any]) -> str:
    for reason in reasons:
        text = str(reason).strip()
        if text:
            return text
    return ""


def get_latest_evaluation(project_id: int, clip_id: str) -> dict[str, Any] | None:
    init_database()
    with session_scope() as session:
        evaluation = ClipEvaluationRepository(session).latest_for_clip(int(project_id), str(clip_id))
        return evaluation_to_dict(evaluation) if evaluation is not None else None


def _apply_review_to_clip(
    *,
    session: Any,
    clip: Any,
    project: Any,
    result: dict[str, Any],
    reviewed_start: Any,
    reviewed_end: Any,
) -> None:
    recommended_action = str(result.get("recommended_action") or "").strip().lower()
    if recommended_action == "reject":
        clip.status = "rejected"
        ClipRepository(session).touch(clip)
        ProjectRepository(session).touch(project)
        return

    if recommended_action not in {"render_ready", "adjust_boundaries"}:
        return
    if not bool(result.get("apply_safe_suggestions", True)):
        return
    if reviewed_start in (None, "") or reviewed_end in (None, ""):
        return

    clip_payload = {
        "min_start": clip.min_start,
        "max_start": clip.max_start,
        "min_end": clip.min_end,
        "max_end": clip.max_end,
    }
    try:
        edited_start, edited_end, _duration = validate_adjusted_bounds(clip_payload, reviewed_start, reviewed_end)
    except Exception:
        return

    clip.reviewed_start = edited_start
    clip.reviewed_end = edited_end
    clip.edited_start = edited_start
    clip.edited_end = edited_end
    clip.boundary_source = "ai_review"
    ClipRepository(session).touch(clip)
    ProjectRepository(session).touch(project)
