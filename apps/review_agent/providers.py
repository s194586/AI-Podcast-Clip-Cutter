from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from pydantic import ValidationError

from .context import (
    allowed_boundary_pair_indexes,
    boundary_options_by_segment_id,
    current_aligned_boundary_options,
    segment_map,
)
from .schemas import GeminiBoundaryDecision


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_GEMINI_REQUEST_TIMEOUT_SECONDS = 300
DEFAULT_GEMINI_CREDENTIAL_PREFLIGHT_TIMEOUT_SECONDS = 10
LOCAL_STUB_MODEL = "local_stub"
COMPACT_REVIEW_REQUEST_CONTRACT_VERSION = 3
REVIEW_RESPONSE_CONTRACT_VERSION = 2
GEMINI_WORKER_PROTOCOL_VERSION = 1
GEMINI_WORKER_MODULE = "apps.review_agent.gemini_worker"
_WORKER_POLL_INTERVAL_SECONDS = 0.1
_WORKER_TERMINATE_GRACE_SECONDS = 1.0
_MAX_WORKER_RESPONSE_BYTES = 1_000_000


class ReviewProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostics = sanitize_provider_failure_diagnostics(diagnostics)


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


class ReviewProviderCredentialError(ReviewProviderError):
    """Raised when Gemini rejects the configured API credentials."""


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
            review_response_contract_version=REVIEW_RESPONSE_CONTRACT_VERSION,
            decision=decision,
            start_segment_id=str(selected_start["segment_id"]),
            end_segment_id=str(selected_end["segment_id"]),
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


def preflight_gemini_credentials(
    *,
    api_key: str,
    timeout_seconds: float = DEFAULT_GEMINI_CREDENTIAL_PREFLIGHT_TIMEOUT_SECONDS,
    client_factory: Callable[[str], Any] | None = None,
) -> bool:
    """Check Gemini credentials with one non-generative request.

    Transient provider failures deliberately return ``False`` so the existing
    review flow can record manual review; confirmed credential failures are the
    sole preflight errors that block automatic job creation.
    """

    if not str(api_key or "").strip():
        raise ReviewProviderCredentialError(
            "Gemini review requires a non-empty GEMINI_API_KEY. Set GEMINI_API_KEY before starting AI review."
        )

    client = None
    try:
        client = (
            client_factory(api_key)
            if client_factory is not None
            else _create_genai_client(api_key, timeout_seconds=max(0.001, float(timeout_seconds)))
        )
        models = getattr(client, "models", None)
        list_models = getattr(models, "list", None)
        if not callable(list_models):
            raise ReviewProviderError("Gemini credential preflight is unavailable in the installed SDK.")
        # The SDK returns a pager; consume no more than its first item so the
        # request verifies authorization without generating any model content.
        next(iter(list_models()), None)
        return True
    except Exception as exc:
        error = _provider_error_from_exception(exc)
        if isinstance(error, ReviewProviderCredentialError):
            raise error from exc
        return False
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
    schema = _normalize_gemini_response_schema(_model_json_schema(GeminiBoundaryDecision))
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
    """Build the compact, provider-facing Gemini request without mutating context."""
    return build_compact_review_request(context)


def build_compact_review_request(context: dict[str, Any]) -> dict[str, Any]:
    """Project internal review context into the versioned Gemini request contract.

    Internal boundary options and allowed pairs remain available to backend
    validation, but segment text is sent to Gemini exactly once.
    """
    canonical_segments = segment_map(context)
    segments = _compact_segments(context)
    compact_segment_ids = {segment["segment_id"] for segment in segments}
    if compact_segment_ids != set(canonical_segments):
        raise ValueError("Compact review request segments do not match canonical transcript segments.")
    start_options = boundary_options_by_segment_id(
        context,
        option_list_name="start_boundary_options",
        canonical_segments=canonical_segments,
    )
    end_options = boundary_options_by_segment_id(
        context,
        option_list_name="end_boundary_options",
        canonical_segments=canonical_segments,
    )
    allowed_pairs = allowed_boundary_pair_indexes(
        context, start_options=start_options, end_options=end_options
    )
    current_start, current_end = current_aligned_boundary_options(
        context,
        start_options=start_options,
        end_options=end_options,
        allowed_pairs=allowed_pairs,
    )
    for segment in segments:
        segment["start_eligible"] = segment["segment_id"] in start_options
        segment["end_eligible"] = segment["segment_id"] in end_options
    return {
        "review_request_contract_version": COMPACT_REVIEW_REQUEST_CONTRACT_VERSION,
        "candidate": {
            "clip_id": context.get("clip_id"),
            "candidate_id": context.get("candidate_id"),
            "current_start": _float_or_none(context.get("candidate_start")),
            "current_end": _float_or_none(context.get("candidate_end")),
            "minimum_duration_seconds": _float_or_none(
                context.get("minimum_duration_seconds", 10.0)
            ),
            "maximum_duration_seconds": _float_or_none(
                context.get("maximum_duration_seconds", 90.0)
            ),
            "current_aligned_start_segment_id": (
                current_start["segment_id"] if current_start is not None else None
            ),
            "current_aligned_end_segment_id": (
                current_end["segment_id"] if current_end is not None else None
            ),
        },
        "segments": segments,
    }


