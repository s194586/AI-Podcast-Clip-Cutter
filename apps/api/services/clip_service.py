from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db.database import init_database, session_scope
from ..db.models import Clip, Project
from ..db.repositories import ClipRepository, ProjectRepository
from .artifact_service import record_render_artifacts
from .clips import ClipValidationError, validate_adjusted_bounds
from .project_state import DEFAULT_PROJECT_ID, PROJECT_ROOT


def clip_to_dict(clip: Clip) -> dict[str, Any]:
    duration = round(float(clip.edited_end) - float(clip.edited_start), 2)
    latest_review = clip.evaluations[0] if clip.evaluations else None
    latest_review_raw = dict(latest_review.raw_result_json or {}) if latest_review else {}
    return {
        "id": clip.external_id,
        "database_id": clip.id,
        "project_id": clip.project_id,
        "index": clip.clip_index,
        "ai_start": round(float(clip.ai_start), 2),
        "ai_end": round(float(clip.ai_end), 2),
        "reviewed_start": _round_optional(clip.reviewed_start),
        "reviewed_end": _round_optional(clip.reviewed_end),
        "edited_start": round(float(clip.edited_start), 2),
        "edited_end": round(float(clip.edited_end), 2),
        "boundary_source": clip.boundary_source or "heuristic",
        "min_start": round(float(clip.min_start), 2),
        "max_start": round(float(clip.max_start), 2),
        "min_end": round(float(clip.min_end), 2),
        "max_end": round(float(clip.max_end), 2),
        "duration": duration,
        "summary": clip.summary or "",
        "text": clip.text or "",
        "source": clip.source or "",
        "status": clip.status or "draft",
        "candidate_id": clip.candidate_id,
        "selection_source": clip.selection_source,
        "local_score": clip.local_score,
        "local_rank": clip.local_rank,
        "selection_reasons": list(clip.selection_reasons or []),
        "local_features": dict(clip.local_features or {}),
        "render_status": clip.render_status or "not_rendered",
        "raw_outputs": list(clip.raw_outputs or []),
        "subtitled_outputs": list(clip.subtitled_outputs or []),
        "last_render_output_dir": clip.last_render_output_dir or "",
        "last_render_warnings": list(clip.last_render_warnings or []),
        "latest_review_provider": latest_review.provider if latest_review else None,
        "latest_review_model": latest_review.model if latest_review else None,
        "latest_review_decision": latest_review.decision if latest_review else None,
        "latest_review_recommended_action": latest_review.recommended_action if latest_review else None,
        "latest_review_reasoning_summary": _review_reasoning_summary(latest_review),
        "latest_review_start_reason": latest_review.start_reason if latest_review else "",
        "latest_review_end_reason": latest_review.end_reason if latest_review else "",
        "latest_review_warnings": list(latest_review.warnings_json or []) if latest_review else [],
        "latest_review_failed": bool(latest_review_raw.get("failed")),
        "latest_review_failure_category": latest_review_raw.get("failure_category"),
        "latest_review_changed_boundaries": _review_changed_boundaries(clip, latest_review),
        "created_at": clip.created_at.isoformat() if clip.created_at else None,
        "updated_at": clip.updated_at.isoformat() if clip.updated_at else None,
    }


def _round_optional(value: float | None) -> float | None:
    return round(float(value), 2) if value is not None else None


def _review_reasoning_summary(review: Any) -> str:
    if review is None:
        return ""
    summary = str(getattr(review, "reasoning_summary", "") or "").strip()
    if summary:
        return summary
    text_reasons = [str(reason).strip() for reason in getattr(review, "reasons_json", []) or [] if str(reason).strip()]
    if not text_reasons:
        return ""
    return " ".join(text_reasons[:2])


