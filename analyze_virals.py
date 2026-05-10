import argparse
import json
import os
import random
import re
import shutil
import ssl
import subprocess
import time
import warnings
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path

from content_classifier import (
    VALID_CONTENT_TYPE_MODES,
    classify_content,
    load_content_profile,
    normalize_content_type_mode,
    save_content_profile,
)
from local_scoring import score_candidates
from pipeline_modes import (
    AI_MODE_LOCAL_ONLY,
    VALID_AI_MODES,
    allows_gemini,
    normalize_ai_mode,
    requires_gemini,
)
from strategies import get_strategy

SSL_CERT_ENV_VARS = (
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH",
)

try:
    import certifi
except Exception as certifi_import_error:
    certifi = None
    CERTIFI_IMPORT_ERROR = certifi_import_error
else:
    CERTIFI_IMPORT_ERROR = None
    _certifi_bundle = certifi.where()
    for _env_name in SSL_CERT_ENV_VARS:
        os.environ[_env_name] = _certifi_bundle

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
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


SENTENCE_BREAK_RE = re.compile(r"(?:(?<=[.!?])|(?<=\.\.\.))\s+")
RETRY_DELAYS_SECONDS = (5, 10, 20)
RATE_LIMIT_RETRY_RANGE_SECONDS = (10, 20)
RATE_LIMIT_MAX_RETRIES = 5


def bootstrap_ssl_certificates(quiet=False, allow_insecure_fallback=False):
    cert_path = None
    if certifi is None:
        if not quiet:
            print("  Warning: certifi is not installed. Run `uv add certifi` to install the CA bundle.")
    else:
        cert_path = certifi.where()
        for env_name in SSL_CERT_ENV_VARS:
            os.environ[env_name] = cert_path

    if allow_insecure_fallback:
        ssl._create_default_https_context = ssl._create_unverified_context
        os.environ["PYTHONHTTPSVERIFY"] = "0"
    elif "PYTHONHTTPSVERIFY" in os.environ:
        os.environ.pop("PYTHONHTTPSVERIFY", None)

    return cert_path


def is_rate_limit_error(exc):
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


def is_ssl_error_message(message):
    text = str(message or "").lower()
    return any(
        token in text
        for token in (
            "certificate_verify_failed",
            "ssl handshake failed",
            "openssl_uplink",
            "unable to get local issuer certificate",
            "tls",
            "schannel",
            "crypt_e_no_revocation_check",
        )
    )


def wait_before_retry(exc, attempt, max_retries, operation):
    if attempt >= max_retries:
        return
    if is_rate_limit_error(exc):
        delay = round(random.uniform(*RATE_LIMIT_RETRY_RANGE_SECONDS), 1)
        print(f"  Wykryto limit API, robię krok w tył na {delay}s...")
        reason = "429/rate limit"
    else:
        delay = RETRY_DELAYS_SECONDS[min(attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)]
        reason = "500/server error" if "500" in " ".join(
            [
                str(exc),
                str(getattr(exc, "code", "")),
                str(getattr(exc, "status", "")),
                str(getattr(exc, "reason", "")),
            ]
        ).lower() or "internal" in " ".join(
            [
                str(exc),
                str(getattr(exc, "code", "")),
                str(getattr(exc, "status", "")),
                str(getattr(exc, "reason", "")),
            ]
        ).lower() else "temporary API error"
    message = " ".join(
        [
            str(exc),
            str(getattr(exc, "code", "")),
            str(getattr(exc, "status", "")),
            str(getattr(exc, "reason", "")),
        ]
    ).lower()
    if not is_rate_limit_error(exc):
        reason = "500/server error" if "500" in message or "internal" in message else "temporary API error"
    print(f"  Warning: {operation}: {reason}, retry in {delay}s ({attempt}/{max_retries})")
    time.sleep(delay)


def resolve_max_retries(exc, default_retries):
    if exc is not None and is_rate_limit_error(exc):
        return max(default_retries, RATE_LIMIT_MAX_RETRIES)
    return default_retries


def configure_gemini(api_key):
    if genai is None:
        raise RuntimeError("google-generativeai is not installed.")
    if hasattr(genai, "configure"):
        transport = os.environ.get("GEMINI_TRANSPORT", "").strip().lower()
        if transport == "curl":
            return
        configure_kwargs = {"api_key": api_key}
        if transport and transport != "sdk":
            configure_kwargs["transport"] = transport
        genai.configure(**configure_kwargs)


def build_request_options(timeout_seconds):
    if genai_types is None or not hasattr(genai_types, "RequestOptions"):
        return None
    return genai_types.RequestOptions(timeout=timeout_seconds)


