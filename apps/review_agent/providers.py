from __future__ import annotations

import json
import multiprocessing
import re
import time
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import ValidationError

from .schemas import GeminiBoundaryDecision


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_GEMINI_REQUEST_TIMEOUT_SECONDS = 300
LOCAL_STUB_MODEL = "local_stub"


class ReviewProviderError(RuntimeError):
    pass


class ReviewProviderOutputError(ReviewProviderError):
    pass


class ReviewProviderExtractionError(ReviewProviderError):
    """Raised when an interaction has no supported completed model output."""


class ReviewProviderTimeoutError(ReviewProviderError):
    pass


class ReviewProviderCancelledError(ReviewProviderError):
    """Raised when the local pipeline explicitly cancels an in-flight request."""


class ReviewProviderRequestCancelledError(ReviewProviderError):
    """Raised when the remote service reports a cancelled request such as HTTP 499."""


class ReviewProviderQuotaError(ReviewProviderError):
    """Raised when Gemini rejects a request because quota or rate limits were reached."""


class ReviewProviderCompatibilityError(ReviewProviderError):
    """Raised when the installed provider contract is incompatible with the API."""


class BoundaryReviewProvider(Protocol):
    provider: str
    model: str

    def review(
        self,
        context: dict[str, Any],
        corrective_message: str | None = None,
        *,
        timeout_seconds: float | None = None,
        cancellation_check: Callable[[], bool] | None = None,
    ) -> GeminiBoundaryDecision:
        ...


class LocalStubBoundaryReviewer:
    provider = "local_stub"
    model = LOCAL_STUB_MODEL

    def review(
        self,
        context: dict[str, Any],
        corrective_message: str | None = None,
        **_: Any,
    ) -> GeminiBoundaryDecision:
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
        selected_start, selected_end = _coerce_local_stub_to_allowed_pair(
            context,
            selected_start,
            selected_end,
        )

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
        request_timeout_seconds: float = DEFAULT_GEMINI_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        if not str(api_key or "").strip():
            raise ReviewProviderError("GEMINI_API_KEY is required when CLIP_REVIEW_MODE=gemini.")
        self.api_key = api_key
        self.model = str(model or DEFAULT_GEMINI_MODEL)
        self._client_factory = client_factory
        self.request_timeout_seconds = max(0.001, float(request_timeout_seconds))
        self.last_prompt_payload: dict[str, Any] | None = None

    def review(
        self,
        context: dict[str, Any],
        corrective_message: str | None = None,
        *,
        timeout_seconds: float | None = None,
        cancellation_check: Callable[[], bool] | None = None,
    ) -> GeminiBoundaryDecision:
        prompt_payload = build_gemini_prompt_payload(context)
        self.last_prompt_payload = prompt_payload
        prompt = build_gemini_prompt(prompt_payload, corrective_message=corrective_message)
        effective_timeout = max(
            0.001,
            min(self.request_timeout_seconds, float(timeout_seconds or self.request_timeout_seconds)),
        )
        if cancellation_check is not None and cancellation_check():
            raise ReviewProviderCancelledError("Gemini boundary review was cancelled.")
        if self._client_factory is None:
            return _run_gemini_request_in_process(
                api_key=self.api_key,
                model=self.model,
                prompt=prompt,
                timeout_seconds=effective_timeout,
                cancellation_check=cancellation_check,
            )
        response = self._create_structured_response(prompt, timeout_seconds=effective_timeout)
        return _parse_boundary_decision(response)

    def _create_structured_response(self, prompt: str, *, timeout_seconds: float) -> Any:
        client = self._client_factory(self.api_key) if self._client_factory else _create_genai_client(
            self.api_key,
            timeout_seconds=timeout_seconds,
        )
        try:
            return _request_structured_response(
                client,
                model=self.model,
                prompt=prompt,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            raise _provider_error_from_exception(exc) from exc
        finally:
            if client is not None and hasattr(client, "close"):
                client.close()


def _request_structured_response(
    client: Any,
    *,
    model: str,
    prompt: str,
    timeout_seconds: float,
) -> Any:
    schema = _model_json_schema(GeminiBoundaryDecision)
    interactions = getattr(client, "interactions", None)
    if interactions is None or not hasattr(interactions, "create"):
        raise ReviewProviderCompatibilityError(
            "Gemini provider compatibility error: google-genai does not expose Interactions."
        )
    return interactions.create(
        model=model,
        input=prompt,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": schema,
        },
    )


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
        "allowed_boundary_pairs": _boundary_pairs_for_prompt(context.get("allowed_boundary_pairs") or []),
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
        "The selected start and end option indexes must match one entry in ALLOWED BOUNDARY PAIRS.\n"
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
        "ALLOWED BOUNDARY PAIRS\n" + _json_for_prompt(payload.get("allowed_boundary_pairs") or []),
    ]
    if corrective_message:
        sections.append("CORRECTION\n" + str(corrective_message).strip())
    return "\n\n".join(sections)


