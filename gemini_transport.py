#!/usr/bin/env python3

import base64
import json
import mimetypes
import os
import random
import shutil
import ssl
import subprocess
import time
import warnings
from pathlib import Path
from typing import Any, Optional

try:
    import certifi
except Exception as certifi_import_error:
    certifi = None
    CERTIFI_IMPORT_ERROR = certifi_import_error
else:
    CERTIFI_IMPORT_ERROR = None

try:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=FutureWarning)
        import google.generativeai as genai
except Exception:
    try:
        import google.genai as genai
    except Exception:
        genai = None

try:
    from google.generativeai import types as genai_types
except Exception:
    genai_types = None


SSL_CERT_ENV_VARS = (
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH",
)
RETRY_DELAYS_SECONDS = (5, 10, 20)
RATE_LIMIT_RETRY_RANGE_SECONDS = (10, 20)
RATE_LIMIT_MAX_RETRIES = 5


def bootstrap_ssl_certificates(quiet: bool = False, allow_insecure_fallback: bool = False) -> Optional[str]:
    cert_path = next(
        (
            os.environ[name]
            for name in SSL_CERT_ENV_VARS
            if os.environ.get(name) and os.path.isfile(os.environ[name])
        ),
        None,
    )
    if cert_path is not None:
        for env_name in SSL_CERT_ENV_VARS:
            os.environ[env_name] = cert_path
    elif certifi is None:
        if not quiet:
            print("  Warning: certifi is not installed. Run `uv add certifi` to install the CA bundle.")
    else:
        cert_path = certifi.where()
        for env_name in SSL_CERT_ENV_VARS:
            os.environ[env_name] = cert_path

    if os.name == "nt":
        os.environ.setdefault("CURL_SSL_NO_REVOKE", "1")

    if allow_insecure_fallback:
        ssl._create_default_https_context = ssl._create_unverified_context
        os.environ["PYTHONHTTPSVERIFY"] = "0"
    elif "PYTHONHTTPSVERIFY" in os.environ:
        os.environ.pop("PYTHONHTTPSVERIFY", None)

    return cert_path


def get_api_key() -> Optional[str]:
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")


def is_rate_limit_error(exc: Exception) -> bool:
    parts = [
        str(exc),
        str(getattr(exc, "code", "")),
        str(getattr(exc, "status", "")),
        str(getattr(exc, "reason", "")),
    ]
    message = " ".join(parts).lower()
    return (
        "429" in message
        or "too many requests" in message
        or "rate limit" in message
        or "resource_exhausted" in message
        or "quota" in message
    )


def wait_before_retry(exc: Exception, attempt: int, max_retries: int, operation: str) -> None:
    if attempt >= max_retries:
        return
    if is_rate_limit_error(exc):
        delay = round(random.uniform(*RATE_LIMIT_RETRY_RANGE_SECONDS), 1)
        print(f"  Wykryto limit API, robię krok w tył na {delay}s...")
        reason = "429/rate limit"
    else:
        delay = RETRY_DELAYS_SECONDS[min(attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)]
        reason = "500/server error" if any(
            token in " ".join(
                [
                    str(exc),
                    str(getattr(exc, "code", "")),
                    str(getattr(exc, "status", "")),
                    str(getattr(exc, "reason", "")),
                ]
            ).lower()
            for token in ("500", "502", "503", "504", "internal")
        ) else "temporary API error"
    message = " ".join(
        [
            str(exc),
            str(getattr(exc, "code", "")),
            str(getattr(exc, "status", "")),
            str(getattr(exc, "reason", "")),
        ]
    ).lower()
    if not is_rate_limit_error(exc) and any(token in message for token in ("500", "502", "503", "504", "internal")):
        reason = "500/server error"
    elif not is_rate_limit_error(exc):
        reason = "temporary API error"
    print(f"  Warning: {operation}: {reason}, retry in {delay}s ({attempt}/{max_retries})")
    time.sleep(delay)


def resolve_max_retries(exc: Exception | None, default_retries: int) -> int:
    if exc is not None and is_rate_limit_error(exc):
        return max(default_retries, RATE_LIMIT_MAX_RETRIES)
    return default_retries


def build_request_options(timeout_seconds: Optional[float]):
    if not timeout_seconds or genai_types is None or not hasattr(genai_types, "RequestOptions"):
        return None
    return genai_types.RequestOptions(timeout=timeout_seconds)