def generate_content_with_backoff(model, payload, operation, max_retries=3, request_timeout=None):
    request_options = build_request_options(request_timeout) if request_timeout else None
    total_attempts = resolve_max_retries(None, max_retries)
    for attempt in range(1, total_attempts + 1):
        try:
            return model.generate_content(payload, request_options=request_options)
        except Exception as exc:
            total_attempts = resolve_max_retries(exc, total_attempts)
            if attempt == total_attempts:
                raise
            wait_before_retry(exc, attempt, total_attempts, operation)
    raise RuntimeError(f"Gemini call failed: {operation}")


def normalize_model_name(model_name):
    return str(model_name or "models/gemini-2.5-flash").strip()


def extract_text_from_response_payload(payload):
    for candidate in payload.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            text = part.get("text")
            if text:
                return text
    raise ValueError(f"Gemini response does not contain text parts: {payload}")


def parse_curl_response(stdout):
    marker = "__HTTP_STATUS__:"
    body, separator, status = (stdout or "").rpartition(marker)
    if not separator:
        return stdout, None
    return body.rstrip(), status.strip()


def generate_content_via_curl(prompt, model_name, api_key, operation, request_timeout=None, max_retries=3):
    curl_binary = shutil.which("curl.exe") or shutil.which("curl")
    if not curl_binary:
        raise RuntimeError("curl is not available in PATH for Gemini REST fallback.")

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
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

    total_attempts = resolve_max_retries(None, max_retries)
    for attempt in range(1, total_attempts + 1):
        result = subprocess.run(
            cmd,
            input=json.dumps(payload, ensure_ascii=False),
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

        if result.returncode != 0 and attempt < total_attempts and is_ssl_error_message(combined_output):
            wait_before_retry(RuntimeError(combined_output), attempt, total_attempts, operation)
            continue

        detail = combined_output or f"curl exited with code {result.returncode}"
        raise RuntimeError(detail)

    raise RuntimeError(f"Gemini curl fallback failed: {operation}")


def generate_gemini_text(prompt, model_name, api_key, request_timeout):
    preferred_transport = os.environ.get("GEMINI_TRANSPORT", "").strip().lower()
    errors = []

    curl_binary = shutil.which("curl.exe") or shutil.which("curl")
    use_curl_first = preferred_transport == "curl" or (os.name == "nt" and curl_binary)
    if not use_curl_first and genai is not None:
        try:
            model = genai.GenerativeModel(
                model_name,
                generation_config={"temperature": 0.2, "response_mime_type": "application/json"},
            )
            result = generate_content_with_backoff(
                model,
                [prompt],
                "Gemini smart context cutter",
                request_timeout=request_timeout,
            )
            text = getattr(result, "text", None)
            if not text and hasattr(result, "candidates") and result.candidates:
                text = str(result.candidates[0])
            if text:
                return text
            errors.append("SDK returned an empty response.")
        except Exception as exc:
            errors.append(f"SDK error: {exc}")
            if preferred_transport == "sdk":
                raise

    if api_key:
        try:
            return generate_content_via_curl(
                prompt,
                model_name,
                api_key,
                "Gemini smart context cutter (curl)",
                request_timeout=request_timeout,
            )
        except Exception as exc:
            errors.append(f"curl error: {exc}")

    if not errors and genai is None:
        errors.append("Gemini client library is unavailable.")
    raise RuntimeError(" | ".join(errors))


def extract_json_object(text):
    if not text:
        raise ValueError("Empty model response.")
    cleaned = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", text, flags=re.S).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = re.sub(r",\s*([}\]])", r"\1", cleaned[start : end + 1])
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed

    raise ValueError(f"Could not extract JSON object from model response: {text[:240]}")


def parse_time(value):
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(",", ".")
    parts = [part for part in text.split(":") if part != ""]
    if len(parts) == 1:
        return float(parts[0])
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise ValueError(f"Invalid timestamp format: {value}")


def format_time(seconds):
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hours > 0:
        return f"{hours:d}:{minutes:02d}:{secs:05.2f}"
    return f"{minutes:02d}:{secs:05.2f}"


def load_transcript(file_path):
    with open(file_path, "r", encoding="utf-8") as file_handle:
        transcript = json.load(file_handle)
    if isinstance(transcript, dict) and "segments" in transcript:
        transcript = transcript["segments"]
    return transcript


def load_heatmap(file_path):
    with open(file_path, "r", encoding="utf-8") as file_handle:
        return json.load(file_handle)


def split_sentences(text):
    parts = SENTENCE_BREAK_RE.split(text.strip())
    sentences = [part.strip() for part in parts if part.strip()]
    return sentences or [text.strip()]