def _create_genai_client(api_key: str, *, timeout_seconds: float) -> Any:
    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on environment
        raise ReviewProviderError("google-genai is not installed. Install the google-genai package.") from exc
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=_timeout_milliseconds(timeout_seconds),
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def _run_gemini_request_in_process(
    *,
    api_key: str,
    model: str,
    prompt: str,
    timeout_seconds: float,
    cancellation_check: Callable[[], bool] | None,
) -> GeminiBoundaryDecision:
    payload = _run_bounded_process(
        _gemini_request_worker,
        (api_key, model, prompt, timeout_seconds),
        timeout_seconds=timeout_seconds,
        cancellation_check=cancellation_check,
    )
    if bool(payload.get("ok")):
        return _validate_decision(dict(payload.get("decision") or {}))
    category = str(payload.get("category") or "ReviewProviderError")
    message = str(payload.get("message") or "Gemini boundary review failed.")
    error_types = {
        "ReviewProviderTimeoutError": ReviewProviderTimeoutError,
        "ReviewProviderRequestCancelledError": ReviewProviderRequestCancelledError,
        "ReviewProviderQuotaError": ReviewProviderQuotaError,
        "ReviewProviderCompatibilityError": ReviewProviderCompatibilityError,
        "ReviewProviderExtractionError": ReviewProviderExtractionError,
        "ReviewProviderOutputError": ReviewProviderOutputError,
    }
    raise error_types.get(category, ReviewProviderError)(message)


