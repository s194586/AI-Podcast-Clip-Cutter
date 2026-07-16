from __future__ import annotations

import json
import re
from typing import Any, Callable, Protocol

from pydantic import ValidationError

from .schemas import GeminiBoundaryDecision


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
LOCAL_STUB_MODEL = "local_stub"


class ReviewProviderError(RuntimeError):
    pass


class ReviewProviderOutputError(ReviewProviderError):
    pass


class BoundaryReviewProvider(Protocol):
    provider: str
    model: str

    def review(self, context: dict[str, Any], corrective_message: str | None = None) -> GeminiBoundaryDecision:
        ...


class LocalStubBoundaryReviewer:
    provider = "local_stub"
    model = LOCAL_STUB_MODEL

    def review(self, context: dict[str, Any], corrective_message: str | None = None) -> GeminiBoundaryDecision:
        candidate_segments = list(context.get("candidate_segments") or [])
        before_segments = list(context.get("context_before") or [])
        after_segments = list(context.get("context_after") or [])
        if not candidate_segments:
            raise ReviewProviderError("Transcript context is missing or does not overlap the candidate.")

        selected_start = _option_for_segment(
            context.get("start_boundary_options") or [],
            str(candidate_segments[0]["segment_id"]),
        )
        selected_end = _option_for_segment(
            context.get("end_boundary_options") or [],
            str(candidate_segments[-1]["segment_id"]),
        )
        start_reason = "The first candidate segment is a usable transcript-aligned start."
        end_reason = "The last candidate segment is a usable transcript-aligned end."

        candidate_text = _joined_text(candidate_segments)
        if before_segments and _starts_mid_thought(candidate_text):
            selected_start = _option_for_segment(
                context.get("start_boundary_options") or [],
                str(before_segments[-1]["segment_id"]),
            )
            start_reason = "The candidate appears to start mid-thought, so the previous context segment is included."

        if after_segments and _ends_unfinished(candidate_text):
            selected_end = _option_for_segment(
                context.get("end_boundary_options") or [],
                str(after_segments[0]["segment_id"]),
            )
            end_reason = "The candidate ending appears unfinished, so the next context segment is included."

        if selected_start is None or selected_end is None:
            raise ReviewProviderError("Local stub could not resolve transcript boundary option indexes.")

        changed = (
            selected_start["segment_id"] != candidate_segments[0]["segment_id"]
            or selected_end["segment_id"] != candidate_segments[-1]["segment_id"]
        )
        decision = "adjust_boundaries" if changed else "render_ready"
        return GeminiBoundaryDecision(
            decision=decision,
            selected_start_option_index=int(selected_start["option_index"]),
            selected_end_option_index=int(selected_end["option_index"]),
            reasoning_summary=(
                "Local stub selected transcript-aligned boundaries for offline development and tests."
            ),
            start_reason=start_reason,
            end_reason=end_reason,
            warnings=[],
        )


class GeminiBoundaryReviewer:
    provider = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        if not str(api_key or "").strip():
            raise ReviewProviderError("GEMINI_API_KEY is required when CLIP_REVIEW_MODE=gemini.")
        self.api_key = api_key
        self.model = str(model or DEFAULT_GEMINI_MODEL)
        self._client_factory = client_factory
        self.last_prompt_payload: dict[str, Any] | None = None

    def review(self, context: dict[str, Any], corrective_message: str | None = None) -> GeminiBoundaryDecision:
        prompt_payload = build_gemini_prompt_payload(context)
        self.last_prompt_payload = prompt_payload
        prompt = build_gemini_prompt(prompt_payload, corrective_message=corrective_message)
        response = self._create_structured_response(prompt)
        return _parse_boundary_decision(response)

    def _create_structured_response(self, prompt: str) -> Any:
        client = self._client_factory(self.api_key) if self._client_factory else _create_genai_client(self.api_key)
        schema = _model_json_schema(GeminiBoundaryDecision)
        try:
            if hasattr(client, "interactions"):
                return client.interactions.create(
                    model=self.model,
                    input=prompt,
                    response_format={
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": schema,
                    },
                )

            if hasattr(client, "models"):
                from google.genai import types  # type: ignore

                return client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=GeminiBoundaryDecision,
                    ),
                )
        except Exception as exc:  # pragma: no cover - network/client dependent
            raise ReviewProviderError(f"Gemini boundary review failed: {exc}") from exc
        raise ReviewProviderError("google-genai client does not expose interactions or models APIs.")