def build_sentence_boundaries(transcript):
    sentences = []
    for segment in transcript:
        start = parse_time(segment["start"])
        end = parse_time(segment["end"])
        text = str(segment.get("text", "")).replace("\n", " ").strip()
        speaker = segment.get("speaker") or segment.get("speaker_id") or segment.get("speakerId")
        if not text:
            continue

        pieces = split_sentences(text)
        if len(pieces) == 1:
            sentences.append({"start": start, "end": end, "text": pieces[0], "speaker": speaker})
            continue

        total_chars = sum(len(piece) for piece in pieces)
        if total_chars == 0:
            sentences.append({"start": start, "end": end, "text": text, "speaker": speaker})
            continue

        cursor = start
        consumed = 0
        for piece in pieces[:-1]:
            consumed += len(piece)
            portion = consumed / total_chars
            boundary = start + (end - start) * portion
            sentences.append({"start": cursor, "end": boundary, "text": piece, "speaker": speaker})
            cursor = boundary
        sentences.append({"start": cursor, "end": end, "text": pieces[-1], "speaker": speaker})
    return sentences


def build_heatmap_index(heatmap):
    heatmap_sorted = sorted(heatmap, key=lambda entry: entry["start_time"])
    starts = [entry["start_time"] for entry in heatmap_sorted]
    return heatmap_sorted, starts


def average_heatmap_value(heatmap, starts, window_start, window_end):
    idx = bisect_left(starts, window_start)
    if idx > 0:
        idx -= 1

    total_weight = 0.0
    weighted_sum = 0.0
    for entry in heatmap[idx:]:
        entry_start = entry["start_time"]
        entry_end = entry["end_time"]
        if entry_start >= window_end:
            break
        overlap_start = max(window_start, entry_start)
        overlap_end = min(window_end, entry_end)
        overlap = overlap_end - overlap_start
        if overlap <= 0:
            continue
        total_weight += overlap
        weighted_sum += overlap * entry["value"]

    if total_weight == 0:
        return 0.0
    return weighted_sum / total_weight


def collect_text_for_window(sentences, window_start, window_end):
    parts = []
    for sentence in sentences:
        if sentence["end"] <= window_start:
            continue
        if sentence["start"] >= window_end:
            break
        parts.append(sentence["text"])
    return " ".join(parts).strip()


def summarize_text(text, max_chars=220):
    summary = " ".join(str(text or "").split())
    if len(summary) <= max_chars:
        return summary

    truncated = summary[:max_chars].rstrip()
    if "." in truncated:
        truncated = truncated[: truncated.rfind(".") + 1]
    if len(truncated) < 40:
        truncated = summary[:max_chars].rstrip()
    return truncated + "..."


def safe_excerpt(text, limit=320):
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."


def collect_context(sentences, window_start, window_end, margin=20.0):
    context_start = max(0.0, window_start - margin)
    context_end = window_end + margin
    context = []
    for sentence in sentences:
        if sentence["end"] <= context_start:
            continue
        if sentence["start"] >= context_end:
            break
        speaker = sentence.get("speaker")
        label = f" [{speaker}]" if speaker else ""
        context.append(
            {
                "start": sentence["start"],
                "end": sentence["end"],
                "text": sentence["text"],
                "speaker": speaker,
                "line": f'{format_time(sentence["start"])} - {format_time(sentence["end"])}{label}: {sentence["text"]}',
            }
        )
    return context


def clamp(value, lower, upper):
    return max(lower, min(upper, value))


def boundary_start_for_time(sentences, value):
    for sentence in sentences:
        if sentence["start"] <= value < sentence["end"]:
            return sentence["start"]
    starts = [sentence["start"] for sentence in sentences]
    if not starts:
        return value
    return min(starts, key=lambda item: abs(item - value))


def boundary_end_for_time(sentences, value):
    for sentence in sentences:
        if sentence["start"] < value <= sentence["end"]:
            return sentence["end"]
    ends = [sentence["end"] for sentence in sentences]
    if not ends:
        return value
    return min(ends, key=lambda item: abs(item - value))


