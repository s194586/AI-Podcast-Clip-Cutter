from __future__ import annotations

import inspect
import time
from collections.abc import Callable
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

from .config import ReviewConfig, ReviewConfigError, load_review_config, normalize_review_mode_value
from .context import build_clip_transcript_context
from .providers import (
    GeminiBoundaryReviewer,
    LocalStubBoundaryReviewer,
    ReviewProviderCancelledError,
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


class ClipReviewCancelledError(ClipReviewError):
    status_code = 409


class ReviewBatchTimeoutError(ClipReviewError):
    status_code = 504


class BoundaryOptionSelectionError(ReviewProviderError):
    pass


BOUNDARY_VALIDATION_FAILURE_MESSAGE = (
    "Gemini returned boundaries outside the permitted clip range. "
    "This clip requires manual review."
)


def normalize_review_mode(value: str | None = None) -> ReviewMode:
    try:
        if value is not None:
            return normalize_review_mode_value(value)
        return load_review_config(project_root=PROJECT_ROOT, require_api_key=False).mode
    except ReviewConfigError as exc:
        raise ClipReviewConfigurationError(str(exc)) from exc


def configured_gemini_model() -> str:
    try:
        return load_review_config(project_root=PROJECT_ROOT, require_api_key=False).gemini_model
    except ReviewConfigError as exc:
        raise ClipReviewConfigurationError(str(exc)) from exc


def configured_context_seconds() -> float:
    try:
        return load_review_config(project_root=PROJECT_ROOT, require_api_key=False).context_seconds
    except ReviewConfigError as exc:
        raise ClipReviewConfigurationError(str(exc)) from exc


class ReviewAgentService:
    def __init__(
        self,
        *,
        project_root: Path = PROJECT_ROOT,
        mode: str | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        try:
            self.config: ReviewConfig = load_review_config(
                project_root=self.project_root,
                mode=mode,
                require_api_key=False,
            )
        except ReviewConfigError as exc:
            raise ClipReviewConfigurationError(str(exc)) from exc
        self.mode = self.config.mode

    @property
    def provider_name(self) -> str:
        return self.config.provider

    @property
    def model_name(self) -> str:
        return self.config.model

    def review_clip(
        self,
        *,
        clip_id: str,
        project_id: int | None = None,
        apply_safe_suggestions: bool = True,
        cancellation_check: Callable[[], bool] | None = None,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        _raise_if_cancelled(cancellation_check)
        _raise_if_deadline_expired(deadline)
        try:
            project, clip = self._load_project_and_clip(project_id=project_id, clip_id=clip_id)
        except LookupError as exc:
            raise ClipReviewNotFoundError(str(exc)) from exc

        context_seconds = self.config.context_seconds
        transcript_path = self._resolve_transcript_path(project.get("transcript_path"))
        context = build_clip_transcript_context(
            transcript_path,
            float(clip["ai_start"]),
            float(clip["ai_end"]),
            context_seconds=context_seconds,
            clip_id=str(clip["id"]),
            allowed_start_min=float(clip["min_start"]),
            allowed_start_max=float(clip["max_start"]),
            allowed_end_min=float(clip["min_end"]),
            allowed_end_max=float(clip["max_end"]),
            min_duration_seconds=MIN_EDITED_DURATION_SECONDS,
            max_duration_seconds=MAX_EDITED_DURATION_SECONDS,
        )
        provider = self._create_provider(
            request_timeout_seconds=_remaining_request_timeout(
                self.config.request_timeout_seconds,
                deadline,
            )
        )

        def call_provider(corrective_message: str | None = None) -> GeminiBoundaryDecision:
            _raise_if_cancelled(cancellation_check)
            timeout_seconds = _remaining_request_timeout(
                self.config.request_timeout_seconds,
                deadline,
            )
            try:
                return _call_provider_review(
                    provider,
                    context,
                    corrective_message=corrective_message,
                    timeout_seconds=timeout_seconds,
                    cancellation_check=cancellation_check,
                )
            except ReviewProviderCancelledError as exc:
                raise ClipReviewCancelledError("Boundary review cancelled by user.") from exc

        debug_metadata = _debug_metadata()
        try:
            decision = call_provider()
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
        except (ReviewProviderOutputError, BoundaryOptionSelectionError) as exc:
            debug_metadata["retry_used"] = True
            debug_metadata["provider_attempt_count"] = 2
            debug_metadata["first_attempt_validation_error"] = str(exc)
            try:
                retry_decision = call_provider(
                    _boundary_retry_message(context, exc),
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
                    failure_category=_failure_category(retry_exc),
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

        _raise_if_cancelled(cancellation_check)
        _raise_if_deadline_expired(deadline)
        saved = save_evaluation(result)
        return saved

    def review_project_clips(
        self,
        *,
        project_id: int,
        apply_safe_suggestions: bool = True,
        progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
        cancellation_check: Callable[[], bool] | None = None,
        skip_completed: bool = False,
    ) -> dict[str, Any]:
        self._ensure_provider_configuration()
        try:
            clips = load_clips(project_id=project_id, project_root=self.project_root)
        except ClipValidationError as exc:
            raise ClipReviewNotFoundError(str(exc)) from exc

        deadline = time.monotonic() + self.config.batch_timeout_seconds
        results: list[dict[str, Any]] = []
        total = len(clips)
        for index, clip in enumerate(clips, start=1):
            _raise_if_cancelled(cancellation_check)
            _raise_if_deadline_expired(deadline)
            clip_id = str(clip["id"])
            _notify_progress(
                progress_callback,
                "review_clip_started",
                clip_id=clip_id,
                index=index,
                total=total,
                provider=self.provider_name,
            )
            if skip_completed and _clip_review_is_complete(clip):
                result = {
                    "clip_id": clip_id,
                    "provider": str(clip.get("latest_review_provider") or self.provider_name),
                    "decision": str(clip.get("latest_review_decision") or "already_reviewed"),
                    "retry_used": False,
                    "failed": False,
                    "skipped": True,
                }
            else:
                result = self.review_clip(
                    project_id=project_id,
                    clip_id=clip_id,
                    apply_safe_suggestions=apply_safe_suggestions,
                    cancellation_check=cancellation_check,
                    deadline=deadline,
                )
            results.append(result)
            event_name = "review_clip_completed"
            if bool(result.get("failed")):
                event_name = "review_clip_failed"
            elif str(result.get("decision") or "") == "manual_review":
                event_name = "review_clip_manual"
            _notify_progress(
                progress_callback,
                event_name,
                clip_id=clip_id,
                index=index,
                total=total,
                provider=str(result.get("provider") or self.provider_name),
                decision=str(result.get("decision") or "unknown"),
                retry_used=bool(result.get("retry_used")),
            )
            _raise_if_cancelled(cancellation_check)
            if index < total:
                _raise_if_deadline_expired(deadline)
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

    def _create_provider(self, *, request_timeout_seconds: float | None = None) -> Any:
        self._ensure_provider_configuration()
        if self.mode == "gemini":
            options: dict[str, Any] = {
                "api_key": str(self.config.api_key or ""),
                "model": self.model_name,
            }
            if _accepts_keyword(GeminiBoundaryReviewer, "request_timeout_seconds"):
                options["request_timeout_seconds"] = (
                    request_timeout_seconds or self.config.request_timeout_seconds
                )
            return GeminiBoundaryReviewer(**options)
        return LocalStubBoundaryReviewer()

    def _ensure_provider_configuration(self) -> None:
        try:
            self.config.require_ready()
        except ReviewConfigError as exc:
            raise ClipReviewConfigurationError(str(exc)) from exc

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
        failure_category: str | None = None,
    ) -> dict[str, Any]:
        warnings = [str(warning)]
        debug = _debug_metadata(debug_metadata)
        reasoning_summary = (
            BOUNDARY_VALIDATION_FAILURE_MESSAGE
            if failure_category == "boundary_validation"
            else "Boundary review could not be safely applied."
        )
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
            "failure_category": failure_category,
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
                "failure_category": failure_category,
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
    _ensure_allowed_pair(context, decision)
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
    start_option = start_options[start_index]
    end_option = end_options[end_index]
    segments = {
        str(segment["segment_id"]): segment
        for key in ("context_before", "candidate_segments", "context_after")
        for segment in context.get(key) or []
    }
    for option, boundary in ((start_option, "start"), (end_option, "end")):
        segment = segments.get(str(option.get("segment_id") or ""))
        if segment is None:
            raise BoundaryOptionSelectionError(
                f"Gemini selected a {boundary} option that does not map to a transcript segment."
            )
        if abs(float(option[boundary]) - float(segment[boundary])) > 0.01:
            raise BoundaryOptionSelectionError(
                f"Gemini selected a {boundary} option that does not match its transcript segment boundary."
            )
    return start_option, end_option


def _derive_segment_ids_for_reject(
    context: dict[str, Any],
    decision: GeminiBoundaryDecision,
) -> tuple[str | None, str | None]:
    start_option, end_option = _selected_options(context, decision)
    _ensure_allowed_pair(context, decision)
    return str(start_option["segment_id"]), str(end_option["segment_id"])


def _ensure_allowed_pair(
    context: dict[str, Any],
    decision: GeminiBoundaryDecision,
) -> None:
    selected_pair = (
        int(decision.selected_start_option_index),
        int(decision.selected_end_option_index),
    )
    if selected_pair not in _allowed_pair_indexes(context):
        raise BoundaryOptionSelectionError(
            "Gemini selected a boundary pair that is not present in allowed_boundary_pairs."
        )


def _allowed_pair_indexes(context: dict[str, Any]) -> set[tuple[int, int]]:
    indexes: set[tuple[int, int]] = set()
    for pair in context.get("allowed_boundary_pairs") or []:
        try:
            indexes.add(
                (
                    int(pair["start_option_index"]),
                    int(pair["end_option_index"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return indexes


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
    *,
    timeout_seconds: float | None = None,
    cancellation_check: Callable[[], bool] | None = None,
) -> GeminiBoundaryDecision:
    options: dict[str, Any] = {}
    review_method = provider.review
    if corrective_message is not None and _accepts_keyword(review_method, "corrective_message"):
        options["corrective_message"] = corrective_message
    if timeout_seconds is not None and _accepts_keyword(review_method, "timeout_seconds"):
        options["timeout_seconds"] = timeout_seconds
    if cancellation_check is not None and _accepts_keyword(review_method, "cancellation_check"):
        options["cancellation_check"] = cancellation_check
    return review_method(context, **options)


def _accepts_keyword(callable_value: Any, keyword: str) -> bool:
    try:
        signature = inspect.signature(callable_value)
    except (TypeError, ValueError):
        return False
    return keyword in signature.parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


def _raise_if_cancelled(cancellation_check: Callable[[], bool] | None) -> None:
    if cancellation_check is not None and cancellation_check():
        raise ClipReviewCancelledError("Boundary review cancelled by user.")


def _raise_if_deadline_expired(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise ReviewBatchTimeoutError("Automatic boundary review exceeded its batch timeout.")


def _remaining_request_timeout(configured_timeout: float, deadline: float | None) -> float:
    timeout = max(0.001, float(configured_timeout))
    if deadline is None:
        return timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ReviewBatchTimeoutError("Automatic boundary review exceeded its batch timeout.")
    return max(0.001, min(timeout, remaining))


def _notify_progress(
    callback: Callable[[str, dict[str, Any]], None] | None,
    event_name: str,
    **metadata: Any,
) -> None:
    if callback is not None:
        callback(event_name, metadata)


def _clip_review_is_complete(clip: dict[str, Any]) -> bool:
    if str(clip.get("boundary_source") or "") == "user":
        return True
    if str(clip.get("status") or "") in {"accepted", "rejected"}:
        return True
    decision = str(clip.get("latest_review_decision") or "")
    return bool(decision and decision != "manual_review" and not clip.get("latest_review_failed"))


def _boundary_retry_message(
    context: dict[str, Any],
    error: Exception | str,
) -> str:
    return (
        f"{_concise_boundary_correction(error)} You must return selected_start_option_index and "
        "selected_end_option_index as non-null "
        "integers. Choose the start index only from START OPTIONS and the end index only from END OPTIONS. "
        "Choose one exact pair from allowed_boundary_pairs. "
        f"Valid start option indexes: {_format_option_indexes(_option_map(context.get('start_boundary_options') or []))}. "
        f"Valid end option indexes: {_format_option_indexes(_option_map(context.get('end_boundary_options') or []))}. "
        f"For reject, use current_aligned_start_option_index={context.get('current_aligned_start_option_index')} "
        f"and current_aligned_end_option_index={context.get('current_aligned_end_option_index')}."
    )


def _concise_boundary_correction(error: Exception | str) -> str:
    message = str(error).casefold()
    if "must not exceed" in message or "duration" in message and "90" in message:
        return "The selected boundary pair is invalid because its duration exceeds 90 seconds."
    if "end must stay" in message or "end after the allowed" in message:
        return "The selected end is outside the permitted clip range."
    if "start must stay" in message or "start before the allowed" in message:
        return "The selected start is outside the permitted clip range."
    if "reversed" in message or "end must be greater" in message:
        return "The selected boundary pair is invalid because the end must be after the start."
    if "allowed_boundary_pairs" in message or "boundary pair" in message:
        return "The selected boundary pair is not allowed."
    if "unknown start option" in message or "unknown end option" in message:
        return "The selected boundary option index is not available."
    return "The prior structured boundary response was invalid."


def _failure_category(error: Exception) -> str:
    if isinstance(error, BoundaryOptionSelectionError):
        return "boundary_validation"
    if isinstance(error, ReviewProviderOutputError):
        return "structured_output"
    return "provider"


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
