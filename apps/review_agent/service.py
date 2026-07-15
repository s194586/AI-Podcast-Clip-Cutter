from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from apps.api.services.clip_service import load_clips
from apps.api.services.clips import ClipValidationError, find_clip
from apps.api.services.project_state import PROJECT_ROOT

from .graph import ReviewAgentRuntime
from .schemas import ClipReviewEvaluation, ReviewMode
from .state import ReviewAgentState
from .tools import evaluate_quality_local


class ClipReviewError(RuntimeError):
    pass


class ClipReviewNotFoundError(ClipReviewError):
    pass


def normalize_review_mode(value: str | None = None) -> ReviewMode:
    normalized = str(value or os.environ.get("CLIP_REVIEW_MODE") or "local_only").strip().lower()
    if normalized not in {"local_only", "llm_optional"}:
        return "local_only"
    return normalized  # type: ignore[return-value]


class LocalHeuristicEvaluator:
    def evaluate(self, state: ReviewAgentState) -> dict[str, Any]:
        return evaluate_quality_local(state)


class OptionalLLMReviewEvaluator:
    """Best-effort LLM adapter with deterministic fallback.

    The project does not require an LLM client for local development. If the
    optional OpenAI client is unavailable or the model call fails, the local
    heuristic result is returned with a warning.
    """

    def __init__(self, fallback: LocalHeuristicEvaluator | None = None) -> None:
        self.fallback = fallback or LocalHeuristicEvaluator()

    def evaluate(self, state: ReviewAgentState) -> dict[str, Any]:
        local_result = self.fallback.evaluate(state)
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return local_result

        try:
            from openai import OpenAI  # type: ignore
        except Exception:
            return _with_warning(local_result, "LLM review requested but the optional OpenAI client is not installed.")

        prompt = _build_llm_prompt(state, local_result)
        try:
            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=os.environ.get("CLIP_REVIEW_MODEL", "gpt-4.1-mini"),
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You review podcast clip candidates. Return strict JSON with scores from 0 to 1, "
                            "needs_more_context, reasons, and warnings. Do not invent facts."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                timeout=20,
            )
            content = response.choices[0].message.content or "{}"
            parsed = json.loads(content)
        except Exception as exc:
            return _with_warning(local_result, f"LLM review failed; local fallback used: {exc}")

        return _merge_llm_result(local_result, parsed)


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
        evaluator = OptionalLLMReviewEvaluator() if self.mode == "llm_optional" else LocalHeuristicEvaluator()
        self.runtime = ReviewAgentRuntime(project_root=self.project_root, evaluator=evaluator, use_langgraph=use_langgraph)

    def review_clip(self, *, clip_id: str, project_id: int | None = None) -> dict[str, Any]:
        initial_state: ReviewAgentState = {"clip_id": str(clip_id), "max_context_expansions": 1}
        if project_id is not None:
            initial_state["project_id"] = int(project_id)
        try:
            completed = self.runtime.run(initial_state)
        except LookupError as exc:
            raise ClipReviewNotFoundError(str(exc)) from exc
        except Exception as exc:
            raise ClipReviewError(str(exc)) from exc

        result = dict(completed["result"])
        return _dump_model(ClipReviewEvaluation(**result))

    def get_latest_review(self, *, clip_id: str, project_id: int | None = None) -> dict[str, Any]:
        try:
            resolved_project_id = self._resolve_project_id_for_clip(clip_id, project_id)
        except LookupError as exc:
            raise ClipReviewNotFoundError(str(exc)) from exc
        latest = self.runtime.latest_evaluation(resolved_project_id, clip_id)
        if latest is None:
            raise ClipReviewNotFoundError(f"No review evaluation exists for clip_id: {clip_id}")
        return _dump_model(ClipReviewEvaluation(**latest))

    def _resolve_project_id_for_clip(self, clip_id: str, project_id: int | None) -> int:
        try:
            clips = load_clips(project_id=project_id, project_root=self.project_root)
            clip = find_clip(clips, clip_id)
        except ClipValidationError as exc:
            raise LookupError(str(exc)) from exc
        return int(clip["project_id"])


def _with_warning(result: dict[str, Any], warning: str) -> dict[str, Any]:
    merged = dict(result)
    merged["warnings"] = list(merged.get("warnings") or []) + [warning]
    return merged


def _dump_model(model: ClipReviewEvaluation) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _build_llm_prompt(state: ReviewAgentState, local_result: dict[str, Any]) -> str:
    clip = state.get("clip") or {}
    context = state.get("context") or {}
    payload = {
        "clip": {
            "id": state.get("clip_id"),
            "start": clip.get("edited_start"),
            "end": clip.get("edited_end"),
            "local_score": clip.get("local_score"),
            "summary": clip.get("summary"),
            "text": clip.get("text"),
        },
        "context": {
            "before_text": context.get("before_text"),
            "clip_text": context.get("clip_text"),
            "after_text": context.get("after_text"),
        },
        "local_result": local_result,
        "allowed_keys": [
            "quality_score",
            "context_score",
            "hook_score",
            "payoff_score",
            "boundary_score",
            "needs_more_context",
            "reasons",
            "warnings",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _merge_llm_result(local_result: dict[str, Any], llm_result: dict[str, Any]) -> dict[str, Any]:
    merged = dict(local_result)
    for key in ("quality_score", "context_score", "hook_score", "payoff_score", "boundary_score"):
        if key in llm_result:
            try:
                merged[key] = round(max(0.0, min(1.0, float(llm_result[key]))), 2)
            except (TypeError, ValueError):
                pass
    if "needs_more_context" in llm_result:
        merged["needs_more_context"] = bool(llm_result["needs_more_context"])
    if isinstance(llm_result.get("reasons"), list):
        merged["reasons"] = [str(item) for item in llm_result["reasons"] if str(item).strip()]
    if isinstance(llm_result.get("warnings"), list):
        merged["warnings"] = list(merged.get("warnings") or []) + [
            str(item) for item in llm_result["warnings"] if str(item).strip()
        ]
    return merged
