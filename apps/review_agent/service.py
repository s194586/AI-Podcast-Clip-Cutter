from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from apps.api.db.database import init_database, session_scope
from apps.api.db.repositories import ProjectRepository
from apps.api.services.clip_service import load_clips
from apps.api.services.clips import (
    MAX_EDITED_DURATION_SECONDS,
    MIN_EDITED_DURATION_SECONDS,
    ClipValidationError,
    find_clip,
    validate_adjusted_bounds,
)
from apps.api.services.project_service import project_to_dict
from apps.api.services.project_state import PROJECT_ROOT

from .context import DEFAULT_REVIEW_CONTEXT_SECONDS, build_clip_transcript_context
from .providers import (
    DEFAULT_GEMINI_MODEL,
    GeminiBoundaryReviewer,
    LocalStubBoundaryReviewer,
    ReviewProviderError,
    ReviewProviderOutputError,
)
from .schemas import GeminiBoundaryDecision, ReviewMode
from .tools import get_latest_evaluation, save_evaluation


class ClipReviewError(RuntimeError):
    status_code = 422


class ClipReviewConfigurationError(ClipReviewError):
    status_code = 503


class ClipReviewNotFoundError(ClipReviewError):
    status_code = 404


class BoundaryOptionSelectionError(ReviewProviderError):
    pass


def normalize_review_mode(value: str | None = None) -> ReviewMode:
    raw_value = str(value or os.environ.get("CLIP_REVIEW_MODE") or "local_stub").strip().lower()
    aliases = {
        "local_only": "local_stub",
        "stub": "local_stub",
    }
    normalized = aliases.get(raw_value, raw_value)
    if normalized not in {"local_stub", "gemini"}:
        raise ClipReviewConfigurationError(
            f"Unsupported CLIP_REVIEW_MODE={raw_value!r}. Use 'local_stub' or 'gemini'."
        )
    return normalized  # type: ignore[return-value]