def enforce_story_bounds(start, end, context, fallback, max_duration):
    if not context:
        return fallback["start"], fallback["end"], ["No transcript context found, keeping heatmap window."]

    context_start = context[0]["start"]
    context_end = context[-1]["end"]
    adjusted_start = boundary_start_for_time(context, clamp(start, context_start, context_end))
    adjusted_end = boundary_end_for_time(context, clamp(end, context_start, context_end))
    decisions = []

    if adjusted_start != start:
        decisions.append(f"Moved hook to sentence start: {format_time(adjusted_start)}.")
    if adjusted_end != end:
        decisions.append(f"Moved punchline to sentence end: {format_time(adjusted_end)}.")

    if adjusted_end <= adjusted_start:
        adjusted_start = fallback["start"]
        adjusted_end = fallback["end"]
        decisions.append("AI returned invalid bounds, fallback to original heatmap window.")

    if adjusted_end - adjusted_start > max_duration:
        limit = adjusted_start + max_duration
        safe_ends = [item["end"] for item in context if adjusted_start < item["end"] <= limit]
        if safe_ends:
            old_end = adjusted_end
            adjusted_end = max(safe_ends)
            decisions.append(
                f"Shortened ending from {format_time(old_end)} to {format_time(adjusted_end)} to respect {max_duration:.0f}s."
            )
        else:
            old_start = adjusted_start
            adjusted_start = max(context_start, adjusted_end - max_duration)
            adjusted_start = boundary_start_for_time(context, adjusted_start)
            decisions.append(
                f"Moved hook from {format_time(old_start)} to {format_time(adjusted_start)} to respect {max_duration:.0f}s."
            )

    return adjusted_start, adjusted_end, decisions


def build_candidate_packet(window, sentences, context_margin):
    context = collect_context(sentences, window["start"], window["end"], margin=context_margin)
    return {
        "candidate_id": window["candidate_id"],
        "local_rank": window.get("local_rank"),
        "local_score": window.get("local_score"),
        "heatmap_score": round(float(window.get("avg_value", 0.0)), 4),
        "range_start": format_time(window["start"]),
        "range_end": format_time(window["end"]),
        "duration_seconds": round(float(window["duration"]), 2),
        "summary": safe_excerpt(window.get("summary") or window.get("text"), limit=220),
        "selection_reasons": window.get("selection_reasons") or [],
        "context": [
            {
                "start": format_time(item["start"]),
                "end": format_time(item["end"]),
                "speaker": item.get("speaker"),
                "text": item.get("text"),
            }
            for item in context
        ],
    }


def rerank_candidates_with_ai_batch(candidate_pool, sentences, *, top_count, model_name, max_duration, context_margin, request_timeout, api_key):
    packets = [build_candidate_packet(candidate, sentences, context_margin) for candidate in candidate_pool]
    prompt = (
        "You are a senior short-form video editor.\n"
        "You are reranking pre-scored short video candidates for one source video.\n"
        "The local system already filtered non-overlapping candidates.\n"
        f"Pick the best {top_count} clips overall.\n"
        "Rules:\n"
        "- favor clear hook -> payoff story shape\n"
        "- keep candidates in their existing order only if quality is similar\n"
        "- you may refine boundaries inside the candidate context\n"
        "- never start or end in the middle of a sentence\n"
        f"- every returned clip must stay under {max_duration:.0f} seconds\n"
        "- if a candidate is already strong, keep its original timestamps\n\n"
        "Return JSON only in this shape:\n"
        "{\n"
        '  "overall_reason": "one short sentence",\n'
        '  "selected": [\n'
        "    {\n"
        '      "candidate_id": "cand_001",\n'
        '      "hook_start": "MM:SS.ss",\n'
        '      "punchline_end": "MM:SS.ss",\n'
        '      "reason": "why this clip made the final cut",\n'
        '      "hook_reason": "why this opening works",\n'
        '      "ending_reason": "why this ending works"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"CANDIDATES_JSON:\n{json.dumps(packets, ensure_ascii=False, indent=2)}\n"
    )

    text = generate_gemini_text(prompt, model_name, api_key, request_timeout)
    parsed = extract_json_object(text or "")
    selected = parsed.get("selected")
    if not isinstance(selected, list):
        raise ValueError("Gemini batch rerank did not return a 'selected' list.")
    return {
        "overall_reason": str(parsed.get("overall_reason", "")).strip(),
        "selected": selected,
    }


def build_fallback_window(window, sentences, context, max_duration, error_message):
    start, end, adjustments = enforce_story_bounds(window["start"], window["end"], context, window, max_duration)
    text = collect_text_for_window(sentences, start, end)
    fallback = dict(window)
    fallback.update(
        {
            "heatmap_start": window.get("heatmap_start", window["start"]),
            "heatmap_end": window.get("heatmap_end", window["end"]),
            "start": start,
            "end": end,
            "duration": end - start,
            "summary": summarize_text(text),
            "text": text,
            "smart_context": False,
            "ai_error": error_message,
            "fallback_reason": "Used local transcript bounds after AI refinement failed.",
        }
    )
    return fallback, adjustments


