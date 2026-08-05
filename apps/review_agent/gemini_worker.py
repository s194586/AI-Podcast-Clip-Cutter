"""Isolated Gemini review worker.

This module is launched only as ``python -m apps.review_agent.gemini_worker``.
It deliberately has no Airflow or DAG imports and exchanges the complete
request and one controlled response through standard streams.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any

from .providers import (
    GEMINI_WORKER_PROTOCOL_VERSION,
    _create_genai_client,
    _parse_boundary_decision,
    _provider_error_from_exception,
    _request_structured_response,
    safe_provider_failure_diagnostics,
)


def _invalid_request(message: str) -> dict[str, Any]:
    return {
        "protocol_version": GEMINI_WORKER_PROTOCOL_VERSION,
        "ok": False,
        "diagnostics": {
            "category": "ReviewProviderError",
            "mapped_error_type": "ReviewProviderError",
            "original_error_type": "InvalidWorkerRequest",
            "safe_message": message,
        },
    }


def handle_request(request: Any) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        return _invalid_request("Gemini worker received a non-object request.")
    if request.get("protocol_version") != GEMINI_WORKER_PROTOCOL_VERSION:
        return _invalid_request("Gemini worker received an unsupported IPC protocol version.")
    if request.get("operation") != "review_gemini":
        return _invalid_request("Gemini worker received an unsupported operation.")

    api_key = request.get("api_key")
    model = request.get("model")
    prompt = request.get("prompt")
    timeout_seconds = request.get("timeout_seconds")
    if not isinstance(api_key, str) or not api_key.strip():
        return _invalid_request("Gemini worker request is missing API credentials.")
    if not isinstance(model, str) or not model.strip():
        return _invalid_request("Gemini worker request is missing the model.")
    if not isinstance(prompt, str) or not prompt:
        return _invalid_request("Gemini worker request is missing the prompt.")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        return _invalid_request("Gemini worker request has an invalid timeout.")
    timeout_seconds = float(timeout_seconds)
    if timeout_seconds <= 0:
        return _invalid_request("Gemini worker request has an invalid timeout.")

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
        return {
            "protocol_version": GEMINI_WORKER_PROTOCOL_VERSION,
            "ok": True,
            "result": {"decision": decision_payload},
        }
    except Exception as exc:
        error = _provider_error_from_exception(exc)
        return {
            "protocol_version": GEMINI_WORKER_PROTOCOL_VERSION,
            "ok": False,
            "diagnostics": safe_provider_failure_diagnostics(error),
        }
    finally:
        if client is not None and hasattr(client, "close"):
            client.close()


def main() -> int:
    try:
        request = json.load(sys.stdin)
        response = handle_request(request)
        json.dump(response, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.flush()
        return 0
    except Exception:
        # Do not serialize raw exceptions: they may contain request secrets.
        response = _invalid_request("Gemini worker could not decode the IPC request.")
        json.dump(response, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.flush()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
