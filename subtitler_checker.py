#!/usr/bin/env python3
"""
subtitler_checker.py — AI Subtitler Checker

Sprawdza, czy transkrypcja nadaje się do generowania napisów:
- waliduje timestampy, długości segmentów i czytelność tekstu lokalnie,
- porównuje krótkie próbki audio z odpowiadającą im transkrypcją przez Gemini,
- zapisuje raport JSON z ostrzeżeniami, błędami i podejrzeniami halucynacji.

Użycie:
  python subtitler_checker.py \
    --audio input/audio.mp3 \
    --transcript transcripts/final_transcript.json \
    --report metadata/subtitle_check_report.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

try:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=FutureWarning)
        import google.generativeai as genai
except Exception:
    try:
        import google.genai as genai
    except Exception:
        genai = None


WORD_RE = re.compile(r"[0-9A-Za-zÀ-žąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+", re.UNICODE)
ARTIFACT_RE = re.compile(
    r"(```|^\s*json\b|transkrypcj[aię]|nie mogę|nie jestem w stanie|"
    r"brak dźwięku|w załączonym audio)",
    re.IGNORECASE,
)
RETRY_DELAYS_SECONDS = (5, 10, 20)
COMMON_NAMES = {
    "adam",
    "ania",
    "bartek",
    "dawid",
    "ewa",
    "jan",
    "kamil",
    "kasia",
    "maciek",
    "marek",
    "michal",
    "michał",
    "olaf",
    "pawel",
    "paweł",
    "piotr",
    "tomek",
}


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
    delay = RETRY_DELAYS_SECONDS[min(attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)]
    reason = "429/rate limit" if is_rate_limit_error(exc) else "temporary API error"
    print(f"  ⚠ {operation}: {reason}, retry za {delay}s ({attempt}/{max_retries})")
    time.sleep(delay)


def generate_content_with_backoff(model: Any, payload: List[Any], operation: str, max_retries: int = 3) -> Any:
    for attempt in range(1, max_retries + 1):
        try:
            return model.generate_content(payload)
        except Exception as exc:
            if attempt == max_retries:
                raise
            wait_before_retry(exc, attempt, max_retries, operation)
    raise RuntimeError(f"Gemini call failed: {operation}")


def upload_file_with_backoff(path: Path, operation: str, max_retries: int = 3) -> Any:
    for attempt in range(1, max_retries + 1):
        try:
            return genai.upload_file(str(path))
        except Exception as exc:
            if attempt == max_retries:
                raise
            wait_before_retry(exc, attempt, max_retries, operation)
    raise RuntimeError(f"Gemini upload failed: {operation}")


def parse_time(value: Any) -> float:
    """Parsuje czas z formatów seconds, MM:SS, HH:MM:SS i wariantów z ms."""
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(",", ".")
    if not text:
        raise ValueError("pusty czas")

    parts = text.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60 + float(seconds)
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError as exc:
        raise ValueError(f"niepoprawny czas: {value}") from exc

    raise ValueError(f"niepoprawny czas: {value}")


def format_time(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:05.2f}"
    return f"{minutes:02d}:{secs:05.2f}"


def tokenize(text: str) -> List[str]:
    return WORD_RE.findall(text.lower())


def collapse_duplicate_words(text: str) -> str:
    words = text.split()
    cleaned = []
    previous = ""
    for word in words:
        comparable = re.sub(r"[^\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+", "", word).lower()
        if comparable and comparable == previous:
            continue
        cleaned.append(word)
        previous = comparable
    return " ".join(cleaned)


def apply_basic_text_fixes(text: str) -> Tuple[str, List[str]]:
    fixes = []
    fixed = re.sub(r"\s+", " ", text).strip()
    if fixed != text:
        fixes.append("whitespace")

    deduped = collapse_duplicate_words(fixed)
    if deduped != fixed:
        fixed = deduped
        fixes.append("duplicate_words")

    if fixed:
        capitalized = fixed[0].upper() + fixed[1:]
        if capitalized != fixed:
            fixed = capitalized
            fixes.append("sentence_capitalization")

    def capitalize_name(match: re.Match) -> str:
        return match.group(1) + match.group(2).capitalize()

    name_pattern = r"(^|[\s\"'(\[])\b(" + "|".join(re.escape(name) for name in sorted(COMMON_NAMES)) + r")\b"
    named = re.sub(name_pattern, capitalize_name, fixed, flags=re.IGNORECASE)
    if named != fixed:
        fixed = named
        fixes.append("name_capitalization")

    if fixed and fixed[-1] not in ".!?…":
        fixed += "."
        fixes.append("terminal_punctuation")

    return fixed, fixes


def parse_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"true", "tak", "yes", "1"}:
        return True
    if normalized in {"false", "nie", "no", "0"}:
        return False
    return None


def load_transcript(path: Path) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "segments" in data:
        data = data["segments"]
    if not isinstance(data, list):
        raise ValueError("Transkrypcja musi być listą segmentów albo obiektem z kluczem 'segments'.")
    return data


def get_duration(path: Path) -> Optional[float]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except Exception:
        return None


def add_issue(
    issues: List[Dict[str, Any]],
    severity: str,
    code: str,
    message: str,
    *,
    index: Optional[int] = None,
    start: Optional[float] = None,
    end: Optional[float] = None,
    text: Optional[str] = None,
) -> None:
    issue: Dict[str, Any] = {
        "severity": severity,
        "code": code,
        "message": message,
    }
    if index is not None:
        issue["segment_index"] = index
    if start is not None:
        issue["start"] = start
        issue["start_label"] = format_time(start)
    if end is not None:
        issue["end"] = end
        issue["end_label"] = format_time(end)
    if text:
        issue["text"] = text[:260]
    issues.append(issue)


def normalize_transcript(
    raw_segments: List[Dict[str, Any]],
    audio_duration: Optional[float],
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    normalized = []
    previous_end: Optional[float] = None
    previous_text = ""

    if not raw_segments:
        add_issue(issues, "error", "EMPTY_TRANSCRIPT", "Transkrypcja jest pusta.")
        return normalized

    for index, segment in enumerate(raw_segments):
        text = str(segment.get("text", "")).replace("\n", " ").strip()
        try:
            start = parse_time(segment["start"])
            end = parse_time(segment["end"])
        except Exception as exc:
            add_issue(
                issues,
                "error",
                "INVALID_TIME",
                f"Nie da się sparsować czasu segmentu: {exc}.",
                index=index,
                text=text,
            )
            continue

        if end < start - 0.05:
            add_issue(
                issues,
                "error",
                "NEGATIVE_DURATION",
                "Segment kończy się przed swoim początkiem.",
                index=index,
                start=start,
                end=end,
                text=text,
            )
            continue

        if end <= start + 0.05:
            add_issue(
                issues,
                "error",
                "ZERO_DURATION",
                "Segment musi spełniać warunek end > start.",
                index=index,
                start=start,
                end=end,
                text=text,
            )
            end = start + 0.5

        if start < -0.05:
            add_issue(
                issues,
                "error",
                "NEGATIVE_TIME",
                "Segment zaczyna się przed początkiem audio.",
                index=index,
                start=start,
                end=end,
                text=text,
            )

        if audio_duration is not None and end > audio_duration + 2.0:
            add_issue(
                issues,
                "error",
                "OUT_OF_AUDIO_RANGE",
                "Segment wychodzi poza długość pliku audio.",
                index=index,
                start=start,
                end=end,
                text=text,
            )

        if previous_end is not None and start < previous_end - 0.25:
            add_issue(
                issues,
                "warning",
                "OVERLAPPING_SEGMENTS",
                "Segment znacząco nachodzi na poprzedni segment.",
                index=index,
                start=start,
                end=end,
                text=text,
            )

        words = tokenize(text)
        duration = end - start
        if not text:
            add_issue(
                issues,
                "warning",
                "EMPTY_TEXT",
                "Segment nie zawiera tekstu.",
                index=index,
                start=start,
                end=end,
            )
        elif not words:
            add_issue(
                issues,
                "warning",
                "NO_WORDS",
                "Tekst segmentu nie zawiera rozpoznawalnych słów.",
                index=index,
                start=start,
                end=end,
                text=text,
            )
        else:
            words_per_second = len(words) / max(duration, 0.01)
            if words_per_second > 7.0:
                add_issue(
                    issues,
                    "warning",
                    "TOO_MANY_WORDS_FOR_DURATION",
                    f"Segment ma bardzo wysokie tempo: {words_per_second:.1f} słów/s.",
                    index=index,
                    start=start,
                    end=end,
                    text=text,
                )
            elif len(words) >= 8 and words_per_second < 0.35:
                add_issue(
                    issues,
                    "warning",
                    "TEXT_TOO_SLOW_FOR_DURATION",
                    f"Segment ma nietypowo długi czas względem tekstu: {words_per_second:.1f} słów/s.",
                    index=index,
                    start=start,
                    end=end,
                    text=text,
                )

            unique_ratio = len(set(words)) / max(len(words), 1)
            if len(words) >= 6 and unique_ratio <= 0.34:
                add_issue(
                    issues,
                    "warning",
                    "REPEATED_WORDS",
                    "Tekst wygląda na zapętlony albo sztucznie powtórzony.",
                    index=index,
                    start=start,
                    end=end,
                    text=text,
                )

        if re.search(r"(.)\1{7,}", text):
            add_issue(
                issues,
                "warning",
                "REPEATED_CHARACTERS",
                "Tekst zawiera podejrzanie długie powtórzenie znaku.",
                index=index,
                start=start,
                end=end,
                text=text,
            )

        if text and ARTIFACT_RE.search(text):
            add_issue(
                issues,
                "warning",
                "MODEL_ARTIFACT",
                "Tekst wygląda jak artefakt odpowiedzi modelu, nie jak wypowiedź z audio.",
                index=index,
                start=start,
                end=end,
                text=text,
            )

        if text:
            non_text_chars = sum(1 for char in text if not (char.isalnum() or char.isspace() or char in ".,!?;:'\"-()[]"))
            if non_text_chars / max(len(text), 1) > 0.18:
                add_issue(
                    issues,
                    "warning",
                    "SUSPICIOUS_CHARACTERS",
                    "Tekst zawiera dużo nietypowych znaków.",
                    index=index,
                    start=start,
                    end=end,
                    text=text,
                )

        if previous_text and text and text.lower() == previous_text.lower() and duration > 1.0:
            add_issue(
                issues,
                "warning",
                "DUPLICATED_ADJACENT_TEXT",
                "Dwa sąsiednie segmenty mają identyczny tekst.",
                index=index,
                start=start,
                end=end,
                text=text,
            )

        normalized.append(
            {
                "index": index,
                "start": start,
                "end": end,
                "duration": duration,
                "text": text,
                "words": words,
            }
        )
        previous_end = end
        previous_text = text

    normalized.sort(key=lambda item: item["start"])

    if normalized:
        first_start = normalized[0]["start"]
        last_end = normalized[-1]["end"]
        if first_start > 15.0:
            add_issue(
                issues,
                "warning",
                "LATE_TRANSCRIPT_START",
                "Pierwszy segment zaczyna się daleko od początku audio.",
                start=first_start,
            )
        if audio_duration is not None:
            trailing_gap = audio_duration - last_end
            if trailing_gap > 30.0:
                add_issue(
                    issues,
                    "warning",
                    "EARLY_TRANSCRIPT_END",
                    "Transkrypcja kończy się dużo wcześniej niż plik audio.",
                    start=last_end,
                    end=audio_duration,
                )

    return normalized


def text_for_window(segments: List[Dict[str, Any]], start: float, end: float) -> str:
    parts = []
    for segment in segments:
        if segment["end"] <= start:
            continue
        if segment["start"] >= end:
            break
        parts.append(segment["text"])
    return " ".join(part for part in parts if part).strip()


def clip_window(center: float, duration: float, max_end: float) -> Tuple[float, float]:
    half = duration / 2.0
    start = max(0.0, center - half)
    end = start + duration
    if end > max_end:
        end = max_end
        start = max(0.0, end - duration)
    return start, end


def select_sample_windows(
    segments: List[Dict[str, Any]],
    issues: List[Dict[str, Any]],
    *,
    max_samples: int,
    sample_duration: float,
    audio_duration: Optional[float],
) -> List[Dict[str, Any]]:
    if not segments or max_samples <= 0:
        return []

    transcript_end = max(segment["end"] for segment in segments)
    max_end = max(audio_duration or 0.0, transcript_end)
    if max_end <= 0:
        return []

    centers: List[float] = []

    risky_codes = {
        "TOO_MANY_WORDS_FOR_DURATION",
        "REPEATED_WORDS",
        "DUPLICATED_ADJACENT_TEXT",
        "MODEL_ARTIFACT",
        "SUSPICIOUS_CHARACTERS",
        "OVERLAPPING_SEGMENTS",
    }
    for issue in issues:
        if issue.get("code") not in risky_codes:
            continue
        start = issue.get("start")
        end = issue.get("end")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            centers.append((start + end) / 2.0)
        elif isinstance(start, (int, float)):
            centers.append(start)

    non_empty = [segment for segment in segments if segment["text"]]
    if non_empty:
        step = max(1, len(non_empty) // max(max_samples, 1))
        for index in range(0, len(non_empty), step):
            segment = non_empty[index]
            centers.append((segment["start"] + segment["end"]) / 2.0)
            if len(centers) >= max_samples * 3:
                break

        for segment in sorted(non_empty, key=lambda item: len(item["words"]) / max(item["duration"], 0.01), reverse=True)[:max_samples]:
            centers.append((segment["start"] + segment["end"]) / 2.0)

    windows: List[Dict[str, Any]] = []
    seen_starts: List[float] = []
    for center in centers:
        start, end = clip_window(center, sample_duration, max_end)
        if end - start < 1.0:
            continue
        if any(abs(start - seen) < sample_duration * 0.45 for seen in seen_starts):
            continue
        expected_text = text_for_window(segments, start, end)
        if not expected_text:
            continue
        windows.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "start_label": format_time(start),
                "end_label": format_time(end),
                "expected_text": expected_text[:1200],
            }
        )
        seen_starts.append(start)
        if len(windows) >= max_samples:
            break

    return windows


def extract_audio_sample(audio_path: Path, output_path: Path, start: float, end: float) -> None:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        str(max(0.0, start)),
        "-t",
        str(max(0.1, end - start)),
        "-i",
        str(audio_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)


def extract_json_object(text: str) -> Dict[str, Any]:
    if not text:
        raise ValueError("Pusta odpowiedź modelu.")

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
        candidate = cleaned[start : end + 1]
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed

    raise ValueError(f"Nie udało się wyciągnąć JSON z odpowiedzi modelu: {text[:200]}")


def configure_gemini(api_key: str) -> None:
    if genai is None:
        raise RuntimeError("google-generativeai nie jest zainstalowane.")
    if hasattr(genai, "configure"):
        genai.configure(api_key=api_key)


def ask_gemini_to_check_sample(
    sample_path: Path,
    expected_text: str,
    *,
    model_name: str,
    max_retries: int = 3,
) -> Dict[str, Any]:
    prompt = (
        "Jesteś weryfikatorem napisów do krótkich filmów. "
        "Porównaj załączony krótki fragment audio z oczekiwaną transkrypcją poniżej. "
        "Sprawdź, czy słowa z transkrypcji naprawdę występują w audio i czy tekst pasuje czasowo do próbki. "
        "Ignoruj interpunkcję, wielkość liter, drobne wahania odmiany oraz oczywiste wypełniacze. "
        "Nie zgaduj na siłę: jeśli audio jest niewyraźne, obniż confidence zamiast wymyślać słowa.\n\n"
        f"OCZEKIWANA_TRANSKRYPCJA:\n{expected_text}\n\n"
        "Zwróć WYŁĄCZNIE JSON jako obiekt z kluczami: "
        "\"matches_audio\" boolean, \"timing_ok\" boolean, \"confidence\" number 0..1, "
        "\"heard_text\" string, \"hallucinated_words\" array, \"missing_words\" array, \"notes\" string."
    )

    uploaded = upload_file_with_backoff(sample_path, "Gemini sample upload", max_retries=max_retries)
    model = genai.GenerativeModel(model_name)
    result = generate_content_with_backoff(
        model,
        [uploaded, prompt],
        "Gemini audio subtitle check",
        max_retries=max_retries,
    )
    text = getattr(result, "text", None)
    if not text and hasattr(result, "candidates") and result.candidates:
        text = str(result.candidates[0])
    parsed = extract_json_object(text or "")
    return normalize_ai_result(parsed)


def retranscribe_window(audio_path: Path, start: float, end: float, api_key: str, model_name: str) -> Optional[str]:
    """Retranskrybuje krótkie okno audio używając Gemini."""
    with tempfile.TemporaryDirectory(prefix="retranscribe_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        sample_path = tmpdir_path / "retranscribe.mp3"

        extract_audio_sample(audio_path, sample_path, start, end)

        prompt = (
            "Przesłuchaj ten krótki fragment audio i dokładnie transkrybuj wszystko, co słyszysz. "
            "Zwróć WYŁĄCZNIE tekst transkrypcji, bez żadnego formatowania ani wyjaśnień. "
            "Nie dodawaj znaczków czasowych ani innych metadanych."
        )

        uploaded = upload_file_with_backoff(sample_path, "Gemini retranscription upload")
        model = genai.GenerativeModel(model_name)
        result = generate_content_with_backoff(model, [uploaded, prompt], "Gemini retranscription")
        text = getattr(result, "text", None)
        if text:
            return text.strip()
    return None


def fix_local_issues(segments: List[Dict[str, Any]], issues: List[Dict[str, Any]], fix_log: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Naprawia lokalne problemy z timingiem i strukturą."""
    fixed_segments = []
    prev_end = None

    for i, segment in enumerate(segments):
        fixed_segment = segment.copy()
        start = parse_time(segment.get("start"))
        end = parse_time(segment.get("end"))
        text = segment.get("text", "").strip()

        if prev_end is not None and start < prev_end - 0.25:
            original_start = start
            start = prev_end
            fixed_segment["start"] = format_time(start)
            fix_log["fixes_applied"].append({
                "type": "overlap_fix",
                "segment_index": i,
                "original_start": format_time(original_start),
                "fixed_start": format_time(start),
                "reason": "Segment nachodził na poprzedni segment",
            })

        if end <= start:
            new_end = start + 0.5
            fixed_segment["end"] = format_time(new_end)
            fix_log["fixes_applied"].append({
                "type": "invalid_duration_fix",
                "segment_index": i,
                "original_start": format_time(parse_time(segment.get("start"))),
                "original_end": format_time(end),
                "fixed_end": format_time(new_end),
                "reason": "Segment nie spełniał warunku end > start",
            })
            end = new_end

        fixed_text, text_fixes = apply_basic_text_fixes(text)
        if fixed_text != text:
            fixed_segment["text"] = fixed_text
            fix_log["fixes_applied"].append({
                "type": "text_cleanup",
                "segment_index": i,
                "original_text": text,
                "fixed_text": fixed_text,
                "fixes": text_fixes,
                "reason": "Lokalna korekta tekstu przed generowaniem napisów",
            })

        fixed_segments.append(fixed_segment)
        prev_end = parse_time(fixed_segment["end"])

    return fixed_segments


def fix_ai_issues(
    segments: List[Dict[str, Any]],
    samples: List[Dict[str, Any]],
    audio_path: Path,
    api_key: str,
    model_name: str,
    fix_log: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Naprawia problemy wykryte przez AI (timing mismatches i hallucinations)."""
    configure_gemini(api_key)
    fixed_segments = segments.copy()

    for sample in samples:
        if sample.get("status") != "checked":
            continue

        start = sample["start"]
        end = sample["end"]
        timing_ok = sample.get("timing_ok")
        hallucinated_words = sample.get("hallucinated_words", [])
        matches_audio = sample.get("matches_audio")
        window_segments = []
        for i, seg in enumerate(fixed_segments):
            seg_start = parse_time(seg["start"])
            seg_end = parse_time(seg["end"])
            if seg_end > start and seg_start < end:
                window_segments.append((i, seg))

        # Napraw timing mismatch
        if timing_ok is False:
            if window_segments:
                # Dostosuj timing na podstawie heard_text (jeśli dostępne)
                heard_text = sample.get("heard_text", "")
                if heard_text:
                    # Prosty heurystyka: przesuń segmenty proporcjonalnie
                    total_window_duration = end - start
                    heard_words = tokenize(heard_text)
                    if heard_words:
                        # Załóż, że słowa są równomiernie rozłożone
                        words_per_sec = len(heard_words) / total_window_duration
                        for idx, seg in window_segments:
                            seg_words = tokenize(seg["text"])
                            expected_duration = len(seg_words) / words_per_sec if words_per_sec > 0 else 1.0
                            current_duration = parse_time(seg["end"]) - parse_time(seg["start"])
                            if abs(current_duration - expected_duration) > 0.5:
                                new_end = parse_time(seg["start"]) + expected_duration
                                fixed_segments[idx]["end"] = format_time(new_end)
                                fix_log["fixes_applied"].append({
                                    "type": "timing_adjustment",
                                    "segment_index": idx,
                                    "original_end": seg["end"],
                                    "fixed_end": format_time(new_end),
                                    "reason": f"Timing mismatch w oknie {format_time(start)}-{format_time(end)}",
                                })

        logic_check = sample.get("logic_check") or {}
        corrected_text = str(logic_check.get("corrected_text") or "").strip()
        expected_text = str(sample.get("expected_text") or "").strip()
        if logic_check and logic_check.get("has_errors") is True and corrected_text and corrected_text != expected_text:
            if len(window_segments) == 1:
                idx, seg = window_segments[0]
                original_text = str(seg.get("text", "")).strip()
                if corrected_text != original_text:
                    fixed_segments[idx]["text"] = corrected_text
                    fix_log["fixes_applied"].append({
                        "type": "ai_logic_text_fix",
                        "segment_index": idx,
                        "original_text": original_text,
                        "fixed_text": corrected_text,
                        "window": f"{format_time(start)}-{format_time(end)}",
                        "reason": logic_check.get("notes") or "Gemini wykrył błąd gramatyczny/kontekstowy",
                    })
            else:
                fix_log["fixes_applied"].append({
                    "type": "ai_logic_suggestion",
                    "window": f"{format_time(start)}-{format_time(end)}",
                    "original_text": expected_text,
                    "suggested_text": corrected_text,
                    "reason": "Sugestia obejmuje wiele segmentów, więc nie została automatycznie rozbita",
                })

        # Napraw hallucinations
        if hallucinated_words or (matches_audio is False and not timing_ok):
            # Retranskrybuj wycinek
            try:
                retranscript = retranscribe_window(audio_path, start, end, api_key, model_name)
                if retranscript:
                    # Zamień segmenty w tym oknie
                    new_segments = []
                    for i, seg in enumerate(fixed_segments):
                        seg_start = parse_time(seg["start"])
                        seg_end = parse_time(seg["end"])
                        if seg_end <= start or seg_start >= end:
                            new_segments.append(seg)
                        else:
                            # Zamień na nowy segment
                            new_segments.append({
                                "start": format_time(max(seg_start, start)),
                                "end": format_time(min(seg_end, end)),
                                "text": retranscript,
                            })
                            fix_log["fixes_applied"].append({
                                "type": "retranscription",
                                "segment_index": i,
                                "original_text": seg["text"][:100],
                                "fixed_text": retranscript[:100],
                                "window": f"{format_time(start)}-{format_time(end)}",
                                "reason": f"Hallucination lub mismatch w oknie",
                            })
                    fixed_segments = new_segments
            except Exception as exc:
                print(f"  ⚠ Nie udało się retranskrybować okna {format_time(start)}-{format_time(end)}: {exc}")

    return fixed_segments


def fix_transcription(
    transcript_path: Path,
    report_path: Path,
    audio_path: Path,
    *,
    api_key: str,
    model_name: str,
) -> Dict[str, Any]:
    """Naprawia transkrypcję na podstawie raportu błędów."""
    print("🔧 Rozpoczynam automatyczną naprawę transkrypcji...")

    # Wczytaj raport
    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    issues = report.get("issues", [])
    samples = report.get("samples", [])
    summary = report.get("summary", {})

    # Wczytaj oryginalną transkrypcję
    with open(transcript_path, "r", encoding="utf-8") as f:
        original_transcript = json.load(f)

    if isinstance(original_transcript, dict) and "segments" in original_transcript:
        segments = original_transcript["segments"]
    else:
        segments = original_transcript

    # Log zmian
    fix_log = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "transcript": str(transcript_path),
        "audio": str(audio_path),
        "fixes_applied": [],
    }

    # Naprawy lokalne (timing overlaps itp.)
    segments = fix_local_issues(segments, issues, fix_log)

    # Naprawy AI (timing mismatches i hallucinations)
    if api_key and samples:
        segments = fix_ai_issues(segments, samples, audio_path, api_key, model_name, fix_log)

    # Zapisz poprawioną transkrypcję
    fixed_transcript = {"segments": segments} if isinstance(original_transcript, dict) and "segments" in original_transcript else segments
    with open(transcript_path, "w", encoding="utf-8") as f:
        json.dump(fixed_transcript, f, ensure_ascii=False, indent=2)

    # Zapisz log
    log_path = Path("metadata/auto_fix_log.json")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(fix_log, f, ensure_ascii=False, indent=2)

    print(f"✓ Naprawy zakończone. Log: {log_path}")
    return fix_log


def sanity_check_transcript(transcript_path: Path, audio_path: Path) -> bool:
    """Sprawdza poprawność techniczną transkrypcji po naprawie."""
    print("🔍 Sanity check po naprawie...")

    try:
        raw_segments = load_transcript(transcript_path)
        audio_duration = get_duration(audio_path)
        issues = []
        normalized_segments = normalize_transcript(raw_segments, audio_duration, issues)

        error_issues = [issue for issue in issues if issue.get("severity") == "error"]
        if error_issues:
            print(f"  ✗ Sanity check nie przeszedł: {len(error_issues)} błędów")
            for issue in error_issues[:3]:
                print(f"    - {issue.get('code')}: {issue.get('message')}")
            return False
        else:
            print("  ✓ Sanity check przeszedł")
            return True
    except Exception as exc:
        print(f"  ✗ Błąd podczas sanity check: {exc}")
        return False


def normalize_ai_result(result: Dict[str, Any]) -> Dict[str, Any]:
    hallucinated = result.get("hallucinated_words") or []
    missing = result.get("missing_words") or []
    if isinstance(hallucinated, str):
        hallucinated = [hallucinated]
    if isinstance(missing, str):
        missing = [missing]

    try:
        confidence = float(result.get("confidence", 0.0))
    except Exception:
        confidence = 0.0

    return {
        "matches_audio": parse_bool(result.get("matches_audio")),
        "timing_ok": parse_bool(result.get("timing_ok")),
        "confidence": max(0.0, min(1.0, confidence)),
        "heard_text": str(result.get("heard_text", "")).strip(),
        "hallucinated_words": [str(word).strip() for word in hallucinated if str(word).strip()],
        "missing_words": [str(word).strip() for word in missing if str(word).strip()],
        "notes": str(result.get("notes", "")).strip(),
    }


def normalize_logic_result(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "natural": parse_bool(result.get("natural")),
        "has_errors": parse_bool(result.get("has_errors")),
        "corrected_text": str(result.get("corrected_text", "")).strip(),
        "notes": str(result.get("notes", "")).strip(),
    }


def verify_with_ai(text: str, *, model_name: str, max_retries: int = 3) -> Dict[str, Any]:
    """Sprawdza, czy tekst brzmi naturalnie i czy nie wymaga korekty językowej."""
    model = genai.GenerativeModel(model_name)
    prompt = (
        "Czy to zdanie brzmi naturalnie i czy nie zawiera błędów gramatycznych/kontekstowych?\n\n"
        f"TEKST:\n{text}\n\n"
        "Zwróć WYŁĄCZNIE JSON jako obiekt z kluczami: "
        "\"natural\" boolean, \"has_errors\" boolean, \"corrected_text\" string, \"notes\" string. "
        "Jeśli tekst jest poprawny, corrected_text ma być identyczny z tekstem wejściowym. "
        "Poprawiaj tylko oczywiste literówki, interpunkcję, wielkość liter i błędy gramatyczne. "
        "Nie zmieniaj sensu wypowiedzi."
    )
    result = generate_content_with_backoff(
        model,
        [prompt],
        "Gemini logic check",
        max_retries=max_retries,
    )
    response_text = getattr(result, "text", None)
    if not response_text and hasattr(result, "candidates") and result.candidates:
        response_text = str(result.candidates[0])
    return normalize_logic_result(extract_json_object(response_text or ""))


def run_ai_samples(
    audio_path: Path,
    windows: List[Dict[str, Any]],
    *,
    api_key: str,
    model_name: str,
) -> List[Dict[str, Any]]:
    configure_gemini(api_key)
    results = []
    with tempfile.TemporaryDirectory(prefix="subtitler_check_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        for index, window in enumerate(windows, start=1):
            sample_path = tmpdir_path / f"sample_{index:02d}.mp3"
            print(
                f"  AI próbka {index}/{len(windows)}: "
                f"{window['start_label']} - {window['end_label']}"
            )
            try:
                extract_audio_sample(audio_path, sample_path, window["start"], window["end"])
                ai_result = ask_gemini_to_check_sample(
                    sample_path,
                    window["expected_text"],
                    model_name=model_name,
                )
                logic_result = verify_with_ai(window["expected_text"], model_name=model_name)
                window_result = dict(window)
                window_result.update(ai_result)
                window_result["logic_check"] = logic_result
                window_result["status"] = "checked"
                results.append(window_result)
            except Exception as exc:
                window_result = dict(window)
                window_result["status"] = "error"
                window_result["error"] = str(exc)
                results.append(window_result)
    return results


def issue_counts(issues: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "errors": sum(1 for issue in issues if issue.get("severity") == "error"),
        "warnings": sum(1 for issue in issues if issue.get("severity") == "warning"),
    }


def calculate_score(issues: List[Dict[str, Any]], sample_results: List[Dict[str, Any]]) -> float:
    counts = issue_counts(issues)
    score = 100.0
    score -= min(45.0, counts["errors"] * 15.0)
    score -= min(30.0, counts["warnings"] * 4.0)

    for sample in sample_results:
        if sample.get("status") == "error":
            score -= 4.0
            continue
        if sample.get("matches_audio") is False:
            score -= 18.0
        if sample.get("timing_ok") is False:
            score -= 8.0
        logic_check = sample.get("logic_check") or {}
        if logic_check.get("has_errors") is True or logic_check.get("natural") is False:
            score -= 5.0
        score -= min(12.0, len(sample.get("hallucinated_words", [])) * 4.0)

    return max(0.0, min(100.0, round(score, 1)))


def build_summary(
    *,
    issues: List[Dict[str, Any]],
    sample_results: List[Dict[str, Any]],
    normalized_segments: List[Dict[str, Any]],
    audio_duration: Optional[float],
    ai_status: str,
    fail_under: float,
    max_hallucinations: int,
) -> Dict[str, Any]:
    counts = issue_counts(issues)
    score = calculate_score(issues, sample_results)
    hallucination_hits = sum(
        1
        for sample in sample_results
        if sample.get("status") == "checked"
        and (sample.get("matches_audio") is False or sample.get("hallucinated_words"))
    )
    timing_mismatches = sum(
        1
        for sample in sample_results
        if sample.get("status") == "checked" and sample.get("timing_ok") is False
    )
    logic_errors = sum(
        1
        for sample in sample_results
        if sample.get("status") == "checked"
        and (
            (sample.get("logic_check") or {}).get("has_errors") is True
            or (sample.get("logic_check") or {}).get("natural") is False
        )
    )

    checked_samples = sum(1 for sample in sample_results if sample.get("status") == "checked")
    failed_samples = sum(1 for sample in sample_results if sample.get("status") == "error")

    status = "pass"
    if counts["errors"] > 0 or score < fail_under or hallucination_hits > max_hallucinations:
        status = "fail"
    elif counts["warnings"] > 0 or hallucination_hits > 0 or timing_mismatches > 0 or logic_errors > 0 or failed_samples > 0 or ai_status != "checked":
        status = "warning"

    return {
        "status": status,
        "score": score,
        "segments": len(normalized_segments),
        "audio_duration": audio_duration,
        "audio_duration_label": format_time(audio_duration),
        "issue_counts": counts,
        "ai_status": ai_status,
        "ai_samples_checked": checked_samples,
        "ai_samples_failed": failed_samples,
        "hallucination_hits": hallucination_hits,
        "timing_mismatches": timing_mismatches,
        "logic_errors": logic_errors,
    }


def print_summary(summary: Dict[str, Any], report_path: Path) -> None:
    status_label = {
        "pass": "PASS",
        "warning": "WARNING",
        "fail": "FAIL",
    }.get(summary["status"], summary["status"].upper())

    print()
    print("📊 Wynik AI Subtitler Checkera")
    print(f"  Status: {status_label}")
    print(f"  Score: {summary['score']}/100")
    print(f"  Segmenty: {summary['segments']}")
    print(
        "  Problemy: "
        f"{summary['issue_counts']['errors']} błędów, "
        f"{summary['issue_counts']['warnings']} ostrzeżeń"
    )
    print(
        "  AI próbki: "
        f"{summary['ai_samples_checked']} sprawdzonych, "
        f"{summary['hallucination_hits']} podejrzeń halucynacji, "
        f"{summary['timing_mismatches']} rozjazdów czasu, "
        f"{summary.get('logic_errors', 0)} problemów językowych"
    )
    print(f"  Raport: {report_path.resolve()}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Subtitler Checker dla transkrypcji i audio")
    parser.add_argument("--audio", required=True, help="Ścieżka do pliku audio")
    parser.add_argument("--transcript", required=True, help="Ścieżka do transkrypcji JSON")
    parser.add_argument(
        "--report",
        default="metadata/subtitle_check_report.json",
        help="Ścieżka raportu JSON",
    )
    parser.add_argument("--model", default="models/gemini-2.5-flash", help="Model Gemini do walidacji próbek")
    parser.add_argument("--max-samples", type=int, default=8, help="Maksymalna liczba próbek audio dla AI")
    parser.add_argument("--sample-duration", type=float, default=8.0, help="Długość jednej próbki audio w sekundach")
    parser.add_argument("--skip-ai", action="store_true", help="Uruchom tylko lokalne walidacje bez próbek Gemini")
    parser.add_argument("--require-ai", action="store_true", help="Zwróć błąd, jeśli nie da się uruchomić części AI")
    parser.add_argument("--fail-under", type=float, default=65.0, help="Minimalny score, poniżej którego checker zwraca FAIL")
    parser.add_argument(
        "--max-hallucinations",
        type=int,
        default=2,
        help="Maksymalna liczba podejrzanych próbek zanim checker zwróci FAIL",
    )
    parser.add_argument("--warn-only", action="store_true", help="Nigdy nie zwracaj kodu błędu dla statusu FAIL")
    parser.add_argument("--fix", action="store_true", help="Automatycznie napraw wykryte błędy")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    audio_path = Path(args.audio)
    transcript_path = Path(args.transcript)
    report_path = Path(args.report)

    if load_dotenv is not None:
        dotenv_path = Path(__file__).parent / ".env"
        if dotenv_path.exists():
            load_dotenv(dotenv_path)

    if not audio_path.exists():
        print(f"✗ Plik audio nie istnieje: {audio_path}", file=sys.stderr)
        sys.exit(2)
    if not transcript_path.exists():
        print(f"✗ Plik transkrypcji nie istnieje: {transcript_path}", file=sys.stderr)
        sys.exit(2)

    print("🔎 AI Subtitler Checker")
    print(f"  Audio: {audio_path}")
    print(f"  Transkrypcja: {transcript_path}")

    issues: List[Dict[str, Any]] = []
    audio_duration = get_duration(audio_path)
    if audio_duration is None:
        add_issue(issues, "warning", "AUDIO_DURATION_UNKNOWN", "Nie udało się odczytać długości audio przez ffprobe.")

    try:
        raw_segments = load_transcript(transcript_path)
    except Exception as exc:
        add_issue(issues, "error", "TRANSCRIPT_LOAD_FAILED", f"Nie udało się wczytać transkrypcji: {exc}.")
        raw_segments = []

    normalized_segments = normalize_transcript(raw_segments, audio_duration, issues)
    windows = select_sample_windows(
        normalized_segments,
        issues,
        max_samples=args.max_samples,
        sample_duration=args.sample_duration,
        audio_duration=audio_duration,
    )

    sample_results: List[Dict[str, Any]] = []
    ai_status = "skipped"
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")

    if args.skip_ai or args.max_samples <= 0:
        ai_status = "skipped"
        print("  AI próbki: pominięte (--skip-ai albo --max-samples=0)")
    elif genai is None:
        ai_status = "unavailable"
        message = "google-generativeai nie jest zainstalowane, więc pomijam próbki AI."
        add_issue(issues, "warning", "AI_UNAVAILABLE", message)
        print(f"  ⚠ {message}")
    elif not api_key:
        ai_status = "missing_api_key"
        message = "Brak GOOGLE_API_KEY/GEMINI_API_KEY/API_KEY, więc pomijam próbki AI."
        add_issue(issues, "warning", "AI_API_KEY_MISSING", message)
        print(f"  ⚠ {message}")
    elif not windows:
        ai_status = "no_samples"
        print("  AI próbki: brak okien z tekstem do sprawdzenia")
    else:
        ai_status = "checked"
        print(f"  AI próbki: {len(windows)} okien po {args.sample_duration:.1f}s")
        try:
            sample_results = run_ai_samples(
                audio_path,
                windows,
                api_key=api_key,
                model_name=args.model,
            )
        except Exception as exc:
            ai_status = "error"
            message = f"Nie udało się uruchomić próbek AI: {exc}."
            add_issue(issues, "warning", "AI_CHECK_FAILED", message)
            print(f"  ⚠ {message}")

    checked_sample_count = sum(1 for sample in sample_results if sample.get("status") == "checked")
    if args.require_ai and (ai_status != "checked" or checked_sample_count == 0):
        add_issue(issues, "error", "AI_REQUIRED_BUT_NOT_RUN", "Wymagano próbek AI, ale nie zostały wykonane.")

    summary = build_summary(
        issues=issues,
        sample_results=sample_results,
        normalized_segments=normalized_segments,
        audio_duration=audio_duration,
        ai_status=ai_status,
        fail_under=args.fail_under,
        max_hallucinations=args.max_hallucinations,
    )

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "audio": str(audio_path),
        "transcript": str(transcript_path),
        "summary": summary,
        "issues": issues,
        "samples": sample_results,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print_summary(summary, report_path)

    fix_success = False
    if args.fix and summary["status"] != "pass":
        try:
            fix_log = fix_transcription(
                transcript_path,
                report_path,
                audio_path,
                api_key=api_key or "",
                model_name=args.model,
            )
            # Sanity check po naprawie
            if sanity_check_transcript(transcript_path, audio_path):
                print("✓ Naprawy zakończone pomyślnie")
                fix_success = True
            else:
                print("⚠ Naprawy wykonane, ale sanity check nie przeszedł")
        except Exception as exc:
            print(f"✗ Błąd podczas naprawy: {exc}")

    if summary["status"] == "fail" and not args.warn_only:
        if args.fix and fix_success:
            return
        sys.exit(1)


if __name__ == "__main__":
    main()