def configure_gemini(api_key: str) -> None:
    if genai is None:
        return
    if hasattr(genai, "configure"):
        transport = os.environ.get("GEMINI_TRANSPORT", "").strip().lower()
        if transport == "curl":
            return
        configure_kwargs = {"api_key": api_key}
        if transport and transport != "sdk":
            configure_kwargs["transport"] = transport
        genai.configure(**configure_kwargs)


def parse_curl_response(stdout: str) -> tuple[str, Optional[str]]:
    marker = "__HTTP_STATUS__:"
    body, separator, status = (stdout or "").rpartition(marker)
    if not separator:
        return stdout or "", None
    return body.rstrip(), status.strip()


def normalize_model_name(model_name: str) -> str:
    return str(model_name or "models/gemini-2.5-flash").strip()


def extract_text_from_response_payload(payload: dict) -> str:
    for candidate in payload.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            text = part.get("text")
            if text:
                return text
    raise ValueError(f"Gemini response does not contain text parts: {payload}")


def guess_mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    suffix_map = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".mp4": "video/mp4",
        ".json": "application/json",
        ".txt": "text/plain",
    }
    return suffix_map.get(path.suffix.lower(), "application/octet-stream")


def _curl_binary() -> Optional[str]:
    return shutil.which("curl.exe") or shutil.which("curl")


def _preferred_transport() -> str:
    transport = os.environ.get("GEMINI_TRANSPORT", "").strip().lower()
    if transport:
        return transport
    if os.name == "nt" and _curl_binary():
        return "curl"
    return "sdk"


def _sdk_generate_text(
    prompt: str,
    model_name: str,
    operation: str,
    *,
    request_timeout: Optional[float],
    response_mime_type: Optional[str],
    temperature: float,
    max_retries: int,
) -> str:
    if genai is None:
        raise RuntimeError("google-generativeai is not installed.")

    generation_config = {"temperature": temperature}
    if response_mime_type:
        generation_config["response_mime_type"] = response_mime_type

    model = genai.GenerativeModel(model_name, generation_config=generation_config)
    request_options = build_request_options(request_timeout)
    total_attempts = resolve_max_retries(None, max_retries)
    for attempt in range(1, total_attempts + 1):
        try:
            result = model.generate_content([prompt], request_options=request_options)
            text = getattr(result, "text", None)
            if not text and hasattr(result, "candidates") and result.candidates:
                text = str(result.candidates[0])
            if not text:
                raise ValueError("Model response contains no text.")
            return text
        except Exception as exc:
            total_attempts = resolve_max_retries(exc, total_attempts)
            if attempt == total_attempts:
                raise
            wait_before_retry(exc, attempt, total_attempts, operation)
    raise RuntimeError(f"Gemini SDK failed: {operation}")


def _sdk_generate_file_text(
    file_path: Path,
    prompt: str,
    model_name: str,
    operation: str,
    *,
    request_timeout: Optional[float],
    response_mime_type: Optional[str],
    temperature: float,
    max_retries: int,
) -> str:
    if genai is None:
        raise RuntimeError("google-generativeai is not installed.")

    generation_config = {"temperature": temperature}
    if response_mime_type:
        generation_config["response_mime_type"] = response_mime_type

    request_options = build_request_options(request_timeout)
    total_attempts = resolve_max_retries(None, max_retries)
    for attempt in range(1, total_attempts + 1):
        try:
            uploaded = genai.upload_file(str(file_path))
            model = genai.GenerativeModel(model_name, generation_config=generation_config)
            result = model.generate_content([uploaded, prompt], request_options=request_options)
            text = getattr(result, "text", None)
            if not text and hasattr(result, "candidates") and result.candidates:
                text = str(result.candidates[0])
            if not text:
                raise ValueError("Model response contains no text.")
            return text
        except Exception as exc:
            total_attempts = resolve_max_retries(exc, total_attempts)
            if attempt == total_attempts:
                raise
            wait_before_retry(exc, attempt, total_attempts, operation)
    raise RuntimeError(f"Gemini SDK file call failed: {operation}")