def build_local_selection(window, sentences, *, index, max_duration, context_margin, reason):
    context = collect_context(sentences, window["start"], window["end"], margin=context_margin)
    fallback_window, adjustments = build_fallback_window(
        window,
        sentences,
        context,
        max_duration,
        reason,
    )
    fallback_window["selection_source"] = "local_ranking"
    fallback_window["selection_reasons"] = window.get("selection_reasons") or []
    fallback_window["local_score"] = window.get("local_score")
    fallback_window["local_rank"] = window.get("local_rank")
    fallback_window["fallback_reason"] = reason
    decision = {
        "index": index,
        "candidate_id": window.get("candidate_id"),
        "selection_source": "local_ranking",
        "local_rank": window.get("local_rank"),
        "local_score": window.get("local_score"),
        "selection_reasons": window.get("selection_reasons") or [],
        "heatmap_start": window["start"],
        "heatmap_end": window["end"],
        "heatmap_start_label": format_time(window["start"]),
        "heatmap_end_label": format_time(window["end"]),
        "context_start_label": format_time(context[0]["start"]) if context else None,
        "context_end_label": format_time(context[-1]["end"]) if context else None,
        "summary_before": safe_excerpt(window.get("summary") or window.get("text")),
        "context_excerpt": safe_excerpt(" ".join(item["text"] for item in context), limit=520),
        "status": "selected_local",
        "error": reason,
        "final_start": fallback_window["start"],
        "final_end": fallback_window["end"],
        "final_start_label": format_time(fallback_window["start"]),
        "final_end_label": format_time(fallback_window["end"]),
        "final_duration": fallback_window["duration"],
        "adjustments": adjustments,
        "summary_after": safe_excerpt(fallback_window["summary"]),
    }
    return fallback_window, decision

def apply_batch_ai_selection(window, ai_choice, sentences, *, index, max_duration, context_margin, overall_reason):
    context = collect_context(sentences, window["start"], window["end"], margin=context_margin)
    hook_start_raw = ai_choice.get("hook_start", format_time(window["start"]))
    punchline_end_raw = ai_choice.get("punchline_end", format_time(window["end"]))
    start, end, adjustments = enforce_story_bounds(
        parse_time(hook_start_raw),
        parse_time(punchline_end_raw),
        context,
        window,
        max_duration,
    )
    text = collect_text_for_window(sentences, start, end)
    refined_window = dict(window)
    refined_window.update(
        {
            "heatmap_start": window["start"],
            "heatmap_end": window["end"],
            "start": start,
            "end": end,
            "duration": end - start,
            "summary": summarize_text(text),
            "text": text,
            "ai_reason": str(ai_choice.get("reason", "")).strip(),
            "hook_reason": str(ai_choice.get("hook_reason", "")).strip(),
            "ending_reason": str(ai_choice.get("ending_reason", "")).strip(),
            "smart_context": True,
            "selection_source": "gemini_batch_rerank",
            "selection_reasons": window.get("selection_reasons") or [],
            "local_score": window.get("local_score"),
            "local_rank": window.get("local_rank"),
        }
    )
    decision = {
        "index": index,
        "candidate_id": window.get("candidate_id"),
        "selection_source": "gemini_batch_rerank",
        "local_rank": window.get("local_rank"),
        "local_score": window.get("local_score"),
        "selection_reasons": window.get("selection_reasons") or [],
        "heatmap_start": window["start"],
        "heatmap_end": window["end"],
        "heatmap_start_label": format_time(window["start"]),
        "heatmap_end_label": format_time(window["end"]),
        "context_start_label": format_time(context[0]["start"]) if context else None,
        "context_end_label": format_time(context[-1]["end"]) if context else None,
        "status": "selected_by_ai_batch",
        "ai_hook_start": hook_start_raw,
        "ai_punchline_end": punchline_end_raw,
        "final_start": start,
        "final_end": end,
        "final_start_label": format_time(start),
        "final_end_label": format_time(end),
        "final_duration": end - start,
        "reason": refined_window.get("ai_reason"),
        "hook_reason": refined_window.get("hook_reason"),
        "ending_reason": refined_window.get("ending_reason"),
        "overall_reason": overall_reason,
        "adjustments": adjustments,
        "summary_after": safe_excerpt(refined_window["summary"]),
    }
    return refined_window, decision


def save_cutting_log(log_path, log):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as file_handle:
        json.dump(log, file_handle, ensure_ascii=False, indent=2)