def build_gemini_prompt(payload: dict[str, Any], corrective_message: str | None = None) -> str:
    instruction = (
        "You are an editor of short-form podcast clips.\n\n"
        "You receive one chronologically ordered transcript window.\n\n"
        "You do not rank clips.\n"
        "You do not calculate engagement metrics.\n"
        "You do not analyze video.\n"
        "You only decide whether the clip forms a coherent standalone excerpt and which supplied transcript "
        "segment boundaries should be used.\n\n"
        "review_request_contract_version 3 identifies the input request contract.\n"
        "Your response must contain review_response_contract_version 2; it identifies the separate response "
        "contract.\n\n"
        "Each segment relation is before, candidate, or after. Boolean start_eligible and "
        "end_eligible identify whether that segment is eligible for each respective boundary.\n"
        "The current aligned segment IDs in candidate metadata identify the transcript boundaries nearest to the "
        "original candidate start and end. The backend resolves IDs to timestamps and performs final validation.\n\n"
        "You must make the editorial decision yourself.\n"
        "You are not allowed to defer the decision to a human.\n\n"
        "Choose exactly one action:\n"
        "- render_ready\n"
        "- adjust_boundaries\n"
        "- reject\n\n"
        "You must always return exactly one valid start_segment_id and one valid end_segment_id.\n"
        "Do not return null.\n"
        "For render_ready, select the segment IDs representing the best current coherent boundaries.\n"
        "Use adjust_boundaries when another supplied start or end segment creates a better standalone clip by "
        "improving the setup, opening sentence, question, answer completeness, payoff, or ending.\n"
        "Use reject when the candidate cannot be turned into a coherent useful short using the supplied "
        "transcript context.\n"
        "For adjust_boundaries, select the segment IDs that improve the beginning or ending.\n"
        "For reject, return current_aligned_start_segment_id and current_aligned_end_segment_id; the backend will "
        "ignore the boundaries.\n\n"
        "The start segment must have start_eligible=true.\n"
        "The end segment must have end_eligible=true.\n"
        "Choose IDs only from the supplied chronological transcript window.\n"
        "Do not return option indexes. Do not invent segment IDs or timestamps.\n\n"
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
        "COMPACT REVIEW REQUEST\n" + _json_for_prompt(payload),
    ]
    if corrective_message:
        sections.append("CORRECTION\n" + str(corrective_message).strip())
    return "\n\n".join(sections)