def configured_gemini_model() -> str:
    return str(os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL


def configured_context_seconds() -> float:
    raw_value = os.environ.get("CLIP_REVIEW_CONTEXT_SECONDS")
    if raw_value in (None, ""):
        return DEFAULT_REVIEW_CONTEXT_SECONDS
    try:
        return max(0.0, float(raw_value))
    except ValueError as exc:
        raise ClipReviewConfigurationError(
            f"CLIP_REVIEW_CONTEXT_SECONDS must be a number, got {raw_value!r}."
        ) from exc


class ReviewAgentService:
    def __init__(
        self,
        *,
        project_root: Path = PROJECT_ROOT,
        mode: str | None = None,
        use_langgraph: bool = True,
    ) -> None:
        self.project_root = Path(project_root)
        self.mode = normalize_review_mode(mode)
        self.use_langgraph = use_langgraph

    @property
    def provider_name(self) -> str:
        return "gemini" if self.mode == "gemini" else "local_stub"

    @property
    def model_name(self) -> str:
        return configured_gemini_model() if self.mode == "gemini" else "local_stub"

    def review_clip(
        self,
        *,
        clip_id: str,
        project_id: int | None = None,
        apply_safe_suggestions: bool = True,
    ) -> dict[str, Any]:
        try:
            project, clip = self._load_project_and_clip(project_id=project_id, clip_id=clip_id)
        except LookupError as exc:
            raise ClipReviewNotFoundError(str(exc)) from exc

        context_seconds = configured_context_seconds()
        transcript_path = self._resolve_transcript_path(project.get("transcript_path"))
        context = build_clip_transcript_context(
            transcript_path,
            float(clip["ai_start"]),
            float(clip["ai_end"]),
            context_seconds=context_seconds,
            clip_id=str(clip["id"]),
        )
        provider = self._create_provider()

        debug_metadata = _debug_metadata()
        try:
            decision = _call_provider_review(provider, context)
            try:
                result = self._result_from_decision(
                    project_id=int(project["id"]),
                    clip=clip,
                    context=context,
                    decision=decision,
                    provider=provider.provider,
                    model=provider.model,
                    apply_safe_suggestions=apply_safe_suggestions,
                    debug_metadata=debug_metadata,
                )
            except BoundaryOptionSelectionError as exc:
                debug_metadata["retry_used"] = True
                debug_metadata["provider_attempt_count"] = 2
                debug_metadata["first_attempt_validation_error"] = str(exc)
                retry_decision = _call_provider_review(
                    provider,
                    context,
                    corrective_message=_boundary_retry_message(context, str(exc)),
                )
                try:
                    result = self._result_from_decision(
                        project_id=int(project["id"]),
                        clip=clip,
                        context=context,
                        decision=retry_decision,
                        provider=provider.provider,
                        model=provider.model,
                        apply_safe_suggestions=apply_safe_suggestions,
                        debug_metadata=debug_metadata,
                    )
                except ReviewProviderError as retry_exc:
                    debug_metadata["final_validation_error"] = str(retry_exc)
                    result = self._failed_result(
                        project_id=int(project["id"]),
                        clip=clip,
                        context=context,
                        provider=provider.provider,
                        model=provider.model,
                        warning=str(retry_exc),
                        apply_safe_suggestions=apply_safe_suggestions,
                        debug_metadata=debug_metadata,
                    )
            except ReviewProviderOutputError as exc:
                debug_metadata["retry_used"] = True
                debug_metadata["provider_attempt_count"] = 2
                debug_metadata["first_attempt_validation_error"] = str(exc)
                try:
                    retry_decision = _call_provider_review(
                        provider,
                        context,
                        corrective_message=_boundary_retry_message(context, str(exc)),
                    )
                    result = self._result_from_decision(
                        project_id=int(project["id"]),
                        clip=clip,
                        context=context,
                        decision=retry_decision,
                        provider=provider.provider,
                        model=provider.model,
                        apply_safe_suggestions=apply_safe_suggestions,
                        debug_metadata=debug_metadata,
                    )
                except ReviewProviderError as retry_exc:
                    debug_metadata["final_validation_error"] = str(retry_exc)
                    result = self._failed_result(
                        project_id=int(project["id"]),
                        clip=clip,
                        context=context,
                        provider=provider.provider,
                        model=provider.model,
                        warning=str(retry_exc),
                        apply_safe_suggestions=apply_safe_suggestions,
                        debug_metadata=debug_metadata,
                    )
        except ReviewProviderOutputError as exc:
            debug_metadata["retry_used"] = True
            debug_metadata["provider_attempt_count"] = 2
            debug_metadata["first_attempt_validation_error"] = str(exc)
            try:
                retry_decision = _call_provider_review(
                    provider,
                    context,
                    corrective_message=_boundary_retry_message(context, str(exc)),
                )
                result = self._result_from_decision(
                    project_id=int(project["id"]),
                    clip=clip,
                    context=context,
                    decision=retry_decision,
                    provider=provider.provider,
                    model=provider.model,
                    apply_safe_suggestions=apply_safe_suggestions,
                    debug_metadata=debug_metadata,
                )
            except ReviewProviderError as retry_exc:
                debug_metadata["final_validation_error"] = str(retry_exc)
                result = self._failed_result(
                    project_id=int(project["id"]),
                    clip=clip,
                    context=context,
                    provider=provider.provider,
                    model=provider.model,
                    warning=str(retry_exc),
                    apply_safe_suggestions=apply_safe_suggestions,
                    debug_metadata=debug_metadata,
                )
        except ReviewProviderError as exc:
            debug_metadata["final_validation_error"] = str(exc)
            result = self._failed_result(
                project_id=int(project["id"]),
                clip=clip,
                context=context,
                provider=provider.provider,
                model=provider.model,
                warning=str(exc),
                apply_safe_suggestions=apply_safe_suggestions,
                debug_metadata=debug_metadata,
            )
        except ClipReviewError:
            raise
        except Exception as exc:
            result = self._failed_result(
                project_id=int(project["id"]),
                clip=clip,
                context=context,
                provider=provider.provider,
                model=provider.model,
                warning=f"Review validation failed: {exc}",
                apply_safe_suggestions=apply_safe_suggestions,
                debug_metadata=debug_metadata,
            )

        saved = save_evaluation(result)
        return saved

    def review_project_clips(
        self,
        *,
        project_id: int,
        apply_safe_suggestions: bool = True,
    ) -> dict[str, Any]:
        self._ensure_provider_configuration()
        try:
            clips = load_clips(project_id=project_id, project_root=self.project_root)
        except ClipValidationError as exc:
            raise ClipReviewNotFoundError(str(exc)) from exc

        results = [
            self.review_clip(
                project_id=project_id,
                clip_id=str(clip["id"]),
                apply_safe_suggestions=apply_safe_suggestions,
            )
            for clip in clips
        ]
        return self._batch_summary(project_id=project_id, results=results)

    def get_latest_review(self, *, clip_id: str, project_id: int | None = None) -> dict[str, Any]:
        try:
            resolved_project_id = self._resolve_project_id_for_clip(clip_id, project_id)
        except LookupError as exc:
            raise ClipReviewNotFoundError(str(exc)) from exc
        latest = get_latest_evaluation(resolved_project_id, clip_id)
        if latest is None:
            raise ClipReviewNotFoundError(f"No review evaluation exists for clip_id: {clip_id}")
        return latest

    def _create_provider(self) -> Any:
        self._ensure_provider_configuration()
        if self.mode == "gemini":
            return GeminiBoundaryReviewer(api_key=str(os.environ.get("GEMINI_API_KEY") or ""), model=self.model_name)
        return LocalStubBoundaryReviewer()

    def _ensure_provider_configuration(self) -> None:
        if self.mode == "gemini" and not str(os.environ.get("GEMINI_API_KEY") or "").strip():
            raise ClipReviewConfigurationError(
                "CLIP_REVIEW_MODE=gemini requires GEMINI_API_KEY. Set GEMINI_API_KEY to enable real Gemini review."
            )

    def _load_project_and_clip(self, *, project_id: int | None, clip_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            clips = load_clips(project_id=project_id, project_root=self.project_root)
            clip = find_clip(clips, str(clip_id))
        except ClipValidationError as exc:
            raise LookupError(str(exc)) from exc

        resolved_project_id = int(clip["project_id"])
        init_database()
        with session_scope() as session:
            project = ProjectRepository(session).get(resolved_project_id)
            if project is None:
                raise LookupError(f"Unknown project_id: {resolved_project_id}")
            return project_to_dict(project), clip

    def _resolve_project_id_for_clip(self, clip_id: str, project_id: int | None) -> int:
        try:
            _project, clip = self._load_project_and_clip(project_id=project_id, clip_id=clip_id)
        except LookupError as exc:
            raise LookupError(str(exc)) from exc
        return int(clip["project_id"])

    def _resolve_transcript_path(self, stored_path: str | None) -> Path | None:
        candidates: list[Path] = []
        if stored_path:
            stored = Path(stored_path)
            candidates.append(stored if stored.is_absolute() else self.project_root / stored)
        candidates.append(self.project_root / "transcripts" / "final_transcript.json")
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0] if candidates else None

    def _result_from_decision(
        self,
        *,
        project_id: int,
        clip: dict[str, Any],
        context: dict[str, Any],
        decision: GeminiBoundaryDecision,
        provider: str,
        model: str,
        apply_safe_suggestions: bool,
        debug_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if decision.decision in {"render_ready", "adjust_boundaries"}:
            reviewed_start, reviewed_end, selected_start_id, selected_end_id = _validated_reviewed_bounds(
                context=context,
                clip=clip,
                decision=decision,
            )
        else:
            reviewed_start = None
            reviewed_end = None
            selected_start_id, selected_end_id = _derive_segment_ids_for_reject(context, decision)

        start_delta = _delta(reviewed_start, clip.get("ai_start"))
        end_delta = _delta(reviewed_end, clip.get("ai_end"))
        debug = _debug_metadata(debug_metadata)
        reasoning_summary = str(decision.reasoning_summary or "").strip()
        result = {
            "project_id": project_id,
            "clip_id": str(clip["id"]),
            "database_clip_id": clip.get("database_id"),
            "provider": provider,
            "model": model,
            "decision": decision.decision,
            "recommended_action": decision.decision,
            "selected_start_option_index": int(decision.selected_start_option_index),
            "selected_end_option_index": int(decision.selected_end_option_index),
            "selected_start_segment_id": selected_start_id,
            "selected_end_segment_id": selected_end_id,
            "suggested_start": reviewed_start,
            "suggested_end": reviewed_end,
            "reviewed_start": reviewed_start,
            "reviewed_end": reviewed_end,
            "start_delta_seconds": start_delta,
            "end_delta_seconds": end_delta,
            "reasoning_summary": reasoning_summary,
            "start_reason": str(decision.start_reason or "").strip(),
            "end_reason": str(decision.end_reason or "").strip(),
            "needs_more_context": False,
            "reasons": [reasoning_summary] if reasoning_summary else [],
            "warnings": [str(item) for item in decision.warnings or [] if str(item).strip()],
            "retry_used": bool(debug["retry_used"]),
            "provider_attempt_count": int(debug["provider_attempt_count"]),
            "first_attempt_validation_error": debug.get("first_attempt_validation_error"),
            "final_validation_error": debug.get("final_validation_error"),
            "context_seconds": float(context.get("context_seconds") or 0.0),
            "apply_safe_suggestions": bool(apply_safe_suggestions),
            "raw_result": _raw_result(
                provider=provider,
                model=model,
                context=context,
                decision=decision,
                reviewed_start=reviewed_start,
                reviewed_end=reviewed_end,
                start_delta=start_delta,
                end_delta=end_delta,
                selected_start_segment_id=selected_start_id,
                selected_end_segment_id=selected_end_id,
                failed=False,
                debug_metadata=debug,
            ),
        }
        return result

    def _failed_result(
        self,
        *,
        project_id: int,
        clip: dict[str, Any],
        context: dict[str, Any],
        provider: str,
        model: str,
        warning: str,
        apply_safe_suggestions: bool,
        debug_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        warnings = [str(warning)]
        debug = _debug_metadata(debug_metadata)
        reasoning_summary = "Boundary review could not be safely applied."
        return {
            "project_id": project_id,
            "clip_id": str(clip["id"]),
            "database_clip_id": clip.get("database_id"),
            "provider": provider,
            "model": model,
            "decision": "manual_review",
            "recommended_action": "manual_review",
            "selected_start_segment_id": None,
            "selected_end_segment_id": None,
            "suggested_start": None,
            "suggested_end": None,
            "reviewed_start": None,
            "reviewed_end": None,
            "start_delta_seconds": None,
            "end_delta_seconds": None,
            "reasoning_summary": reasoning_summary,
            "start_reason": "The model result was invalid or unavailable.",
            "end_reason": "The model result was invalid or unavailable.",
            "needs_more_context": False,
            "reasons": [reasoning_summary],
            "warnings": warnings,
            "failure_reason": str(warning),
            "retry_used": bool(debug["retry_used"]),
            "provider_attempt_count": int(debug["provider_attempt_count"]),
            "first_attempt_validation_error": debug.get("first_attempt_validation_error"),
            "final_validation_error": debug.get("final_validation_error") or str(warning),
            "context_seconds": float(context.get("context_seconds") or 0.0),
            "apply_safe_suggestions": bool(apply_safe_suggestions),
            "raw_result": {
                "provider": provider,
                "model": model,
                "decision": "manual_review",
                "failed": True,
                "failure_reason": str(warning),
                "retry_used": bool(debug["retry_used"]),
                "provider_attempt_count": int(debug["provider_attempt_count"]),
                "first_attempt_validation_error": debug.get("first_attempt_validation_error"),
                "final_validation_error": debug.get("final_validation_error") or str(warning),
                "context_seconds": float(context.get("context_seconds") or 0.0),
                "context_summary": _context_summary(context),
            },
        }

    def _batch_summary(self, *, project_id: int, results: list[dict[str, Any]]) -> dict[str, Any]:
        failed_count = sum(1 for result in results if bool(result.get("failed")))
        return {
            "project_id": int(project_id),
            "provider": self.provider_name,
            "model": self.model_name,
            "clip_count": len(results),
            "success_count": len(results) - failed_count,
            "render_ready_count": _count_decision(results, "render_ready"),
            "adjust_boundaries_count": _count_decision(results, "adjust_boundaries"),
            "reject_count": _count_decision(results, "reject"),
            "manual_review_count": _count_decision(results, "manual_review"),
            "failed_count": failed_count,
            "reviews": results,
        }


def _validated_reviewed_bounds(
    *,
    context: dict[str, Any],
    clip: dict[str, Any],
    decision: GeminiBoundaryDecision,
) -> tuple[float, float, str, str]:
    start_option, end_option = _selected_options(context, decision)
    selected_start_id = str(start_option["segment_id"])
    selected_end_id = str(end_option["segment_id"])
    reviewed_start = round(float(start_option["start"]), 2)
    reviewed_end = round(float(end_option["end"]), 2)
    if reviewed_start >= reviewed_end:
        raise BoundaryOptionSelectionError("Gemini returned reversed or zero-length boundaries.")
    if reviewed_start < float(context.get("earliest_allowed_start", reviewed_start)):
        raise BoundaryOptionSelectionError("Gemini returned a start before the allowed context.")
    if reviewed_end > float(context.get("latest_allowed_end", reviewed_end)):
        raise BoundaryOptionSelectionError("Gemini returned an end after the allowed context.")

    try:
        validate_adjusted_bounds(clip, reviewed_start, reviewed_end)
    except Exception as exc:
        raise BoundaryOptionSelectionError(str(exc)) from exc
    duration = reviewed_end - reviewed_start
    if duration < MIN_EDITED_DURATION_SECONDS or duration > MAX_EDITED_DURATION_SECONDS:
        raise BoundaryOptionSelectionError("Gemini returned boundaries outside the editor duration limits.")
    return reviewed_start, reviewed_end, selected_start_id, selected_end_id


def _selected_options(
    context: dict[str, Any],
    decision: GeminiBoundaryDecision,
) -> tuple[dict[str, Any], dict[str, Any]]:
    start_options = _option_map(context.get("start_boundary_options") or [])
    end_options = _option_map(context.get("end_boundary_options") or [])
    start_index = int(decision.selected_start_option_index)
    end_index = int(decision.selected_end_option_index)
    if start_index not in start_options:
        raise BoundaryOptionSelectionError(
            f"Gemini selected unknown start option index {start_index}. "
            f"Valid start option indexes: {_format_option_indexes(start_options)}."
        )
    if end_index not in end_options:
        raise BoundaryOptionSelectionError(
            f"Gemini selected unknown end option index {end_index}. "
            f"Valid end option indexes: {_format_option_indexes(end_options)}."
        )
    return start_options[start_index], end_options[end_index]


def _derive_segment_ids_for_reject(
    context: dict[str, Any],
    decision: GeminiBoundaryDecision,
) -> tuple[str | None, str | None]:
    try:
        start_option, end_option = _selected_options(context, decision)
    except BoundaryOptionSelectionError:
        return None, None
    return str(start_option["segment_id"]), str(end_option["segment_id"])


def _option_map(options: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    mapped: dict[int, dict[str, Any]] = {}
    for option in options:
        try:
            mapped[int(option["option_index"])] = option
        except (KeyError, TypeError, ValueError):
            continue
    return mapped


def _format_option_indexes(options: dict[int, dict[str, Any]]) -> str:
    indexes = sorted(options)
    if not indexes:
        return "none"
    if indexes == list(range(indexes[0], indexes[-1] + 1)):
        return f"{indexes[0]}-{indexes[-1]}"
    return ", ".join(str(index) for index in indexes)


def _call_provider_review(
    provider: Any,
    context: dict[str, Any],
    corrective_message: str | None = None,
) -> GeminiBoundaryDecision:
    if corrective_message is None:
        return provider.review(context)
    try:
        return provider.review(context, corrective_message=corrective_message)
    except TypeError as exc:
        if "corrective_message" not in str(exc):
            raise
        return provider.review(context)


def _boundary_retry_message(
    context: dict[str, Any],
    error_message: str,
) -> str:
    return (
        f"{error_message} You must return selected_start_option_index and selected_end_option_index as non-null "
        "integers. Choose the start index only from START OPTIONS and the end index only from END OPTIONS. "
        f"Valid start option indexes: {_format_option_indexes(_option_map(context.get('start_boundary_options') or []))}. "
        f"Valid end option indexes: {_format_option_indexes(_option_map(context.get('end_boundary_options') or []))}. "
        f"For reject, use current_aligned_start_option_index={context.get('current_aligned_start_option_index')} "
        f"and current_aligned_end_option_index={context.get('current_aligned_end_option_index')}."
    )


def _delta(reviewed_value: Any, original_value: Any) -> float | None:
    if reviewed_value in (None, "") or original_value in (None, ""):
        return None
    return round(float(reviewed_value) - float(original_value), 2)


def _raw_result(
    *,
    provider: str,
    model: str,
    context: dict[str, Any],
    decision: GeminiBoundaryDecision,
    reviewed_start: float | None,
    reviewed_end: float | None,
    start_delta: float | None,
    end_delta: float | None,
    selected_start_segment_id: str | None,
    selected_end_segment_id: str | None,
    failed: bool,
    debug_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    debug = _debug_metadata(debug_metadata)
    return {
        "provider": provider,
        "model": model,
        "decision": decision.decision,
        "selected_start_option_index": int(decision.selected_start_option_index),
        "selected_end_option_index": int(decision.selected_end_option_index),
        "selected_start_segment_id": selected_start_segment_id,
        "selected_end_segment_id": selected_end_segment_id,
        "reviewed_start": reviewed_start,
        "reviewed_end": reviewed_end,
        "start_delta_seconds": start_delta,
        "end_delta_seconds": end_delta,
        "reasoning_summary": decision.reasoning_summary,
        "start_reason": decision.start_reason,
        "end_reason": decision.end_reason,
        "warnings": list(decision.warnings or []),
        "context_seconds": float(context.get("context_seconds") or 0.0),
        "retry_used": bool(debug["retry_used"]),
        "provider_attempt_count": int(debug["provider_attempt_count"]),
        "first_attempt_validation_error": debug.get("first_attempt_validation_error"),
        "final_validation_error": debug.get("final_validation_error"),
        "context_summary": _context_summary(context),
        "failed": bool(failed),
    }


def _debug_metadata(value: dict[str, Any] | None = None) -> dict[str, Any]:
    base = {
        "retry_used": False,
        "provider_attempt_count": 1,
        "first_attempt_validation_error": None,
        "final_validation_error": None,
    }
    if value:
        base.update(value)
    return base


def _context_summary(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "clip_id": context.get("clip_id"),
        "candidate_start": context.get("candidate_start"),
        "candidate_end": context.get("candidate_end"),
        "earliest_allowed_start": context.get("earliest_allowed_start"),
        "latest_allowed_end": context.get("latest_allowed_end"),
        "current_aligned_start_option_index": context.get("current_aligned_start_option_index"),
        "current_aligned_end_option_index": context.get("current_aligned_end_option_index"),
        "current_aligned_start_segment_id": context.get("current_aligned_start_segment_id"),
        "current_aligned_end_segment_id": context.get("current_aligned_end_segment_id"),
        "context_before_segment_ids": [segment.get("segment_id") for segment in context.get("context_before") or []],
        "candidate_segment_ids": [segment.get("segment_id") for segment in context.get("candidate_segments") or []],
        "context_after_segment_ids": [segment.get("segment_id") for segment in context.get("context_after") or []],
        "start_boundary_option_ids": [
            option.get("segment_id") for option in context.get("start_boundary_options") or []
        ],
        "end_boundary_option_ids": [option.get("segment_id") for option in context.get("end_boundary_options") or []],
        "start_boundary_option_indexes": [
            option.get("option_index") for option in context.get("start_boundary_options") or []
        ],
        "end_boundary_option_indexes": [
            option.get("option_index") for option in context.get("end_boundary_options") or []
        ],
    }


def _count_decision(results: list[dict[str, Any]], decision: str) -> int:
    return sum(1 for result in results if result.get("decision") == decision)