def build_gemini_prompt_payload(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "clip_id": context.get("clip_id"),
        "candidate_start": context.get("candidate_start"),
        "candidate_end": context.get("candidate_end"),
        "context_seconds": context.get("context_seconds"),
        "earliest_allowed_start": context.get("earliest_allowed_start"),
        "latest_allowed_end": context.get("latest_allowed_end"),
        "current_aligned_start_option_index": context.get("current_aligned_start_option_index"),
        "current_aligned_end_option_index": context.get("current_aligned_end_option_index"),
        "context_before": _segments_for_prompt(context.get("context_before") or []),
        "candidate_segments": _segments_for_prompt(context.get("candidate_segments") or []),
        "context_after": _segments_for_prompt(context.get("context_after") or []),
        "start_boundary_options": _boundary_options_for_prompt(context.get("start_boundary_options") or []),
        "end_boundary_options": _boundary_options_for_prompt(context.get("end_boundary_options") or []),
    }


def build_gemini_prompt(payload: dict[str, Any], corrective_message: str | None = None) -> str:
    instruction = (
        "You are an editor of short-form podcast clips.\n\n"
        "You receive a candidate transcript and nearby transcript context.\n\n"
        "You do not rank clips.\n"
        "You do not calculate engagement metrics.\n"
        "You do not analyze video.\n"
        "You only decide whether the clip forms a coherent standalone excerpt and which supplied transcript "
        "segment boundaries should be used.\n\n"
        "Choose boundaries only from the supplied START OPTIONS and END OPTIONS.\n"
        "The current_aligned_start_option_index and current_aligned_end_option_index identify the transcript "
        "boundary options nearest to the original candidate start and end.\n\n"
        "You must make the editorial decision yourself.\n"
        "You are not allowed to defer the decision to a human.\n\n"
        "Choose exactly one action:\n"
        "- render_ready\n"
        "- adjust_boundaries\n"
        "- reject\n\n"
        "You must always return one valid start option index and one valid end option index.\n"
        "Do not return null.\n"
        "For render_ready, select the option indexes representing the best current coherent boundaries.\n"
        "Use adjust_boundaries when another supplied start or end segment creates a better standalone clip by "
        "improving the setup, opening sentence, question, answer completeness, payoff, or ending.\n"
        "Use reject when the candidate cannot be turned into a coherent useful short using the supplied "
        "transcript context.\n"
        "For adjust_boundaries, select the option indexes that improve the beginning or ending.\n"
        "For reject, return the current aligned option indexes; the backend will ignore them.\n\n"
        "The start index refers to START OPTIONS.\n"
        "The end index refers to END OPTIONS.\n"
        "Do not invent an index.\n"
        "Choose only indexes present in the supplied options.\n\n"
        "You are evaluating a podcast / talking-head transcript. Visual framing is not part of your task. "
        "Phrases such as \"jak widzisz\", \"na tym wykresie\", or \"spojrz tutaj\" may be mentioned in warnings, "
        "but you must still choose render_ready, adjust_boundaries, or reject based on semantic transcript "
        "coherence.\n\n"
        "Warnings may mention transcript uncertainty, but you must still choose one of the three actions.\n"
        "Do not choose a start after the selected end.\n"
        "Do not choose boundaries outside the supplied context.\n"
        "Prefer a final duration between 20 and 90 seconds.\n"
        "Return only the structured response."
    )
    sections = [
        instruction,
        "CANDIDATE METADATA\n" + _json_for_prompt(
            {
                "clip_id": payload.get("clip_id"),
                "candidate_start": payload.get("candidate_start"),
                "candidate_end": payload.get("candidate_end"),
                    "context_seconds": payload.get("context_seconds"),
                    "earliest_allowed_start": payload.get("earliest_allowed_start"),
                    "latest_allowed_end": payload.get("latest_allowed_end"),
                    "current_aligned_start_option_index": payload.get("current_aligned_start_option_index"),
                    "current_aligned_end_option_index": payload.get("current_aligned_end_option_index"),
                }
            ),
        "CONTEXT BEFORE\n" + _json_for_prompt(payload.get("context_before") or []),
        "CANDIDATE\n" + _json_for_prompt(payload.get("candidate_segments") or []),
        "CONTEXT AFTER\n" + _json_for_prompt(payload.get("context_after") or []),
        "ALLOWED START OPTIONS\n" + _json_for_prompt(payload.get("start_boundary_options") or []),
        "ALLOWED END OPTIONS\n" + _json_for_prompt(payload.get("end_boundary_options") or []),
    ]
    if corrective_message:
        sections.append("CORRECTION\n" + str(corrective_message).strip())
    return "\n\n".join(sections)


def _create_genai_client(api_key: str) -> Any:
    try:
        from google import genai  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on environment
        raise ReviewProviderError("google-genai is not installed. Install the google-genai package.") from exc
    return genai.Client(api_key=api_key)


def _parse_boundary_decision(response: Any) -> GeminiBoundaryDecision:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, GeminiBoundaryDecision):
        return parsed
    if isinstance(parsed, dict):
        return _validate_decision(parsed)

    text = (
        getattr(response, "output_text", None)
        or getattr(response, "text", None)
        or _first_candidate_text(response)
    )
    if not text:
        raise ReviewProviderError("Gemini response did not contain structured output text.")
    try:
        if hasattr(GeminiBoundaryDecision, "model_validate_json"):
            return GeminiBoundaryDecision.model_validate_json(str(text))
        return GeminiBoundaryDecision.parse_raw(str(text))
    except ValidationError as exc:
        raise ReviewProviderOutputError(f"Gemini response did not match the boundary decision schema: {exc}") from exc
    except Exception as exc:
        raise ReviewProviderError(f"Gemini response could not be parsed as JSON: {exc}") from exc


def _validate_decision(value: dict[str, Any]) -> GeminiBoundaryDecision:
    if hasattr(GeminiBoundaryDecision, "model_validate"):
        return GeminiBoundaryDecision.model_validate(value)
    return GeminiBoundaryDecision.parse_obj(value)


def _first_candidate_text(response: Any) -> str:
    try:
        candidates = getattr(response, "candidates", None) or []
        parts = candidates[0].content.parts
        return "".join(str(getattr(part, "text", "") or "") for part in parts)
    except Exception:
        return ""


def _segments_for_prompt(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for segment in segments:
        item = {
            "segment_id": str(segment.get("segment_id") or ""),
            "start": float(segment.get("start") or 0.0),
            "end": float(segment.get("end") or 0.0),
            "text": str(segment.get("text") or ""),
        }
        speaker = segment.get("speaker")
        if speaker not in (None, ""):
            item["speaker"] = str(speaker)
        sanitized.append(item)
    return sanitized


def _boundary_options_for_prompt(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "segment_id": str(option.get("segment_id") or ""),
            "option_index": int(option.get("option_index") or 0),
            "start": float(option.get("start") or 0.0),
            "end": float(option.get("end") or 0.0),
            "text": str(option.get("text") or ""),
        }
        for option in options
    ]


def _model_json_schema(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_json_schema"):
        return model.model_json_schema()
    return model.schema()


def _json_for_prompt(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _joined_text(segments: list[dict[str, Any]]) -> str:
    return " ".join(str(segment.get("text") or "").strip() for segment in segments).strip()


def _option_for_segment(options: list[dict[str, Any]], segment_id: str) -> dict[str, Any] | None:
    for option in options:
        if str(option.get("segment_id")) == str(segment_id):
            return option
    return None


def _starts_mid_thought(text: str) -> bool:
    first = _first_word(text)
    return first in {"and", "but", "so", "because", "ale", "bo", "wiec", "czyli"}


def _ends_unfinished(text: str) -> bool:
    compact = " ".join(str(text or "").split())
    if not compact:
        return True
    if compact[-1] in {",", ":", ";", "-"}:
        return True
    return _first_word(compact.split()[-1]) in {"and", "but", "because", "so", "ale", "bo", "wiec"}


def _first_word(text: str) -> str:
    compact = " ".join(str(text or "").strip().split()).lower()
    if not compact:
        return ""
    match = re.match(r"\w+", compact, re.UNICODE)
    return match.group(0) if match else ""