def _review_changed_boundaries(clip: Clip, review: Any) -> bool:
    if review is None:
        return False
    reviewed_start = getattr(review, "reviewed_start", None)
    reviewed_end = getattr(review, "reviewed_end", None)
    if reviewed_start is None:
        reviewed_start = getattr(review, "suggested_start", None)
    if reviewed_end is None:
        reviewed_end = getattr(review, "suggested_end", None)
    if reviewed_start is None or reviewed_end is None:
        return False
    return abs(float(reviewed_start) - float(clip.ai_start)) > 0.01 or abs(float(reviewed_end) - float(clip.ai_end)) > 0.01


def _resolve_project(session, project_id: int | str | None, project_root: Path) -> Project | None:
    project_repo = ProjectRepository(session)
    if isinstance(project_id, int):
        return project_repo.get(project_id)
    if isinstance(project_id, str) and project_id.isdigit():
        return project_repo.get(int(project_id))

    project = project_repo.get_default()
    if project is not None:
        return project

    from .legacy_import_service import bootstrap_legacy_state_if_needed

    return bootstrap_legacy_state_if_needed(session, project_root=project_root, project_id=DEFAULT_PROJECT_ID)


def load_clips(
    *,
    project_id: int | str | None = None,
    project_root: Path = PROJECT_ROOT,
) -> list[dict[str, Any]]:
    init_database()
    with session_scope() as session:
        project = _resolve_project(session, project_id, project_root)
        if project is None:
            return []
        return [clip_to_dict(clip) for clip in ClipRepository(session).list_for_project(project.id)]


def load_project_clips(project_id: int, *, project_root: Path = PROJECT_ROOT) -> list[dict[str, Any]]:
    return load_clips(project_id=project_id, project_root=project_root)


def update_bounds(
    clip_id: str,
    start: Any,
    end: Any,
    *,
    project_id: int | str | None = None,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    init_database()
    with session_scope() as session:
        project = _resolve_project(session, project_id, project_root)
        if project is None:
            raise ClipValidationError("No project is available.")

        clip_repo = ClipRepository(session)
        clip = clip_repo.get_by_external_id(project.id, clip_id)
        if clip is None:
            raise ClipValidationError(f"Unknown clip_id: {clip_id}")

        edited_start, edited_end, _duration = validate_adjusted_bounds(clip_to_dict(clip), start, end)
        clip.edited_start = edited_start
        clip.edited_end = edited_end
        clip.boundary_source = "user"
        clip_repo.touch(clip)
        ProjectRepository(session).touch(project)
        return clip_to_dict(clip)


def set_status(
    clip_id: str,
    status: str,
    *,
    project_id: int | str | None = None,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {"draft", "accepted", "rejected"}:
        raise ClipValidationError("Clip status must be draft, accepted, or rejected.")

    init_database()
    with session_scope() as session:
        project = _resolve_project(session, project_id, project_root)
        if project is None:
            raise ClipValidationError("No project is available.")

        clip_repo = ClipRepository(session)
        clip = clip_repo.get_by_external_id(project.id, clip_id)
        if clip is None:
            raise ClipValidationError(f"Unknown clip_id: {clip_id}")
        clip.status = normalized_status
        clip_repo.touch(clip)
        ProjectRepository(session).touch(project)
        return clip_to_dict(clip)


def record_render_result(
    clip_id: str,
    render_result: dict[str, Any],
    *,
    project_id: int | str | None = None,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    init_database()
    with session_scope() as session:
        project = _resolve_project(session, project_id, project_root)
        if project is None:
            raise ClipValidationError("No project is available.")

        clip_repo = ClipRepository(session)
        clip = clip_repo.get_by_external_id(project.id, clip_id)
        if clip is None:
            raise ClipValidationError(f"Unknown clip_id: {clip_id}")

        clip.render_status = str(render_result.get("status") or "completed")
        clip.raw_outputs = list(render_result.get("raw_outputs") or [])
        clip.subtitled_outputs = list(render_result.get("subtitled_outputs") or [])
        clip.last_render_output_dir = str(render_result.get("output_dir") or "")
        clip.last_render_warnings = list(render_result.get("warnings") or [])
        clip_repo.touch(clip)
        ProjectRepository(session).touch(project)
        record_render_artifacts(session, clip, render_result)
        return clip_to_dict(clip)