def _run_bounded_process(
    target: Callable[..., None],
    args: tuple[Any, ...],
    *,
    timeout_seconds: float,
    cancellation_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    process_context = multiprocessing.get_context("spawn")
    receive_connection, send_connection = process_context.Pipe(duplex=False)
    process = process_context.Process(
        target=target,
        args=(send_connection, *args),
        daemon=True,
    )
    deadline = time.monotonic() + max(0.001, float(timeout_seconds))
    try:
        try:
            process.start()
        except OSError as exc:
            raise ReviewProviderError("Gemini request worker could not start.") from exc
        send_connection.close()
        while True:
            if cancellation_check is not None and cancellation_check():
                _terminate_process(process)
                raise ReviewProviderCancelledError("Gemini boundary review was cancelled.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process(process)
                raise ReviewProviderTimeoutError(
                    f"Gemini boundary review timed out after {float(timeout_seconds):g} seconds."
                )
            if receive_connection.poll(min(0.1, remaining)):
                try:
                    payload = receive_connection.recv()
                except EOFError as exc:
                    raise ReviewProviderError(
                        "Gemini request worker closed without a response."
                    ) from exc
                process.join(timeout=1.0)
                if process.is_alive():
                    _terminate_process(process)
                return dict(payload or {})
            if not process.is_alive():
                process.join(timeout=1.0)
                if receive_connection.poll():
                    try:
                        return dict(receive_connection.recv() or {})
                    except EOFError as exc:
                        raise ReviewProviderError(
                            "Gemini request worker closed without a response."
                        ) from exc
                raise ReviewProviderError("Gemini request worker exited without a response.")
    finally:
        send_connection.close()
        receive_connection.close()
        if process.is_alive():
            _terminate_process(process)


def _gemini_request_worker(
    send_connection: Any,
    api_key: str,
    model: str,
    prompt: str,
    timeout_seconds: float,
) -> None:
    client = None
    try:
        client = _create_genai_client(api_key, timeout_seconds=timeout_seconds)
        response = _request_structured_response(
            client,
            model=model,
            prompt=prompt,
            timeout_seconds=timeout_seconds,
        )
        decision = _parse_boundary_decision(response)
        decision_payload = (
            decision.model_dump() if hasattr(decision, "model_dump") else decision.dict()
        )
        send_connection.send({"ok": True, "decision": decision_payload})
    except Exception as exc:
        error = _provider_error_from_exception(exc)
        send_connection.send(
            {
                "ok": False,
                "category": error.__class__.__name__,
                "message": str(error),
            }
        )
    finally:
        try:
            if client is not None and hasattr(client, "close"):
                client.close()
        finally:
            send_connection.close()


def _terminate_process(process: Any) -> None:
    if not process.is_alive():
        process.join(timeout=0.2)
        return
    process.terminate()
    process.join(timeout=1.0)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(timeout=1.0)


def _timeout_milliseconds(timeout_seconds: float) -> int:
    return max(1, int(float(timeout_seconds) * 1000))


def _provider_error_from_exception(exc: Exception) -> ReviewProviderError:
    if isinstance(exc, ReviewProviderError):
        return exc
    status_values = [
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ]
    message = _safe_provider_error_message(exc)
    if 429 in status_values or re.search(r"\b429\b", message):
        return ReviewProviderQuotaError(
            "Gemini quota or rate limit was exceeded (HTTP 429). Retry review later."
        )
    if 499 in status_values or re.search(r"\b499\b", message):
        return ReviewProviderRequestCancelledError(
            "Gemini request was cancelled by the upstream service (HTTP 499)."
        )
    class_name = exc.__class__.__name__.casefold()
    if isinstance(exc, TimeoutError) or "timeout" in class_name or "timed out" in message.casefold():
        return ReviewProviderTimeoutError("Gemini boundary review request timed out.")
    if _is_provider_compatibility_error(status_values, message):
        status = "HTTP 400" if 400 in status_values or re.search(r"\b400\b", message) else "unsupported API"
        return ReviewProviderCompatibilityError(
            f"Gemini provider compatibility error ({status})."
        )
    status = next((value for value in status_values if isinstance(value, int)), None)
    status_suffix = f" (HTTP {status})" if status is not None else ""
    return ReviewProviderError(f"Gemini provider request failed{status_suffix}.")


def _is_provider_compatibility_error(status_values: list[Any], message: str) -> bool:
    normalized = message.casefold()
    compatibility_markers = (
        "legacy interactions api schema",
        "provider contract",
        "unsupported api version",
        "unsupported sdk schema",
        "requires attention",
        "upgrade your google-genai",
    )
    return bool(
        (400 in status_values or re.search(r"\b400\b", normalized))
        and ("invalid_request" in normalized or any(marker in normalized for marker in compatibility_markers))
    )


def _safe_provider_error_message(exc: Exception) -> str:
    message = " ".join(str(exc).split()) or exc.__class__.__name__
    message = re.sub(
        r"(?i)([?&](?:key|api[_-]?key|token|access_token|authorization)=)([^&\s\"']+)",
        r"\1<redacted>",
        message,
    )
    message = re.sub(
        r"(?i)(api[_-]?key|password|secret|token)\s*=\s*([^\s,;]+)",
        r"\1=<redacted>",
        message,
    )
    return message[:500]


def _parse_boundary_decision(response: Any) -> GeminiBoundaryDecision:
    text = _interaction_structured_text(response)
    if not text:
        raise ReviewProviderError("Gemini response did not contain structured output text.")
    try:
        if hasattr(GeminiBoundaryDecision, "model_validate_json"):
            return GeminiBoundaryDecision.model_validate_json(str(text))
        return GeminiBoundaryDecision.parse_raw(str(text))
    except ValidationError as exc:
        raise ReviewProviderOutputError(
            "Gemini response did not match the boundary decision schema."
        ) from exc
    except Exception as exc:
        raise ReviewProviderOutputError(
            "Gemini response could not be parsed as structured JSON."
        ) from exc


def _interaction_structured_text(response: Any) -> str:
    status = _normalized_discriminator(_object_field(response, "status"))
    if status and status != "completed":
        raise ReviewProviderExtractionError(
            "Gemini interaction did not complete with supported model output."
        )

    output_text = _object_field(response, "output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    steps = _object_field(response, "steps")
    if not isinstance(steps, (list, tuple)) or not steps:
        if _object_field(response, "outputs") is not None:
            raise ReviewProviderCompatibilityError(
                "Gemini provider compatibility error: legacy outputs schema is unsupported."
            )
        raise ReviewProviderExtractionError(
            "Gemini interaction did not contain supported model output."
        )

    model_output_text: list[str] = []
    for step in steps:
        step_type = _normalized_discriminator(_object_field(step, "type"))
        if step_type != "model_output":
            continue
        if _object_field(step, "error") is not None:
            continue
        content_items = _object_field(step, "content")
        if not isinstance(content_items, (list, tuple)):
            continue
        for content in content_items:
            if _normalized_discriminator(_object_field(content, "type")) != "text":
                continue
            text = _object_field(content, "text")
            if isinstance(text, str) and text:
                model_output_text.append(text)

    combined_text = "".join(model_output_text).strip()
    if not combined_text:
        raise ReviewProviderExtractionError(
            "Gemini interaction did not contain supported model output."
        )
    return combined_text


def _normalized_discriminator(value: Any) -> str:
    if isinstance(value, str):
        return value
    enum_value = getattr(value, "value", None)
    return enum_value if isinstance(enum_value, str) else ""


def _object_field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _validate_decision(value: dict[str, Any]) -> GeminiBoundaryDecision:
    if hasattr(GeminiBoundaryDecision, "model_validate"):
        return GeminiBoundaryDecision.model_validate(value)
    return GeminiBoundaryDecision.parse_obj(value)


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


def _boundary_pairs_for_prompt(pairs: list[dict[str, Any]]) -> list[dict[str, int]]:
    return [
        {
            "start_option_index": int(pair.get("start_option_index") or 0),
            "end_option_index": int(pair.get("end_option_index") or 0),
        }
        for pair in pairs
    ]


def _coerce_local_stub_to_allowed_pair(
    context: dict[str, Any],
    selected_start: dict[str, Any],
    selected_end: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    allowed_pairs = [dict(pair) for pair in context.get("allowed_boundary_pairs") or []]
    selected_pair = (
        int(selected_start["option_index"]),
        int(selected_end["option_index"]),
    )
    allowed_indexes = {
        (int(pair["start_option_index"]), int(pair["end_option_index"]))
        for pair in allowed_pairs
    }
    if selected_pair in allowed_indexes or not allowed_pairs:
        return selected_start, selected_end

    current_pair = (
        int(context.get("current_aligned_start_option_index") or 0),
        int(context.get("current_aligned_end_option_index") or 0),
    )
    first_allowed = allowed_pairs[0]
    fallback_pair = current_pair if current_pair in allowed_indexes else (
        int(first_allowed["start_option_index"]),
        int(first_allowed["end_option_index"]),
    )
    starts = {
        int(option["option_index"]): option
        for option in context.get("start_boundary_options") or []
    }
    ends = {
        int(option["option_index"]): option
        for option in context.get("end_boundary_options") or []
    }
    return starts[fallback_pair[0]], ends[fallback_pair[1]]


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