def _create_genai_client(api_key: str, *, timeout_seconds: float) -> Any:
    try:
        import ssl

        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
        import truststore
    except Exception as exc:  # pragma: no cover - depends on environment
        raise ReviewProviderError("google-genai is not installed. Install the google-genai package.") from exc

    # This factory also runs in the isolated review worker. Keep the SSL
    # context process-local so every Gemini client uses the platform certificate
    # store without relaxing certificate validation.
    tls_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=_timeout_milliseconds(timeout_seconds),
            retry_options=types.HttpRetryOptions(attempts=1),
            client_args={"verify": tls_context},
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
        {
            "protocol_version": GEMINI_WORKER_PROTOCOL_VERSION,
            "operation": "review_gemini",
            "api_key": api_key,
            "model": model,
            "prompt": prompt,
            "timeout_seconds": timeout_seconds,
        },
        timeout_seconds=timeout_seconds,
        cancellation_check=cancellation_check,
    )
    if bool(payload.get("ok")):
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise ReviewProviderError("Gemini request worker returned an incomplete success response.")
        return _validate_decision(dict(result.get("decision") or {}))
    diagnostics = sanitize_provider_failure_diagnostics(payload.get("diagnostics"))
    category = str(
        diagnostics.get("mapped_error_type")
        or diagnostics.get("category")
        or payload.get("category")
        or "ReviewProviderError"
    )
    message = str(
        diagnostics.get("safe_message")
        or payload.get("message")
        or "Gemini boundary review failed."
    )
    error_types = {
        "ReviewProviderTimeoutError": ReviewProviderTimeoutError,
        "ReviewProviderRequestCancelledError": ReviewProviderRequestCancelledError,
        "ReviewProviderQuotaError": ReviewProviderQuotaError,
        "ReviewProviderCredentialError": ReviewProviderCredentialError,
        "ReviewProviderCompatibilityError": ReviewProviderCompatibilityError,
        "ReviewProviderExtractionError": ReviewProviderExtractionError,
        "ReviewProviderOutputError": ReviewProviderOutputError,
    }
    raise error_types.get(category, ReviewProviderError)(message, diagnostics=diagnostics)