def _curl_generate_parts(
    parts: list[dict[str, Any]],
    model_name: str,
    api_key: str,
    operation: str,
    *,
    request_timeout: Optional[float],
    response_mime_type: Optional[str],
    temperature: float,
    max_retries: int,
) -> str:
    curl_binary = _curl_binary()
    if not curl_binary:
        raise RuntimeError("curl is not available in PATH for Gemini REST fallback.")

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": temperature,
        },
    }
    if response_mime_type:
        payload["generationConfig"]["responseMimeType"] = response_mime_type

    timeout_seconds = max(int(request_timeout or 75), 10)
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/{normalize_model_name(model_name)}:generateContent?key={api_key}"
    cmd = [
        curl_binary,
        "--silent",
        "--show-error",
        "--location",
        "--request",
        "POST",
        "--header",
        "Content-Type: application/json",
        "--data-binary",
        "@-",
        "--max-time",
        str(timeout_seconds + 5),
        "--connect-timeout",
        str(min(timeout_seconds, 20)),
        "-w",
        "\n__HTTP_STATUS__:%{http_code}",
        endpoint,
    ]
    if os.name == "nt" or os.environ.get("CURL_SSL_NO_REVOKE") == "1":
        cmd.insert(1, "--ssl-no-revoke")

    request_body = json.dumps(payload, ensure_ascii=False)
    total_attempts = resolve_max_retries(None, max_retries)
    for attempt in range(1, total_attempts + 1):
        result = subprocess.run(
            cmd,
            input=request_body,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        body, status_code = parse_curl_response(result.stdout)
        combined_output = "\n".join(part for part in (body, result.stderr) if part).strip()

        if result.returncode == 0 and status_code and status_code.startswith("2"):
            response_payload = json.loads(body or "{}")
            return extract_text_from_response_payload(response_payload)

        if result.returncode == 0 and status_code in {"429", "500", "502", "503", "504"}:
            exc = RuntimeError(f"Gemini HTTP {status_code}: {combined_output[:400]}")
            total_attempts = resolve_max_retries(exc, total_attempts)
            if attempt == total_attempts:
                raise exc
            wait_before_retry(exc, attempt, total_attempts, operation)
            continue

        detail = combined_output or f"curl exited with code {result.returncode}"
        exc = RuntimeError(detail)
        total_attempts = resolve_max_retries(exc, total_attempts)
        if attempt == total_attempts:
            raise exc
        wait_before_retry(exc, attempt, total_attempts, operation)

    raise RuntimeError(f"Gemini curl fallback failed: {operation}")


def generate_text_with_transport(
    prompt: str,
    model_name: str,
    api_key: str,
    operation: str,
    *,
    request_timeout: Optional[float] = None,
    response_mime_type: Optional[str] = None,
    temperature: float = 0.2,
    max_retries: int = 3,
) -> str:
    transport = _preferred_transport()
    bootstrap_ssl_certificates(quiet=True)

    errors = []
    if transport != "curl":
        try:
            configure_gemini(api_key)
            return _sdk_generate_text(
                prompt,
                model_name,
                operation,
                request_timeout=request_timeout,
                response_mime_type=response_mime_type,
                temperature=temperature,
                max_retries=max_retries,
            )
        except Exception as exc:
            errors.append(f"SDK error: {exc}")

    try:
        return _curl_generate_parts(
            [{"text": prompt}],
            model_name,
            api_key,
            operation,
            request_timeout=request_timeout,
            response_mime_type=response_mime_type,
            temperature=temperature,
            max_retries=max_retries,
        )
    except Exception as exc:
        errors.append(f"curl error: {exc}")
        raise RuntimeError(" | ".join(errors))


def generate_file_text_with_transport(
    file_path: Path,
    prompt: str,
    model_name: str,
    api_key: str,
    operation: str,
    *,
    mime_type: Optional[str] = None,
    request_timeout: Optional[float] = None,
    response_mime_type: Optional[str] = None,
    temperature: float = 0.2,
    max_retries: int = 3,
) -> str:
    transport = _preferred_transport()
    bootstrap_ssl_certificates(quiet=True)
    mime_type = mime_type or guess_mime_type(file_path)

    if transport == "curl":
        data = base64.b64encode(file_path.read_bytes()).decode("ascii")
        return _curl_generate_parts(
            [
                {"inline_data": {"mime_type": mime_type, "data": data}},
                {"text": prompt},
            ],
            model_name,
            api_key,
            operation,
            request_timeout=request_timeout,
            response_mime_type=response_mime_type,
            temperature=temperature,
            max_retries=max_retries,
        )

    configure_gemini(api_key)
    return _sdk_generate_file_text(
        file_path,
        prompt,
        model_name,
        operation,
        request_timeout=request_timeout,
        response_mime_type=response_mime_type,
        temperature=temperature,
        max_retries=max_retries,
    )
