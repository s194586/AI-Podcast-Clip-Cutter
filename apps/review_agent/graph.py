from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from apps.api.db.database import init_database, session_scope
from apps.api.db.repositories import ProjectRepository
from apps.api.services.clip_service import load_clips
from apps.api.services.clips import ClipValidationError, find_clip
from apps.api.services.project_service import project_to_dict
from apps.api.services.project_state import PROJECT_ROOT

from .state import ReviewAgentState
from .tools import (
    check_sensitive_patterns,
    evaluate_quality_local,
    get_candidate_features,
    get_latest_evaluation,
    get_transcript_context,
    load_transcript_segments,
    save_evaluation,
    suggest_boundaries,
    suggest_crop_advice,
)

try:  # pragma: no cover - exercised only when optional dependency is installed
    from langgraph.graph import END, StateGraph
except Exception:  # pragma: no cover - default local test path
    END = "__end__"
    StateGraph = None


class ReviewEvaluatorProtocol(Protocol):
    def evaluate(self, state: ReviewAgentState) -> dict[str, Any]:
        ...


class ReviewAgentRuntime:
    def __init__(
        self,
        *,
        project_root: Path = PROJECT_ROOT,
        evaluator: ReviewEvaluatorProtocol | None = None,
        use_langgraph: bool = True,
    ) -> None:
        self.project_root = Path(project_root)
        self.evaluator = evaluator or LocalEvaluatorAdapter()
        self.use_langgraph = use_langgraph

    def run(self, initial_state: ReviewAgentState) -> ReviewAgentState:
        workflow = build_review_graph(self) if self.use_langgraph and StateGraph is not None else LocalReviewWorkflow(self)
        return workflow.invoke(initial_state)

    def load_candidate(self, state: ReviewAgentState) -> ReviewAgentState:
        project_id = state.get("project_id")
        clip_id = str(state["clip_id"])
        try:
            clips = load_clips(project_id=project_id, project_root=self.project_root)
            clip = find_clip(clips, clip_id)
        except ClipValidationError as exc:
            raise LookupError(str(exc)) from exc

        resolved_project_id = int(clip["project_id"])
        init_database()
        with session_scope() as session:
            project = ProjectRepository(session).get(resolved_project_id)
            if project is None:
                raise LookupError(f"Unknown project_id: {resolved_project_id}")
            project_payload = project_to_dict(project)

        transcript_path = self._resolve_transcript_path(project_payload.get("transcript_path"))
        segments = load_transcript_segments(transcript_path)
        next_state = dict(state)
        next_state.update(
            {
                "project_id": resolved_project_id,
                "clip_id": clip_id,
                "project": project_payload,
                "clip": clip,
                "transcript_path": str(transcript_path) if transcript_path else None,
                "transcript_segments": segments,
                "context_padding_seconds": float(state.get("context_padding_seconds") or 20.0),
                "context_expansions": int(state.get("context_expansions") or 0),
                "max_context_expansions": int(state.get("max_context_expansions") or 1),
                "reasons": list(state.get("reasons") or []),
                "warnings": list(state.get("warnings") or []),
            }
        )
        return next_state

    def retrieve_context(self, state: ReviewAgentState) -> ReviewAgentState:
        clip = state["clip"]
        context = get_transcript_context(
            state.get("transcript_segments") or [],
            float(clip["edited_start"]),
            float(clip["edited_end"]),
            float(state.get("context_padding_seconds") or 20.0),
        )
        if not context.get("clip_text"):
            context["clip_text"] = str(clip.get("text") or clip.get("summary") or "")

        next_state = dict(state)
        next_state["context"] = context
        return next_state

    def evaluate_quality(self, state: ReviewAgentState) -> ReviewAgentState:
        evaluation = self.evaluator.evaluate(state)
        next_state = dict(state)
        next_state.update(
            {
                "quality_score": float(evaluation.get("quality_score", 0.0)),
                "context_score": float(evaluation.get("context_score", 0.0)),
                "hook_score": float(evaluation.get("hook_score", 0.0)),
                "payoff_score": float(evaluation.get("payoff_score", 0.0)),
                "boundary_score": float(evaluation.get("boundary_score", 0.0)),
                "needs_more_context": bool(evaluation.get("needs_more_context", False)),
                "reasons": list(evaluation.get("reasons") or []),
            }
        )
        warnings = list(next_state.get("warnings") or [])
        warnings.extend(str(item) for item in evaluation.get("warnings") or [])
        next_state["warnings"] = warnings
        return next_state

    def route_context_decision(self, state: ReviewAgentState) -> ReviewAgentState:
        return state

    def should_retrieve_more_context(self, state: ReviewAgentState) -> str:
        if bool(state.get("needs_more_context")) and int(state.get("context_expansions") or 0) < int(
            state.get("max_context_expansions") or 1
        ):
            return "retrieve_more_context"
        return "continue"

    def retrieve_more_context(self, state: ReviewAgentState) -> ReviewAgentState:
        next_state = dict(state)
        next_state["context_expansions"] = int(next_state.get("context_expansions") or 0) + 1
        next_state["context_padding_seconds"] = max(45.0, float(next_state.get("context_padding_seconds") or 20.0) * 2.0)
        return self.retrieve_context(next_state)

    def check_privacy(self, state: ReviewAgentState) -> ReviewAgentState:
        context = state.get("context") or {}
        text = " ".join(
            [
                str(context.get("before_text") or ""),
                str(context.get("clip_text") or ""),
                str(context.get("after_text") or ""),
            ]
        )
        sensitive = check_sensitive_patterns(text)
        warnings = list(state.get("warnings") or [])
        if sensitive["privacy_risk"] != "low":
            warnings.append(f"Potential privacy risk: {sensitive['privacy_risk']}.")
        for match in sensitive.get("matches") or []:
            warnings.append(f"Sensitive pattern detected ({match['type']}): {match['text']}")
        next_state = dict(state)
        next_state["sensitive_check"] = sensitive
        next_state["warnings"] = warnings
        return next_state

    def suggest_boundaries(self, state: ReviewAgentState) -> ReviewAgentState:
        suggestion = suggest_boundaries(state.get("context") or {}, state["clip"])
        next_state = dict(state)
        next_state["boundary_suggestion"] = suggestion

        clip = state["clip"]
        start_delta = abs(float(suggestion["suggested_start"]) - float(clip["edited_start"]))
        end_delta = abs(float(suggestion["suggested_end"]) - float(clip["edited_end"]))
        reasons = list(state.get("reasons") or [])
        if start_delta > 0.25:
            reasons.append(suggestion["start_advice"])
        if end_delta > 0.25:
            reasons.append(suggestion["end_advice"])
        next_state["reasons"] = reasons
        return next_state

    def suggest_crop(self, state: ReviewAgentState) -> ReviewAgentState:
        crop = suggest_crop_advice(state.get("context") or {}, state["clip"])
        next_state = dict(state)
        next_state["crop_suggestion"] = crop
        reasons = list(state.get("reasons") or [])
        reasons.append(crop["reason"])
        next_state["reasons"] = reasons
        return next_state

    def final_recommendation(self, state: ReviewAgentState) -> ReviewAgentState:
        clip = state["clip"]
        sensitive = state.get("sensitive_check") or {"privacy_risk": "low", "matches": []}
        boundary = state.get("boundary_suggestion") or {
            "suggested_start": clip["edited_start"],
            "suggested_end": clip["edited_end"],
        }
        crop = state.get("crop_suggestion") or {"crop_advice": "keep_current"}
        quality_score = float(state.get("quality_score") or 0.0)
        context_score = float(state.get("context_score") or 0.0)
        boundary_score = float(state.get("boundary_score") or 0.0)
        privacy_risk = str(sensitive.get("privacy_risk") or "low")

        boundary_change = (
            abs(float(boundary["suggested_start"]) - float(clip["edited_start"])) > 0.5
            or abs(float(boundary["suggested_end"]) - float(clip["edited_end"])) > 0.5
        )

        recommended_action = "keep"
        decision = "recommended"
        if privacy_risk == "high":
            recommended_action = "manual_review"
            decision = "manual_review"
        elif quality_score < 0.35:
            recommended_action = "reject"
            decision = "rejected"
        elif privacy_risk == "medium":
            recommended_action = "manual_review"
            decision = "manual_review"
        elif bool(state.get("needs_more_context")) or context_score < 0.45:
            recommended_action = "extend_context"
            decision = "manual_review"
        elif boundary_change and boundary_score < 0.72:
            recommended_action = "adjust_boundaries"
        elif quality_score >= 0.65 and boundary_score >= 0.65:
            recommended_action = "render_ready"

        reasons = _dedupe(state.get("reasons") or [])
        warnings = _dedupe(state.get("warnings") or [])
        if recommended_action == "render_ready":
            reasons.append("The clip is likely understandable as a standalone short after review.")
        elif recommended_action == "adjust_boundaries":
            reasons.append("Review the suggested trim points before rendering.")
        elif recommended_action == "manual_review" and privacy_risk != "low":
            reasons.append("Human review is recommended before rendering because privacy risk is not low.")

        result = {
            "project_id": int(state["project_id"]),
            "clip_id": str(state["clip_id"]),
            "database_clip_id": clip.get("database_id"),
            "decision": decision,
            "recommended_action": recommended_action,
            "quality_score": round(quality_score, 2),
            "context_score": round(context_score, 2),
            "hook_score": round(float(state.get("hook_score") or 0.0), 2),
            "payoff_score": round(float(state.get("payoff_score") or 0.0), 2),
            "boundary_score": round(boundary_score, 2),
            "privacy_risk": privacy_risk,
            "needs_more_context": bool(state.get("needs_more_context")),
            "suggested_start": float(boundary["suggested_start"]),
            "suggested_end": float(boundary["suggested_end"]),
            "reviewed_start": float(boundary["suggested_start"]),
            "reviewed_end": float(boundary["suggested_end"]),
            "crop_advice": str(crop.get("crop_advice") or "keep_current"),
            "reasons": reasons,
            "warnings": warnings,
            "context_expansions": int(state.get("context_expansions") or 0),
            "apply_safe_suggestions": bool(state.get("apply_safe_suggestions", True)),
            "raw_result": {
                "candidate_features": get_candidate_features(clip),
                "context_padding_seconds": float(state.get("context_padding_seconds") or 20.0),
                "context_expansions": int(state.get("context_expansions") or 0),
                "sensitive_matches": sensitive.get("matches") or [],
                "boundary_suggestion": boundary,
                "crop_suggestion": crop,
                "transcript_path": state.get("transcript_path"),
            },
        }
        next_state = dict(state)
        next_state.update({"decision": decision, "recommended_action": recommended_action, "result": result})
        return next_state

    def save_evaluation(self, state: ReviewAgentState) -> ReviewAgentState:
        saved = save_evaluation(state["result"])
        result = dict(state["result"])
        result["evaluation_id"] = saved["evaluation_id"]
        next_state = dict(state)
        next_state["evaluation_id"] = int(saved["evaluation_id"])
        next_state["result"] = result
        return next_state

    def latest_evaluation(self, project_id: int, clip_id: str) -> dict[str, Any] | None:
        return get_latest_evaluation(project_id, clip_id)

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