def _run_bounded_process(
    request_payload: Mapping[str, Any],
    *,
    timeout_seconds: float,
    cancellation_check: Callable[[], bool] | None = None,
    _worker_module: str = GEMINI_WORKER_MODULE,
    _executable: str | None = None,
) -> dict[str, Any]:
    """Run an importable worker module without inheriting the parent's ``__main__``.

    Production always uses the fixed Gemini worker module. The private module
    override exists solely for offline process-boundary regression tests; it is
    never selected from the request payload or other production input.
    """

    command = [str(_executable or sys.executable), "-m", str(_worker_module)]
    serialized_request = json.dumps(
        dict(request_payload), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    process = None
    deadline = time.monotonic() + max(0.001, float(timeout_seconds))
    try:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            raise ReviewProviderError("Gemini request worker could not start.") from exc

        communicate_input: bytes | None = serialized_request
        while True:
            if cancellation_check is not None and cancellation_check():
                _terminate_subprocess(process)
                raise ReviewProviderCancelledError("Gemini boundary review was cancelled.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_subprocess(process)
                raise ReviewProviderTimeoutError(
                    f"Gemini boundary review timed out after {float(timeout_seconds):g} seconds."
                )
            try:
                stdout, _stderr = process.communicate(
                    input=communicate_input,
                    timeout=min(_WORKER_POLL_INTERVAL_SECONDS, remaining),
                )
                break
            except subprocess.TimeoutExpired:
                # ``communicate`` may safely be resumed after TimeoutExpired;
                # input has already been retained by Popen's communication state.
                communicate_input = None

        return_code = process.returncode
        if return_code != 0:
            if return_code is not None and return_code < 0:
                raise ReviewProviderError(
                    f"Gemini request worker was terminated by signal {-return_code}."
                )
            raise ReviewProviderError(
                f"Gemini request worker exited with code {return_code}."
            )
        return _decode_worker_response(stdout)
    finally:
        if process is not None and process.poll() is None:
            _terminate_subprocess(process)
        if process is not None:
            _close_subprocess_pipes(process)


def _decode_worker_response(stdout: bytes) -> dict[str, Any]:
    if not stdout:
        raise ReviewProviderError("Gemini request worker returned an empty IPC response.")
    if len(stdout) > _MAX_WORKER_RESPONSE_BYTES:
        raise ReviewProviderError("Gemini request worker returned an oversized IPC response.")
    try:
        decoded = stdout.decode("utf-8")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewProviderError("Gemini request worker returned an invalid IPC response.") from exc
    if not isinstance(payload, dict):
        raise ReviewProviderError("Gemini request worker returned a non-object IPC response.")
    if payload.get("protocol_version") != GEMINI_WORKER_PROTOCOL_VERSION:
        raise ReviewProviderError("Gemini request worker returned an unsupported IPC protocol version.")
    if type(payload.get("ok")) is not bool:
        raise ReviewProviderError("Gemini request worker returned an incomplete IPC response.")
    if payload["ok"] and not isinstance(payload.get("result"), dict):
        raise ReviewProviderError("Gemini request worker returned an incomplete success response.")
    if not payload["ok"] and not isinstance(payload.get("diagnostics"), dict):
        raise ReviewProviderError("Gemini request worker returned an incomplete failure response.")
    return payload


def _terminate_subprocess(process: subprocess.Popen[bytes]) -> None:
    """Reap a child after a terminate/kill escalation, closing all three pipes."""

    try:
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=_WORKER_TERMINATE_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            process.kill()
        try:
            process.communicate(timeout=_WORKER_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:  # pragma: no cover - OS-level pathological case
            process.wait(timeout=_WORKER_TERMINATE_GRACE_SECONDS)
    finally:
        _close_subprocess_pipes(process)


def _close_subprocess_pipes(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            stream.close()


def _timeout_milliseconds(timeout_seconds: float) -> int:
    return max(1, int(float(timeout_seconds) * 1000))


def _provider_error_from_exception(exc: Exception) -> ReviewProviderError:
    if isinstance(exc, ReviewProviderError):
        if not exc.diagnostics:
            exc.diagnostics = _provider_failure_diagnostics(exc, exc)
        return exc
    status_values = _provider_status_values(exc)
    message = _safe_provider_error_message(exc)
    if 429 in status_values or re.search(r"\b429\b", message):
        return _with_provider_failure_diagnostics(
            ReviewProviderQuotaError(
                "Gemini quota or rate limit was exceeded (HTTP 429). Retry review later."
            ),
            exc,
        )
    if 499 in status_values or re.search(r"\b499\b", message):
        return _with_provider_failure_diagnostics(
            ReviewProviderRequestCancelledError(
                "Gemini request was cancelled by the upstream service (HTTP 499)."
            ),
            exc,
        )
    if 401 in status_values or 403 in status_values or re.search(r"\b(?:401|403)\b", message):
        return _with_provider_failure_diagnostics(
            ReviewProviderCredentialError(
                "Gemini rejected GEMINI_API_KEY credentials. Update GEMINI_API_KEY and retry review."
            ),
            exc,
        )
    if _is_invalid_api_key_error(status_values, message):
        return _with_provider_failure_diagnostics(
            ReviewProviderCredentialError(
                "Gemini rejected GEMINI_API_KEY credentials. Update GEMINI_API_KEY and retry review."
            ),
            exc,
        )
    class_name = exc.__class__.__name__.casefold()
    if isinstance(exc, TimeoutError) or "timeout" in class_name or "timed out" in message.casefold():
        return _with_provider_failure_diagnostics(
            ReviewProviderTimeoutError("Gemini boundary review request timed out."),
            exc,
        )
    if _is_provider_compatibility_error(status_values, message):
        status = "HTTP 400" if 400 in status_values or re.search(r"\b400\b", message) else "unsupported API"
        return _with_provider_failure_diagnostics(
            ReviewProviderCompatibilityError(
                f"Gemini provider compatibility error ({status})."
            ),
            exc,
        )
    status = _safe_http_status(exc)
    status_suffix = f" (HTTP {status})" if status is not None else ""
    return _with_provider_failure_diagnostics(
        ReviewProviderError(f"Gemini provider request failed{status_suffix}."),
        exc,
    )


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


def _is_invalid_api_key_error(status_values: list[Any], message: str) -> bool:
    normalized = message.casefold()
    invalid_key_markers = (
        "api key not valid",
        "invalid api key",
        "invalid api_key",
        "api key is invalid",
        "invalid_api_key",
        "api_key_invalid",
    )
    return bool(
        (400 in status_values or re.search(r"\b400\b", normalized))
        and any(marker in normalized for marker in invalid_key_markers)
    )


def safe_provider_failure_diagnostics(error: Exception) -> dict[str, Any]:
    existing = getattr(error, "diagnostics", None)
    if existing:
        return sanitize_provider_failure_diagnostics(existing)
    mapped = error if isinstance(error, ReviewProviderError) else _provider_error_from_exception(error)
    return _provider_failure_diagnostics(error, mapped)


def sanitize_provider_failure_diagnostics(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the only diagnostic fields allowed across the process boundary."""
    if not isinstance(value, Mapping):
        return {}
    diagnostics: dict[str, Any] = {}
    for key in ("category", "mapped_error_type", "original_error_type", "cause_type", "context_type"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate and len(candidate) <= 160:
            diagnostics[key] = candidate
    status = value.get("http_status")
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        diagnostics["http_status"] = status
    message = value.get("safe_message")
    if isinstance(message, str) and message.strip():
        diagnostics["safe_message"] = _sanitize_provider_error_message(message, "Gemini provider request failed.")
    validation_errors = value.get("validation_errors")
    if isinstance(validation_errors, (list, tuple)):
        safe_validation_errors = []
        for item in validation_errors:
            if not isinstance(item, Mapping):
                continue
            location = item.get("loc")
            error_type = item.get("type")
            error_message = item.get("msg")
            if not isinstance(location, (list, tuple)):
                continue
            safe_location = [part for part in location if isinstance(part, (str, int))]
            if len(safe_location) != len(location):
                continue
            if not isinstance(error_type, str) or not error_type or len(error_type) > 160:
                continue
            if not isinstance(error_message, str) or not error_message.strip():
                continue
            safe_validation_errors.append(
                {
                    "loc": safe_location,
                    "type": error_type,
                    "msg": _sanitize_provider_error_message(error_message, "Validation failed."),
                }
            )
        if safe_validation_errors:
            diagnostics["validation_errors"] = safe_validation_errors
    return diagnostics


def _with_provider_failure_diagnostics(
    mapped_error: ReviewProviderError,
    original_error: Exception,
) -> ReviewProviderError:
    mapped_error.diagnostics = _provider_failure_diagnostics(original_error, mapped_error)
    return mapped_error


def _provider_failure_diagnostics(
    original_error: Exception,
    mapped_error: ReviewProviderError,
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "category": mapped_error.__class__.__name__,
        "mapped_error_type": mapped_error.__class__.__name__,
        "original_error_type": _exception_type_name(original_error),
        "safe_message": _safe_provider_error_message(original_error),
    }
    cause = original_error.__cause__
    context = original_error.__context__
    if cause is not None:
        diagnostics["cause_type"] = _exception_type_name(cause)
    elif context is not None:
        diagnostics["context_type"] = _exception_type_name(context)
    status = _safe_http_status(original_error)
    if status is not None:
        diagnostics["http_status"] = status
    return sanitize_provider_failure_diagnostics(diagnostics)


def _provider_status_values(exc: Exception) -> list[Any]:
    return [
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ]


def _safe_http_status(exc: Exception) -> int | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for value in _provider_status_values(current):
            if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
                return value
        current = current.__cause__ or current.__context__
    return None


def _safe_provider_error_message(exc: Exception) -> str:
    return _sanitize_provider_error_message(str(exc), exc.__class__.__name__)


def _exception_type_name(exc: BaseException) -> str:
    module = exc.__class__.__module__
    name = exc.__class__.__name__
    return name if module == "builtins" else f"{module}.{name}"


def _sanitize_provider_error_message(message: str, fallback: str) -> str:
    message = str(message or "")
    message = re.sub(r"(?is)\b(?:response\s+)?(?:body|content|headers?)\s*[:=]\s*(?:\{.*?\}|\[.*?\]|[^\n]+)", "<redacted>", message)
    message = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer <redacted>", message)
    message = re.sub(
        r"(?i)\b(?:authorization|x-goog-api-key|x-api-key)\s*[:=]\s*[^\s,;]+",
        "authorization=<redacted>",
        message,
    )
    message = re.sub(r"(?i)https?://[^\s\"']+", "<redacted-url>", message)
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
    message = " ".join(message.split()) or fallback
    return message[:500]


def _parse_boundary_decision(response: Any) -> GeminiBoundaryDecision:
    text = _interaction_structured_text(response)
    if not text:
        raise ReviewProviderError("Gemini response did not contain structured output text.")
    try:
        _reject_legacy_response_fields(str(text))
        if hasattr(GeminiBoundaryDecision, "model_validate_json"):
            return GeminiBoundaryDecision.model_validate_json(str(text))
        return GeminiBoundaryDecision.parse_raw(str(text))
    except ReviewProviderOutputError:
        raise
    except ValidationError as exc:
        error = ReviewProviderOutputError(
            "Gemini response did not match the boundary decision schema.",
            diagnostics={
                "category": "ReviewProviderOutputError",
                "mapped_error_type": "ReviewProviderOutputError",
                "original_error_type": _exception_type_name(exc),
                "safe_message": "Gemini response did not match the boundary decision schema.",
                "validation_errors": [
                    {
                        "loc": list(item.get("loc") or ()),
                        "type": item.get("type"),
                        "msg": item.get("msg"),
                    }
                    for item in exc.errors(include_url=False, include_context=False, include_input=False)
                ],
            },
        )
        raise error from exc
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
    if "selected_start_option_index" in value or "selected_end_option_index" in value:
        raise ReviewProviderOutputError(
            "Gemini response used deprecated option-index boundary fields."
        )
    if hasattr(GeminiBoundaryDecision, "model_validate"):
        return GeminiBoundaryDecision.model_validate(value)
    return GeminiBoundaryDecision.parse_obj(value)


def _reject_legacy_response_fields(text: str) -> None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return
    if isinstance(value, dict) and (
        "selected_start_option_index" in value or "selected_end_option_index" in value
    ):
        raise ReviewProviderOutputError(
            "Gemini response used deprecated option-index boundary fields."
        )


def _compact_segments(
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    ordered: list[tuple[float, float, int, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    position = 0
    for relation, key in (
        ("before", "context_before"),
        ("candidate", "candidate_segments"),
        ("after", "context_after"),
    ):
        for segment in context.get(key) or []:
            segment_id = _required_segment_id(
                segment.get("segment_id"),
                location=f"{key} compact segment",
            )
            if segment_id in seen_ids:
                raise ValueError(f"Duplicate segment_id in compact review request: {segment_id}")
            seen_ids.add(segment_id)
            item = {
                "segment_id": segment_id,
                "start": float(segment.get("start") or 0.0),
                "end": float(segment.get("end") or 0.0),
                "text": str(segment.get("text") or ""),
                "relation": relation,
            }
            speaker = str(segment.get("speaker") or "").strip()
            if speaker:
                item["speaker"] = speaker
            ordered.append((item["start"], item["end"], position, item))
            position += 1
    return [item for _, _, _, item in sorted(ordered)]


def _required_segment_id(value: Any, *, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} segment_id must be a non-empty string.")
    return value


def _float_or_none(value: Any) -> float | None:
    return None if value is None else float(value)


def _coerce_local_stub_to_allowed_pair(
    context: dict[str, Any],
    selected_start: dict[str, Any],
    selected_end: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    canonical_segments = segment_map(context)
    starts_by_id = boundary_options_by_segment_id(
        context,
        option_list_name="start_boundary_options",
        canonical_segments=canonical_segments,
    )
    ends_by_id = boundary_options_by_segment_id(
        context,
        option_list_name="end_boundary_options",
        canonical_segments=canonical_segments,
    )
    allowed_indexes = allowed_boundary_pair_indexes(
        context, start_options=starts_by_id, end_options=ends_by_id
    )
    current_start, current_end = current_aligned_boundary_options(
        context,
        start_options=starts_by_id,
        end_options=ends_by_id,
        allowed_pairs=allowed_indexes,
    )
    selected_pair = (
        int(selected_start["option_index"]),
        int(selected_end["option_index"]),
    )
    if selected_pair in allowed_indexes or not allowed_indexes:
        return selected_start, selected_end

    current_pair = (current_start["option_index"], current_end["option_index"])
    fallback_pair = current_pair if current_pair in allowed_indexes else min(allowed_indexes)
    starts = {
        int(option["option_index"]): option
        for option in starts_by_id.values()
    }
    ends = {
        int(option["option_index"]): option
        for option in ends_by_id.values()
    }
    return starts[fallback_pair[0]], ends[fallback_pair[1]]


def _model_json_schema(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_json_schema"):
        return model.model_json_schema()
    return model.schema()


def _normalize_gemini_response_schema(value: Any) -> Any:
    """Return a schema copy using Gemini-supported singleton enums instead of const."""

    if isinstance(value, dict):
        normalized = {
            key: _normalize_gemini_response_schema(item)
            for key, item in value.items()
            if key != "const"
        }
        if "const" in value:
            normalized["enum"] = [_normalize_gemini_response_schema(value["const"])]
        return normalized
    if isinstance(value, list):
        return [_normalize_gemini_response_schema(item) for item in value]
    return value


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