def resolve_content_routing(args):
    content_mode = normalize_content_type_mode(args.content_type)
    profile_path = Path(args.content_profile) if args.content_profile else None
    profile_loaded = False
    classification = None

    if content_mode == "auto" and profile_path and profile_path.exists():
        try:
            classification = load_content_profile(profile_path)
            profile_loaded = True
        except Exception:
            classification = None

    if classification is None:
        result = classify_content(
            args.transcript,
            args.heatmap,
            video_path=args.video,
            forced_content_type=content_mode,
        )
        classification = result.to_dict()
        if profile_path:
            save_content_profile(result, profile_path)
            profile_loaded = False

    strategy_name = (
        classification.get("strategy_name")
        or classification.get("content_type")
        or "generic"
    )
    strategy = get_strategy(strategy_name)
    routing = {
        "content_type": classification.get("content_type", "generic"),
        "confidence": round(float(classification.get("confidence", 0.0) or 0.0), 4),
        "reasons": classification.get("reasons") or [],
        "source": "cached_profile" if profile_loaded else (classification.get("source") or "heuristic_classifier"),
        "classifier_source": classification.get("source") or "heuristic_classifier",
        "forced_content_type": classification.get("forced_content_type"),
        "loaded_from_profile": profile_loaded,
        "features": classification.get("features") or {},
        "scores": classification.get("scores") or {},
        "strategy": strategy.to_dict(),
    }
    return routing, strategy


def rerank_cuts_with_ai(
    candidate_pool,
    sentences,
    *,
    ai_mode,
    top_count,
    total_candidates,
    model_name,
    api_key,
    max_duration,
    context_margin,
    log_path,
    request_timeout,
    selection_context=None,
):
    local_candidate_snapshots = [
        {
            "candidate_id": candidate.get("candidate_id"),
            "local_rank": candidate.get("local_rank"),
            "local_score": candidate.get("local_score"),
            "local_features": candidate.get("local_features") or {},
            "avg_value": candidate.get("avg_value"),
            "start": candidate.get("start"),
            "end": candidate.get("end"),
            "duration": candidate.get("duration"),
            "summary": candidate.get("summary"),
            "selection_reasons": candidate.get("selection_reasons") or [],
        }
        for candidate in candidate_pool
    ]

    log = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strategy": "local_first_selector",
        "ai_mode": ai_mode,
        "model": model_name,
        "context_margin_seconds": context_margin,
        "max_duration_seconds": max_duration,
        "request_timeout_seconds": request_timeout,
        "clips_requested": top_count,
        "total_candidates_generated": total_candidates,
        "local_candidate_pool_size": len(candidate_pool),
        "local_candidate_ids": [candidate.get("candidate_id") for candidate in candidate_pool],
        "local_candidate_pool": local_candidate_snapshots,
        "decisions": [],
    }
    if selection_context:
        log["content_routing"] = selection_context
    if not candidate_pool:
        log["status"] = "no_windows"
        save_cutting_log(log_path, log)
        return candidate_pool, log

    curl_available = bool(shutil.which("curl.exe") or shutil.which("curl"))
    ai_ready = bool(api_key and (genai is not None or curl_available))
    transport = os.environ.get("GEMINI_TRANSPORT", "").strip().lower()
    if not allows_gemini(ai_mode):
        log["ai_status"] = "skipped"
        log["ai_reason"] = "AI mode is local_only."
    elif ai_ready:
        configure_gemini(api_key)
        log["ai_status"] = "ready"
        log["ai_transport"] = transport or ("curl" if os.name == "nt" else "sdk")
    else:
        log["ai_status"] = "unavailable"
        if genai is None:
            log["ai_reason"] = "Gemini client library is unavailable."
        else:
            log["ai_reason"] = "Gemini API key is missing."

    if requires_gemini(ai_mode) and not ai_ready:
        raise RuntimeError(log.get("ai_reason", "Gemini is required but unavailable."))

    local_top = candidate_pool[:top_count]
    final_windows = []
    decisions = []
    refined_count = 0
    batch_reason = ""

    if allows_gemini(ai_mode) and ai_ready:
        print(
            f"  Gemini batch rerank: evaluating {len(candidate_pool)} local candidates "
            f"(timeout={request_timeout}s)"
        )
        try:
            ai_result = rerank_candidates_with_ai_batch(
                candidate_pool,
                sentences,
                top_count=top_count,
                model_name=model_name,
                max_duration=max_duration,
                context_margin=context_margin,
                request_timeout=request_timeout,
                api_key=api_key,
            )
            batch_reason = ai_result.get("overall_reason", "")
            selected_by_id = {}
            for item in ai_result.get("selected", []):
                candidate_id = str(item.get("candidate_id") or "").strip()
                if candidate_id and candidate_id not in selected_by_id:
                    selected_by_id[candidate_id] = item

            if not selected_by_id:
                raise ValueError("Gemini batch rerank returned no valid candidate ids.")

            for candidate in candidate_pool:
                choice = selected_by_id.get(candidate.get("candidate_id"))
                if not choice:
                    continue
                refined_window, decision = apply_batch_ai_selection(
                    candidate,
                    choice,
                    sentences,
                    index=len(final_windows) + 1,
                    max_duration=max_duration,
                    context_margin=context_margin,
                    overall_reason=batch_reason,
                )
                final_windows.append(refined_window)
                decisions.append(decision)
                refined_count += 1
                if len(final_windows) == top_count:
                    break
        except Exception as exc:
            log["ai_status"] = "fallback_local_only"
            log["ai_reason"] = str(exc)
            print(f"  Warning: Gemini batch rerank failed, using local ranking only. Reason: {exc}")

    if len(final_windows) < top_count:
        fallback_reason = log.get("ai_reason", "Using local ranking.")
        used_ids = {window.get("candidate_id") for window in final_windows}
        for candidate in local_top:
            if candidate.get("candidate_id") in used_ids:
                continue
            local_window, decision = build_local_selection(
                candidate,
                sentences,
                index=len(final_windows) + 1,
                max_duration=max_duration,
                context_margin=context_margin,
                reason=fallback_reason,
            )
            final_windows.append(local_window)
            decisions.append(decision)
            if len(final_windows) == top_count:
                break

    decisions.sort(key=lambda item: item["index"])
    log["decisions"] = decisions
    log["status"] = "completed"
    log["batch_rerank_used"] = refined_count > 0
    log["batch_rerank_reason"] = batch_reason
    log["clips_refined_with_ai"] = refined_count
    log["clips_with_local_fallback"] = max(0, len(final_windows) - refined_count)
    save_cutting_log(log_path, log)
    return final_windows, log


def build_candidates(sentences, heatmap, starts, min_duration, max_duration):
    sentence_boundaries = sorted({boundary for sentence in sentences for boundary in (sentence["start"], sentence["end"])})
    candidates = []

    for window_start in sentence_boundaries:
        min_end = window_start + min_duration
        max_end = window_start + max_duration
        valid_ends = [boundary for boundary in sentence_boundaries if min_end <= boundary <= max_end]
        if not valid_ends:
            continue

        for window_end in valid_ends:
            avg_value = average_heatmap_value(heatmap, starts, window_start, window_end)
            if avg_value <= 0:
                continue
            text_snippet = collect_text_for_window(sentences, window_start, window_end)
            candidates.append(
                {
                    "start": window_start,
                    "end": window_end,
                    "duration": window_end - window_start,
                    "avg_value": avg_value,
                    "summary": summarize_text(text_snippet),
                    "text": text_snippet,
                }
            )

    return candidates


def select_non_overlapping(candidates, count=5, score_key="avg_value"):
    selected = []
    for candidate in sorted(
        candidates,
        key=lambda item: (
            float(item.get(score_key, 0.0)),
            float(item.get("avg_value", 0.0)),
            -float(item.get("duration", 0.0)),
        ),
        reverse=True,
    ):
        overlaps = any(
            not (candidate["end"] <= chosen["start"] or candidate["start"] >= chosen["end"])
            for chosen in selected
        )
        if overlaps:
            continue
        selected.append(candidate)
        if len(selected) == count:
            break
    return selected


def save_top_windows(windows, output_path):
    with open(output_path, "w", encoding="utf-8") as file_handle:
        json.dump(windows, file_handle, ensure_ascii=False, indent=2)


def parse_args():
    parser = argparse.ArgumentParser(description="Analyze viral fragments using transcript and heatmap.")
    parser.add_argument("--transcript", default="transcripts/final_transcript.json", help="Transcript JSON path")
    parser.add_argument("--heatmap", default="metadata/heatmap.json", help="Heatmap JSON path")
    parser.add_argument("--video", default=None, help="Optional video path for content classification")
    parser.add_argument("--content-profile", default=None, help="Optional cached content profile JSON path")
    parser.add_argument(
        "--content-type",
        default="auto",
        choices=VALID_CONTENT_TYPE_MODES,
        help="auto, podcast, gameplay, tutorial or generic",
    )
    parser.add_argument("--min-duration", type=float, default=30.0, help="Minimum window duration in seconds")
    parser.add_argument("--max-duration", type=float, default=60.0, help="Maximum window duration in seconds")
    parser.add_argument("--top", type=int, default=5, help="How many clips to export")
    parser.add_argument("--save-json", default=None, help="Output JSON path for selected windows")
    parser.add_argument("--model", default="models/gemini-2.5-flash", help="Gemini model for Smart Context Cutter")
    parser.add_argument("--ai-mode", default="gemini_optional", choices=VALID_AI_MODES, help="Selection mode: local_only, gemini_optional, gemini_enabled")
    parser.add_argument("--context-margin", type=float, default=20.0, help="Transcript margin around each window")
    parser.add_argument("--request-timeout", type=float, default=75.0, help="Gemini timeout in seconds per selected scene")
    parser.add_argument("--rerank-pool-size", type=int, default=0, help="How many locally ranked non-overlapping candidates to expose to Gemini batch rerank")
    parser.add_argument("--cutting-log", default="metadata/cutting_logic.json", help="Output log for cutting logic")
    parser.add_argument("--skip-smart-context", action="store_true", help="Skip Gemini refinement and keep local windows")
    return parser.parse_args()


def main():
    args = parse_args()
    bootstrap_ssl_certificates()

    if load_dotenv is not None:
        dotenv_path = Path(__file__).parent / ".env"
        if dotenv_path.exists():
            load_dotenv(dotenv_path)

    transcript = load_transcript(args.transcript)
    heatmap = load_heatmap(args.heatmap)
    sentences = build_sentence_boundaries(transcript)
    heatmap_index, heatmap_starts = build_heatmap_index(heatmap)
    ai_mode = AI_MODE_LOCAL_ONLY if args.skip_smart_context else normalize_ai_mode(args.ai_mode)
    content_routing, strategy = resolve_content_routing(args)

    print(
        f"  Content classification: {content_routing['content_type']} "
        f"(confidence={content_routing['confidence']:.2f}, source={content_routing['source']})"
    )
    if content_routing["reasons"]:
        print(f"  Routing reasons: {', '.join(content_routing['reasons'])}")
    print(
        f"  Strategy selected: {strategy.name} | "
        f"weights={json.dumps(strategy.score_weights, ensure_ascii=False, sort_keys=True)}"
    )

    candidates = build_candidates(sentences, heatmap_index, heatmap_starts, args.min_duration, args.max_duration)
    if not candidates:
        raise SystemExit("No candidate windows were found in the 30-60 second range.")

    scored_candidates = score_candidates(
        candidates,
        transcript,
        heatmap_index,
        score_weights=strategy.score_weights,
        strategy_name=strategy.name,
    )
    for index, candidate in enumerate(scored_candidates, start=1):
        candidate["candidate_id"] = f"cand_{index:03d}"

    rerank_pool_size = args.rerank_pool_size or max(args.top, args.top * 3)
    rerank_pool_size = max(args.top, rerank_pool_size)
    candidate_pool = select_non_overlapping(scored_candidates, count=rerank_pool_size, score_key="local_score")
    if not candidate_pool:
        raise SystemExit("Could not select non-overlapping windows.")

    print(f"  Local candidate generation: {len(candidates)} windows")
    print(f"  Local scoring ready: top pool {len(candidate_pool)} / requested clips {args.top}")

    if allows_gemini(ai_mode):
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
        top_windows, cutting_log = rerank_cuts_with_ai(
            candidate_pool,
            sentences,
            ai_mode=ai_mode,
            top_count=args.top,
            total_candidates=len(candidates),
            model_name=args.model,
            api_key=api_key,
            max_duration=args.max_duration,
            context_margin=args.context_margin,
            log_path=Path(args.cutting_log),
            request_timeout=args.request_timeout,
            selection_context=content_routing,
        )
        fallback_count = cutting_log.get("clips_with_local_fallback", 0)
        if fallback_count:
            print(f"  Selection fallback: {fallback_count} clip(s) kept from the local ranking.")
    else:
        top_windows, cutting_log = rerank_cuts_with_ai(
            candidate_pool,
            sentences,
            ai_mode=ai_mode,
            top_count=args.top,
            total_candidates=len(candidates),
            model_name=args.model,
            api_key="",
            max_duration=args.max_duration,
            context_margin=args.context_margin,
            log_path=Path(args.cutting_log),
            request_timeout=args.request_timeout,
            selection_context=content_routing,
        )
        print("  AI rerank skipped: local_only mode is active.")

    print(f"\nTop {len(top_windows)} moments for Shorts:")
    for index, window in enumerate(top_windows, start=1):
        print(f"Clip {index}:")
        print(f'  Range: {format_time(window["start"])} - {format_time(window["end"])}')
        print(f'  Avg heatmap score: {window["avg_value"]:.4f}')
        print(f'  Local score: {window.get("local_score", 0):.2f}')
        print(f'  Duration: {window["duration"]:.1f}s')
        reasons = ", ".join(window.get("selection_reasons") or [])
        if reasons:
            print(f"  Why selected: {reasons}")
        if window.get("selection_source") == "gemini_batch_rerank":
            print(f'  Gemini reason: {window.get("ai_reason") or "batch rerank selected this clip"}')
        print(f'  Summary: {window["summary"]}')
        print()

    if args.save_json:
        out_path = Path(args.save_json)
        save_top_windows(top_windows, out_path)
        print(f"Saved selected windows to: {out_path}")


if __name__ == "__main__":
    main()