class LocalEvaluatorAdapter:
    def evaluate(self, state: ReviewAgentState) -> dict[str, Any]:
        return evaluate_quality_local(state)


class LocalReviewWorkflow:
    def __init__(self, runtime: ReviewAgentRuntime) -> None:
        self.runtime = runtime

    def invoke(self, state: ReviewAgentState) -> ReviewAgentState:
        current = self.runtime.load_candidate(state)
        current = self.runtime.retrieve_context(current)
        current = self.runtime.evaluate_quality(current)
        current = self.runtime.route_context_decision(current)
        if self.runtime.should_retrieve_more_context(current) == "retrieve_more_context":
            current = self.runtime.retrieve_more_context(current)
            current = self.runtime.evaluate_quality(current)
        current = self.runtime.check_privacy(current)
        current = self.runtime.suggest_boundaries(current)
        current = self.runtime.suggest_crop(current)
        current = self.runtime.final_recommendation(current)
        current = self.runtime.save_evaluation(current)
        return current


def build_review_graph(runtime: ReviewAgentRuntime):  # pragma: no cover - optional dependency path
    if StateGraph is None:
        return LocalReviewWorkflow(runtime)

    graph = StateGraph(ReviewAgentState)
    graph.add_node("load_candidate", runtime.load_candidate)
    graph.add_node("retrieve_context", runtime.retrieve_context)
    graph.add_node("evaluate_quality", runtime.evaluate_quality)
    graph.add_node("route_context_decision", runtime.route_context_decision)
    graph.add_node("retrieve_more_context", runtime.retrieve_more_context)
    graph.add_node("check_privacy", runtime.check_privacy)
    graph.add_node("suggest_boundaries", runtime.suggest_boundaries)
    graph.add_node("suggest_crop", runtime.suggest_crop)
    graph.add_node("final_recommendation", runtime.final_recommendation)
    graph.add_node("save_evaluation", runtime.save_evaluation)

    graph.set_entry_point("load_candidate")
    graph.add_edge("load_candidate", "retrieve_context")
    graph.add_edge("retrieve_context", "evaluate_quality")
    graph.add_edge("evaluate_quality", "route_context_decision")
    graph.add_conditional_edges(
        "route_context_decision",
        runtime.should_retrieve_more_context,
        {
            "retrieve_more_context": "retrieve_more_context",
            "continue": "check_privacy",
        },
    )
    graph.add_edge("retrieve_more_context", "evaluate_quality")
    graph.add_edge("check_privacy", "suggest_boundaries")
    graph.add_edge("suggest_boundaries", "suggest_crop")
    graph.add_edge("suggest_crop", "final_recommendation")
    graph.add_edge("final_recommendation", "save_evaluation")
    graph.add_edge("save_evaluation", END)
    return graph.compile()


def _dedupe(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped
