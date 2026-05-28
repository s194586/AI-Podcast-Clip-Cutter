#!/usr/bin/env python3
"""Experimental semantic clip review helpers.

This module is intentionally kept out of the default pipeline.
It remains in the repository as a reference prototype, but the current
project direction is to replace it with provider-based transcription and
timestamp-aware video understanding adapters.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from gemini_transport import generate_text_with_transport, get_api_key


SEMANTIC_DIRECTOR_MODE_OFF = "off"
SEMANTIC_DIRECTOR_MODE_LOCAL_ONLY = "local_only"
SEMANTIC_DIRECTOR_MODE_GEMINI_OPTIONAL = "gemini_optional"
SEMANTIC_DIRECTOR_MODE_GEMINI_REQUIRED = "gemini_required"

VALID_SEMANTIC_DIRECTOR_MODES = (
    SEMANTIC_DIRECTOR_MODE_OFF,
    SEMANTIC_DIRECTOR_MODE_LOCAL_ONLY,
    SEMANTIC_DIRECTOR_MODE_GEMINI_OPTIONAL,
    SEMANTIC_DIRECTOR_MODE_GEMINI_REQUIRED,
)

SUBTITLE_CORRECTION_MODE_OFF = "off"
SUBTITLE_CORRECTION_MODE_LOCAL_ONLY = "local_only"
SUBTITLE_CORRECTION_MODE_GEMINI_OPTIONAL = "gemini_optional"
SUBTITLE_CORRECTION_MODE_GEMINI_REQUIRED = "gemini_required"

VALID_SUBTITLE_CORRECTION_MODES = (
    SUBTITLE_CORRECTION_MODE_OFF,
    SUBTITLE_CORRECTION_MODE_LOCAL_ONLY,
    SUBTITLE_CORRECTION_MODE_GEMINI_OPTIONAL,
    SUBTITLE_CORRECTION_MODE_GEMINI_REQUIRED,
)


def normalize_semantic_director_mode(value: Any) -> str:
    normalized = str(value or SEMANTIC_DIRECTOR_MODE_OFF).strip().lower()
    if normalized not in VALID_SEMANTIC_DIRECTOR_MODES:
        raise ValueError(
            "Unsupported semantic director mode: "
            f"{value}. Expected one of: {', '.join(VALID_SEMANTIC_DIRECTOR_MODES)}"
        )
    return normalized


def normalize_subtitle_correction_mode(value: Any) -> str:
    normalized = str(value or SUBTITLE_CORRECTION_MODE_OFF).strip().lower()
    if normalized not in VALID_SUBTITLE_CORRECTION_MODES:
        raise ValueError(
            "Unsupported subtitle correction mode: "
            f"{value}. Expected one of: {', '.join(VALID_SUBTITLE_CORRECTION_MODES)}"
        )
    return normalized


def clamp_score(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    return max(0.0, min(1.0, parsed))


def _normalize_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


def _normalize_float_or_none(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def basic_subtitle_cleanup(text: str) -> str:
    cleaned = " ".join(str(text or "").replace("\n", " ").split())
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"([,.;:!?])(?=[^\s])", r"\1 ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    if not cleaned:
        return ""
    if cleaned[0].isalpha():
        cleaned = cleaned[0].upper() + cleaned[1:]
    cleaned = re.sub(r"([!?.,])\1{2,}", r"\1", cleaned)
    return cleaned


def _extract_json_payload(text: str) -> Any:
    stripped = str(text or "").strip()
    if not stripped:
        raise ValueError("Empty Gemini response.")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", stripped, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(1))


@dataclass
class ClipDirectorError(RuntimeError):
    message: str

    def __str__(self) -> str:
        return self.message


class ClipDirector:
    def __init__(
        self,
        *,
        mode: str = SEMANTIC_DIRECTOR_MODE_OFF,
        model_name: str = "models/gemini-2.5-flash",
        request_timeout: float = 45.0,
        api_key: str | None = None,
    ) -> None:
        self.mode = normalize_semantic_director_mode(mode)
        self.model_name = str(model_name or "models/gemini-2.5-flash").strip()
        self.request_timeout = float(request_timeout or 45.0)
        self.api_key = api_key or get_api_key()

    def review_candidates(self, candidates: list[dict[str, Any]], **context: Any) -> list[dict[str, Any]]:
        return [self.review_candidate(candidate, **context) for candidate in candidates]

    def review_candidate(self, candidate: dict[str, Any], **context: Any) -> dict[str, Any]:
        local_features = candidate.get("local_features") or {}
        hook_score = clamp_score(local_features.get("hook_score"), default=0.45)
        context_score = clamp_score(local_features.get("boundary_completeness_score"), default=0.55)
        payoff_score = clamp_score(
            max(
                float(local_features.get("payoff_score", 0.0) or 0.0),
                1.0 - float(local_features.get("low_payoff_penalty", 0.0) or 0.0),
            ),
            default=0.45,
        )
        story_score = clamp_score((hook_score + context_score + payoff_score) / 3.0, default=0.48)
        ad_hits = float(local_features.get("ad_like_hits", 0) or 0)
        boring_setup = bool(
            float(local_features.get("gameplay_setup_penalty", 0.0) or 0.0) >= 0.25
            or float(local_features.get("preamble_penalty", 0.0) or 0.0) >= 0.2
        )
        too_context_dependent = bool(float(local_features.get("contextless_penalty", 0.0) or 0.0) >= 0.15)
        advertisement = bool(ad_hits > 0 or float(local_features.get("ad_like_penalty", 0.0) or 0.0) >= 0.2)
        keep = not advertisement
        return {
            "keep": keep,
            "story_score": story_score,
            "hook_score": hook_score,
            "context_score": context_score,
            "payoff_score": payoff_score,
            "boring_setup": boring_setup,
            "advertisement_or_intro": advertisement,
            "too_context_dependent": too_context_dependent,
            "suggested_start": None,
            "suggested_end": None,
            "reason": "Local semantic fallback kept the candidate." if keep else "Local fallback marked clip as advertisement/intro.",
            "subtitle_language_issues": [],
            "semantic_director_used": False,
            "semantic_model": "",
            "semantic_fallback_reason": "" if keep else "advertisement_or_intro",
        }

    def refine_boundaries(
        self,
        candidate: dict[str, Any],
        review: dict[str, Any],
        context_segments: list[dict[str, Any]],
        *,
        min_duration: float,
        max_duration: float,
    ) -> dict[str, Any]:
        start = float(candidate.get("start") or 0.0)
        end = float(candidate.get("end") or start)
        original_start = start
        original_end = end
        reasons: list[str] = []
        fallback_reason = ""
        lower_bound = 0.0
        upper_bound = max(original_end, original_start + min_duration)
        if context_segments:
            lower_bound = min(float(item.get("start", lower_bound) or lower_bound) for item in context_segments)
            upper_bound = max(float(item.get("end", upper_bound) or upper_bound) for item in context_segments)

        suggested_start = _normalize_float_or_none(review.get("suggested_start"))
        suggested_end = _normalize_float_or_none(review.get("suggested_end"))
        if suggested_start is not None:
            start = max(lower_bound, suggested_start)
            reasons.append("semantic_suggested_start")
        elif _normalize_bool(review.get("too_context_dependent")) or clamp_score(review.get("context_score"), 0.5) < 0.45:
            previous = [segment for segment in context_segments if float(segment.get("start", start)) < original_start - 0.02]
            if previous:
                start = max(lower_bound, float(previous[-1].get("start", start)))
                reasons.append("semantic_context_extension")

        if suggested_end is not None:
            end = min(upper_bound, suggested_end)
            reasons.append("semantic_suggested_end")
        elif clamp_score(review.get("payoff_score"), 0.5) < 0.45:
            following = [segment for segment in context_segments if float(segment.get("end", end)) > original_end + 0.02]
            if following:
                end = min(upper_bound, float(following[0].get("end", end)))
                reasons.append("semantic_payoff_extension")

        if end <= start:
            start = original_start
            end = original_end
            fallback_reason = "invalid_semantic_bounds"
            reasons = []

        start = max(lower_bound, start)
        end = min(upper_bound, end)
        duration = end - start
        if duration > max_duration:
            end = min(end, start + max_duration)
            reasons.append("semantic_max_duration_clamp")
        if end - start < min_duration:
            if original_end - start >= min_duration:
                end = start + min_duration
                reasons.append("semantic_min_duration_extension")
            else:
                start = original_start
                end = original_end
                if not fallback_reason:
                    fallback_reason = "semantic_min_duration_conflict"
                reasons = []

        return {
            "start": round(start, 4),
            "end": round(end, 4),
            "semantic_boundary_adjusted": bool(abs(start - original_start) > 0.02 or abs(end - original_end) > 0.02),
            "semantic_fallback_reason": fallback_reason,
            "adjustments": reasons,
        }

    def correct_subtitle_text(self, segments: list[dict[str, Any]], **context: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        normalized = [dict(segment) for segment in segments]
        corrected_count = 0
        for segment in normalized:
            original = str(segment.get("text", "") or "")
            corrected = basic_subtitle_cleanup(original)
            if corrected and corrected != original:
                segment["text"] = corrected
                corrected_count += 1
        return normalized, {
            "subtitles_corrected": corrected_count > 0,
            "subtitle_corrector_used": "local_cleanup" if corrected_count > 0 else "off",
            "corrected_segments_count": corrected_count,
            "correction_fallback_reason": "",
        }


class GeminiClipDirector(ClipDirector):
    def __init__(
        self,
        *,
        mode: str = SEMANTIC_DIRECTOR_MODE_GEMINI_OPTIONAL,
        model_name: str = "models/gemini-2.5-flash",
        request_timeout: float = 45.0,
        api_key: str | None = None,
    ) -> None:
        super().__init__(mode=mode, model_name=model_name, request_timeout=request_timeout, api_key=api_key)
        if self.mode == SEMANTIC_DIRECTOR_MODE_GEMINI_REQUIRED and not self.api_key:
            raise ClipDirectorError("Gemini semantic director requires GOOGLE_API_KEY / GEMINI_API_KEY / API_KEY.")

    def _generate_json(self, prompt: str, *, operation: str) -> Any:
        if not self.api_key:
            raise ClipDirectorError("Gemini API key is missing.")
        text = generate_text_with_transport(
            prompt,
            self.model_name,
            self.api_key,
            operation,
            request_timeout=self.request_timeout,
            response_mime_type="application/json",
            temperature=0.2,
        )
        return _extract_json_payload(text)

    def review_candidate(self, candidate: dict[str, Any], **context: Any) -> dict[str, Any]:
        local_review = super().review_candidate(candidate, **context)
        payload = {
            "case_id": context.get("case_id") or "",
            "title": context.get("title") or "",
            "content_type": context.get("content_type") or "",
            "candidate": {
                "start": round(float(candidate.get("start") or 0.0), 4),
                "end": round(float(candidate.get("end") or 0.0), 4),
                "duration": round(float(candidate.get("duration") or 0.0), 4),
                "local_score": round(float(candidate.get("local_score") or 0.0), 4),
                "selection_reasons": candidate.get("selection_reasons") or [],
                "summary": candidate.get("summary") or "",
                "detected_speakers": context.get("detected_speakers") or [],
            },
            "transcript_context": [
                {
                    "start": round(float(item.get("start") or 0.0), 4),
                    "end": round(float(item.get("end") or 0.0), 4),
                    "speaker": item.get("speaker") or "",
                    "text": str(item.get("text") or "").strip(),
                }
                for item in context.get("context_segments") or []
            ],
        }
        prompt = (
            "Review this short-form clip candidate for story completeness and subtitle language quality. "
            "Return only JSON with keys: keep, story_score, hook_score, context_score, payoff_score, "
            "boring_setup, advertisement_or_intro, too_context_dependent, suggested_start, suggested_end, reason, subtitle_language_issues.\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )
        try:
            parsed = self._generate_json(prompt, operation="Semantic clip director")
            if not isinstance(parsed, dict):
                raise ValueError("Gemini semantic director response must be a JSON object.")
            review = {
                "keep": _normalize_bool(parsed.get("keep"), True),
                "story_score": clamp_score(parsed.get("story_score"), local_review["story_score"]),
                "hook_score": clamp_score(parsed.get("hook_score"), local_review["hook_score"]),
                "context_score": clamp_score(parsed.get("context_score"), local_review["context_score"]),
                "payoff_score": clamp_score(parsed.get("payoff_score"), local_review["payoff_score"]),
                "boring_setup": _normalize_bool(parsed.get("boring_setup"), local_review["boring_setup"]),
                "advertisement_or_intro": _normalize_bool(parsed.get("advertisement_or_intro"), local_review["advertisement_or_intro"]),
                "too_context_dependent": _normalize_bool(parsed.get("too_context_dependent"), local_review["too_context_dependent"]),
                "suggested_start": _normalize_float_or_none(parsed.get("suggested_start")),
                "suggested_end": _normalize_float_or_none(parsed.get("suggested_end")),
                "reason": str(parsed.get("reason") or "").strip() or "Gemini semantic director reviewed the clip.",
                "subtitle_language_issues": list(parsed.get("subtitle_language_issues") or []),
                "semantic_director_used": True,
                "semantic_model": self.model_name,
                "semantic_fallback_reason": "",
            }
            return review
        except Exception as exc:
            if self.mode == SEMANTIC_DIRECTOR_MODE_GEMINI_REQUIRED:
                raise ClipDirectorError(f"Gemini semantic director failed: {exc}") from exc
            local_review["semantic_fallback_reason"] = str(exc)
            return local_review

    def correct_subtitle_text(self, segments: list[dict[str, Any]], **context: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        local_segments, local_metadata = super().correct_subtitle_text(segments, **context)
        payload = [
            {
                "index": index,
                "start": segment.get("start"),
                "end": segment.get("end"),
                "speaker": segment.get("speaker"),
                "text": segment.get("text"),
            }
            for index, segment in enumerate(segments)
        ]
        prompt = (
            "Correct subtitle text while preserving meaning, timestamps, and segment count. "
            "Return only a JSON list of objects with keys: index, text. Do not rewrite content beyond obvious ASR/spelling/punctuation fixes.\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )
        try:
            parsed = self._generate_json(prompt, operation="Subtitle correction")
            if not isinstance(parsed, list) or len(parsed) != len(segments):
                raise ValueError("Gemini subtitle correction must return a list with the same segment count.")
            corrected_segments = [dict(segment) for segment in segments]
            corrected_count = 0
            for item in parsed:
                if not isinstance(item, dict):
                    raise ValueError("Gemini subtitle correction items must be JSON objects.")
                index = int(item.get("index"))
                if index < 0 or index >= len(corrected_segments):
                    raise ValueError("Gemini subtitle correction returned an out-of-range segment index.")
                text = basic_subtitle_cleanup(str(item.get("text") or ""))
                if not text:
                    raise ValueError("Gemini subtitle correction returned an empty text segment.")
                if text != str(corrected_segments[index].get("text") or ""):
                    corrected_segments[index]["text"] = text
                    corrected_count += 1
            return corrected_segments, {
                "subtitles_corrected": corrected_count > 0,
                "subtitle_corrector_used": self.model_name,
                "corrected_segments_count": corrected_count,
                "correction_fallback_reason": "",
            }
        except Exception as exc:
            required_mode = normalize_subtitle_correction_mode(context.get("mode") or SUBTITLE_CORRECTION_MODE_OFF)
            if required_mode == SUBTITLE_CORRECTION_MODE_GEMINI_REQUIRED:
                raise ClipDirectorError(f"Gemini subtitle correction failed: {exc}") from exc
            local_metadata["correction_fallback_reason"] = str(exc)
            return local_segments, local_metadata


def build_clip_director(
    *,
    mode: str,
    model_name: str = "models/gemini-2.5-flash",
    request_timeout: float = 45.0,
    api_key: str | None = None,
) -> ClipDirector:
    normalized = normalize_semantic_director_mode(mode)
    if normalized in {SEMANTIC_DIRECTOR_MODE_GEMINI_OPTIONAL, SEMANTIC_DIRECTOR_MODE_GEMINI_REQUIRED}:
        return GeminiClipDirector(
            mode=normalized,
            model_name=model_name,
            request_timeout=request_timeout,
            api_key=api_key,
        )
    return ClipDirector(mode=normalized, model_name=model_name, request_timeout=request_timeout, api_key=api_key)


def build_subtitle_corrector(
    *,
    mode: str,
    model_name: str = "models/gemini-2.5-flash",
    request_timeout: float = 45.0,
    api_key: str | None = None,
) -> ClipDirector:
    normalized = normalize_subtitle_correction_mode(mode)
    if normalized in {SUBTITLE_CORRECTION_MODE_GEMINI_OPTIONAL, SUBTITLE_CORRECTION_MODE_GEMINI_REQUIRED}:
        semantic_mode = (
            SEMANTIC_DIRECTOR_MODE_GEMINI_REQUIRED
            if normalized == SUBTITLE_CORRECTION_MODE_GEMINI_REQUIRED
            else SEMANTIC_DIRECTOR_MODE_GEMINI_OPTIONAL
        )
        return GeminiClipDirector(
            mode=semantic_mode,
            model_name=model_name,
            request_timeout=request_timeout,
            api_key=api_key,
        )
    return ClipDirector(mode=SEMANTIC_DIRECTOR_MODE_LOCAL_ONLY if normalized == SUBTITLE_CORRECTION_MODE_LOCAL_ONLY else SEMANTIC_DIRECTOR_MODE_OFF)
