#!/usr/bin/env python3

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

SPEAKER_STYLE_PALETTE: List[Dict[str, str]] = [
    {"primary": "&H00FFFFFF", "outline": "&H0000FFFF"},
    {"primary": "&H00FFCC00", "outline": "&H00000000"},
    {"primary": "&H0000A5FF", "outline": "&H00000000"},
    {"primary": "&H00FFCC66", "outline": "&H00000000"},
    {"primary": "&H0088FF88", "outline": "&H00000000"},
    {"primary": "&H00CC99FF", "outline": "&H00000000"},
    {"primary": "&H0066E0FF", "outline": "&H00000000"},
    {"primary": "&H00A8FFDD", "outline": "&H00000000"},
]
DEFAULT_STYLE_NAME = "Default"
CHAOS_EMPHASIS_STYLE = "ChaosEmphasis"
DEFAULT_FONT = "DejaVu Sans"
BASE_FONT_SIZE = 68
CHAOS_FONT_SIZE = 72
OUTLINE_WIDTH = 5
SHADOW_SIZE = 2
MARGIN_H = 90
MARGIN_V = 360
EMPHASIS_COLOR = "&H0000FF00"
CHAOS_EMPHASIS_COLOR = "&H0000FF66"
KEYWORD_PATTERNS = [
    r"\d+\s*(?:zl|pln|euro|usd|dollar)",
    r"\b[A-ZÀ-ŽĄĆĘŁŃÓŚŹŻ][a-zà-žąćęłńóśźż]+\b",
    r"(?:wow|super|fantastycz|niesamowit|genialn|straszn|okropn)",
]
KEYWORD_REGEX = re.compile("|".join(KEYWORD_PATTERNS), re.IGNORECASE | re.UNICODE)
DEFAULT_SPEAKER_SMOOTHING_WINDOW = 1.25
SUBTITLE_CORRECTION_MODE_OFF = "off"
DEFAULT_SUBTITLE_CORRECTION_MODEL = "models/gemini-2.5-flash"
MIN_WORDS_PER_CUE = 3
TARGET_WORDS_PER_CUE = 5
MAX_WORDS_PER_CUE = 7
NATURAL_PAUSE_SECONDS = 0.55
MAX_LINE_CHARACTERS = 24
MAX_MERGED_CUE_CHARACTERS = (MAX_LINE_CHARACTERS * 2) + 1
SHORT_CONNECTORS = {
    "a",
    "ale",
    "and",
    "bo",
    "but",
    "czy",
    "do",
    "i",
    "lub",
    "na",
    "o",
    "od",
    "or",
    "u",
    "w",
    "z",
    "za",
    "że",
}
STRONG_PUNCTUATION_RE = re.compile(r"[.!?…][\"'”’)]*$")
SOFT_PUNCTUATION_RE = re.compile(r"[,;:][\"'”’)]*$")
WORD_CHARACTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def parse_time(time_str: str) -> float:
    if isinstance(time_str, (int, float)):
        return float(time_str)
    time_str = str(time_str).strip().replace(",", ".")
    pattern = r"^(?:(\d+):)?(\d{1,2}):(\d{2}(?:\.\d+)?)$"
    match = re.match(pattern, time_str)
    if not match:
        raise ValueError(f"Invalid time format: {time_str}")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def load_transcript(path: Path) -> List[Dict]:
    with open(path, "r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)
    if isinstance(data, dict) and "segments" in data:
        return data["segments"]
    return data


def normalize_speaker(segment: Dict) -> str:
    raw = (
        segment.get("speaker")
        or segment.get("speaker_id")
        or segment.get("speakerId")
        or DEFAULT_STYLE_NAME
    )
    normalized = " ".join(str(raw).strip().split())
    if normalized.lower().startswith("speaker "):
        suffix = normalized.split()[-1]
        if suffix.isdigit():
            normalized = f"Speaker {int(suffix)}"
        else:
            suffix = suffix.upper()
            if len(suffix) == 1 and "A" <= suffix <= "Z":
                normalized = f"Speaker {ord(suffix) - ord('A')}"
    return normalized if normalized.lower().startswith("speaker ") else DEFAULT_STYLE_NAME


def speaker_style(name: str) -> Dict[str, str]:
    normalized = " ".join(str(name or "").strip().split())
    if normalized == DEFAULT_STYLE_NAME:
        return SPEAKER_STYLE_PALETTE[0]
    match = re.search(r"(\d+)", normalized)
    if not match:
        return SPEAKER_STYLE_PALETTE[0]
    speaker_index = int(match.group(1))
    return SPEAKER_STYLE_PALETTE[speaker_index % len(SPEAKER_STYLE_PALETTE)]


def speaker_segment_duration(segment: Dict) -> float:
    try:
        return max(0.0, parse_time(segment.get("end", "00:00")) - parse_time(segment.get("start", "00:00")))
    except Exception:
        return 0.0


def _normalized_speaker_sequence(transcript: List[Dict]) -> List[str]:
    return [normalize_speaker(item) for item in transcript]


def smooth_speaker_labels(
    transcript: List[Dict],
    *,
    max_flip_duration: float = DEFAULT_SPEAKER_SMOOTHING_WINDOW,
    return_metadata: bool = False,
) -> List[Dict] | Tuple[List[Dict], Dict[str, object]]:
    if max_flip_duration <= 0 or len(transcript) < 3:
        passthrough = [dict(item) for item in transcript]
        if return_metadata:
            labels = _normalized_speaker_sequence(passthrough)
            metadata = {
                "speaker_flips_smoothed": 0,
                "detected_speaker_count": len({label for label in labels if label != DEFAULT_STYLE_NAME}),
                "effective_speaker_count": len({label for label in labels if label != DEFAULT_STYLE_NAME}),
                "speaker_smoothing_enabled": max_flip_duration > 0,
                "speaker_smoothing_window": float(max_flip_duration),
            }
            return passthrough, metadata
        return passthrough

    smoothed = [dict(item) for item in transcript]
    normalized_before = _normalized_speaker_sequence(smoothed)
    normalized = list(normalized_before)
    flips_smoothed = 0
    for index in range(1, len(smoothed) - 1):
        previous_speaker = normalized[index - 1]
        current_speaker = normalized[index]
        next_speaker = normalized[index + 1]
        if previous_speaker != next_speaker or current_speaker == previous_speaker:
            continue
        if speaker_segment_duration(smoothed[index]) > max_flip_duration:
            continue
        smoothed[index]["speaker"] = previous_speaker
        normalized[index] = previous_speaker
        flips_smoothed += 1
    if not return_metadata:
        return smoothed
    detected_speakers = {label for label in normalized_before if label != DEFAULT_STYLE_NAME}
    effective_speakers = {label for label in normalized if label != DEFAULT_STYLE_NAME}
    metadata = {
        "speaker_flips_smoothed": flips_smoothed,
        "detected_speaker_count": len(detected_speakers),
        "effective_speaker_count": len(effective_speakers),
        "speaker_smoothing_enabled": max_flip_duration > 0,
        "speaker_smoothing_window": float(max_flip_duration),
    }
    return smoothed, metadata


def collect_speaker_styles(events: List[Dict]) -> List[str]:
    speaker_names = {DEFAULT_STYLE_NAME}
    for event in events:
        speaker = normalize_speaker({"speaker": event.get("speaker")})
        if speaker != DEFAULT_STYLE_NAME:
            speaker_names.add(speaker)
    return sorted(
        speaker_names,
        key=lambda name: (-1 if name == DEFAULT_STYLE_NAME else int(re.search(r"(\d+)", name).group(1))),
    )


def speaker_color_map(speaker_names: List[str]) -> Dict[str, Dict[str, str]]:
    mapping: Dict[str, Dict[str, str]] = {}
    for speaker_name in speaker_names:
        style = speaker_style(speaker_name)
        mapping[speaker_name] = {
            "primary": style["primary"],
            "outline": style["outline"],
        }
    return mapping


def resolve_effective_speaker_cap(
    *,
    content_type_hint: str = "",
    expected_speaker_mode: str = "unknown",
    max_effective_speakers: int | None = None,
) -> int | None:
    if isinstance(max_effective_speakers, int) and max_effective_speakers > 0:
        return max_effective_speakers
    normalized_mode = normalize_expected_speaker_mode(expected_speaker_mode)
    if normalized_mode == "single":
        return 2
    return None


def normalize_expected_speaker_mode(value: str | None) -> str:
    normalized = str(value or "unknown").strip().lower()
    aliases = {
        "single_speaker": "single",
        "single-speaker": "single",
        "single speaker": "single",
        "multi_speaker": "multi",
        "multi-speaker": "multi",
        "multiple": "multi",
        "multiple_speakers": "multi",
    }
    return aliases.get(normalized, normalized if normalized in {"single", "multi"} else "unknown")


def resolve_speaker_color_policy(
    events: List[Dict],
    *,
    expected_speaker_mode: str = "unknown",
) -> Dict[str, object]:
    normalized_events = [dict(event) for event in events]
    speakers = [
        normalize_speaker({"speaker": event.get("speaker")})
        for event in normalized_events
        if normalize_speaker({"speaker": event.get("speaker")}) != DEFAULT_STYLE_NAME
    ]
    speaker_sequence = [speaker for speaker in speakers if speaker != DEFAULT_STYLE_NAME]
    speaker_switch_count = sum(
        1 for left, right in zip(speaker_sequence, speaker_sequence[1:]) if left != right
    )
    speaker_switch_ratio = speaker_switch_count / max(len(speaker_sequence) - 1, 1) if speaker_sequence else 0.0
    durations: Dict[str, float] = {}
    short_segments_by_speaker: Dict[str, int] = {}
    for event in normalized_events:
        speaker = normalize_speaker({"speaker": event.get("speaker")})
        if speaker == DEFAULT_STYLE_NAME:
            continue
        duration = max(0.0, float(event.get("end", 0.0)) - float(event.get("start", 0.0)))
        durations[speaker] = durations.get(speaker, 0.0) + duration
        if duration <= 1.0:
            short_segments_by_speaker[speaker] = short_segments_by_speaker.get(speaker, 0) + 1

    active_speakers = sorted(durations, key=lambda speaker: durations[speaker], reverse=True)
    fallback_reasons: list[str] = []
    expected_mode = normalize_expected_speaker_mode(expected_speaker_mode)
    if expected_mode == "single":
        fallback_reasons.append("expected_single_speaker")
    if len(active_speakers) < 2:
        fallback_reasons.append("not_enough_speakers_for_color")
    if len(active_speakers) > 2:
        fallback_reasons.append("too_many_detected_speakers")
    if short_segments_by_speaker and sum(short_segments_by_speaker.values()) >= max(2, len(normalized_events) // 4):
        fallback_reasons.append("many_short_speaker_segments")
    if speaker_switch_ratio >= 0.7 and sum(short_segments_by_speaker.values()) > 0:
        fallback_reasons.append("unstable_speaker_switches")
    if durations and min(durations.values()) < 2.0 and len(active_speakers) > 1:
        fallback_reasons.append("speaker_duration_too_short")

    mode = "single_style_fallback" if fallback_reasons else "stable_per_speaker"
    speaker_names = active_speakers[:2] if mode == "stable_per_speaker" else [DEFAULT_STYLE_NAME]
    return {
        "speaker_color_mode": mode,
        "speaker_color_fallback_reason": ",".join(fallback_reasons),
        "speaker_switch_count": speaker_switch_count,
        "speaker_switch_ratio": round(float(speaker_switch_ratio), 4),
        "speaker_styles_used": speaker_names,
    }


def _nearest_kept_speaker(index: int, events: List[Dict], keep_speakers: set[str], dominant_speaker: str) -> str:
    for offset in range(1, len(events)):
        left = index - offset
        if left >= 0 and events[left].get("speaker") in keep_speakers:
            return str(events[left].get("speaker") or dominant_speaker)
        right = index + offset
        if right < len(events) and events[right].get("speaker") in keep_speakers:
            return str(events[right].get("speaker") or dominant_speaker)
    return dominant_speaker


def stabilize_speaker_events(
    events: List[Dict],
    *,
    content_type_hint: str = "",
    expected_speaker_mode: str = "unknown",
    max_effective_speakers: int | None = None,
) -> Tuple[List[Dict], Dict[str, object]]:
    if not events:
        return [], {
            "merged_low_duration_speakers": [],
            "speaker_stability_reason": "no_events",
            "detected_speaker_count": 0,
            "effective_speaker_count": 0,
        }
    cap = resolve_effective_speaker_cap(
        content_type_hint=content_type_hint,
        expected_speaker_mode=expected_speaker_mode,
        max_effective_speakers=max_effective_speakers,
    )
    normalized = [dict(event) for event in events]
    durations: Dict[str, float] = {}
    for event in normalized:
        speaker = normalize_speaker({"speaker": event.get("speaker")})
        event["speaker"] = speaker
        if speaker == DEFAULT_STYLE_NAME:
            continue
        durations[speaker] = durations.get(speaker, 0.0) + max(0.0, float(event.get("end", 0.0)) - float(event.get("start", 0.0)))
    detected_speakers = [speaker for speaker in durations.keys() if speaker != DEFAULT_STYLE_NAME]
    if cap is None or len(detected_speakers) <= cap:
        return normalized, {
            "merged_low_duration_speakers": [],
            "speaker_stability_reason": "within_effective_cap" if cap else "no_effective_cap",
            "detected_speaker_count": len(detected_speakers),
            "effective_speaker_count": len(detected_speakers),
        }

    sorted_speakers = sorted(durations.items(), key=lambda item: item[1], reverse=True)
    dominant_speaker = sorted_speakers[0][0]
    keep_speakers = {speaker for speaker, _duration in sorted_speakers[:cap]}
    total_duration = sum(durations.values()) or 1.0
    merged_speakers: list[str] = []
    for index, event in enumerate(normalized):
        speaker = str(event.get("speaker") or DEFAULT_STYLE_NAME)
        if speaker == DEFAULT_STYLE_NAME or speaker in keep_speakers:
            continue
        speaker_duration = durations.get(speaker, 0.0)
        speaker_share = speaker_duration / total_duration
        if len(detected_speakers) >= 6 or speaker_duration <= 1.5 or speaker_share <= 0.12:
            replacement = _nearest_kept_speaker(index, normalized, keep_speakers, dominant_speaker)
            event["speaker"] = replacement
            if speaker not in merged_speakers:
                merged_speakers.append(speaker)

    effective_speakers = sorted(
        {str(event.get("speaker") or DEFAULT_STYLE_NAME) for event in normalized if str(event.get("speaker") or DEFAULT_STYLE_NAME) != DEFAULT_STYLE_NAME}
    )
    return normalized, {
        "merged_low_duration_speakers": merged_speakers,
        "speaker_stability_reason": "merged_low_duration_speakers" if merged_speakers else "no_merge_needed",
        "detected_speaker_count": len(detected_speakers),
        "effective_speaker_count": len(effective_speakers),
    }


def ass_color(color_value: str) -> str:
    return f"\\c{color_value}&" if color_value.endswith("&") else f"\\c{color_value}&"


def apply_emphasis(text: str, speaker_name: str) -> str:
    color_tag = f"\\c{EMPHASIS_COLOR}&"
    reset_style = speaker_name if speaker_name != DEFAULT_STYLE_NAME else DEFAULT_STYLE_NAME

    def repl(match):
        word = match.group(0)
        return f"{{\\b1{color_tag}}}{word}{{\\r{reset_style}}}"

    return KEYWORD_REGEX.sub(repl, text)


def normalize_subtitle_text(text: str, *, capitalize: bool = False) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return ""
    normalized = re.sub(r"(?<=\d)\s+-\s+(?=\d)", "\N{EN DASH}", normalized)
    normalized = re.sub(r"\s+([,.;:!?%…])", r"\1", normalized)
    normalized = re.sub(r"([(\[{„“])\s+", r"\1", normalized)
    normalized = re.sub(r"\s+([)\]}”])", r"\1", normalized)
    normalized = re.sub(r"([,;:!?])(?=[^\W\d_])", r"\1 ", normalized, flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if capitalize:
        first_letter = WORD_CHARACTER_RE.search(normalized)
        if first_letter is not None:
            index = first_letter.start()
            normalized = normalized[:index] + normalized[index].upper() + normalized[index + 1 :]
    return normalized


def _plain_token(value: object) -> str:
    return normalize_subtitle_text(str(value or ""), capitalize=False)


def _is_short_connector(value: str) -> bool:
    normalized = value.strip(".,;:!?…\"'„“”’()[]{}").casefold()
    return normalized in SHORT_CONNECTORS


def wrap_subtitle_text(text: str) -> str:
    normalized = normalize_subtitle_text(text)
    words = normalized.split()
    if len(words) <= 1 or len(normalized) <= MAX_LINE_CHARACTERS:
        return normalized

    candidates: list[tuple[int, int, str, str]] = []
    for split_at in range(1, len(words)):
        left_words = words[:split_at]
        right_words = words[split_at:]
        left = " ".join(left_words)
        right = " ".join(right_words)
        if (
            (len(left_words) == 1 and _is_short_connector(left_words[0]))
            or (len(right_words) == 1 and _is_short_connector(right_words[0]))
        ):
            continue
        connector_penalty = 12 if _is_short_connector(left_words[-1]) else 0
        overflow = max(0, len(left) - MAX_LINE_CHARACTERS) + max(0, len(right) - MAX_LINE_CHARACTERS)
        balance = abs(len(left) - len(right))
        candidates.append((overflow * 20 + connector_penalty + balance, split_at, left, right))

    if not candidates:
        return normalized

    _score, _split_at, left, right = min(candidates, key=lambda item: (item[0], item[1]))
    return f"{left}\n{right}"


def _subtitle_text_fits_merge_limits(text: str) -> bool:
    normalized = normalize_subtitle_text(text)
    if not normalized or len(normalized.split()) > MAX_WORDS_PER_CUE:
        return False
    if len(normalized) > MAX_MERGED_CUE_CHARACTERS:
        return False
    lines = wrap_subtitle_text(normalized).splitlines()
    return len(lines) <= 2 and all(len(line) <= MAX_LINE_CHARACTERS for line in lines)


def escape_ass_text(text: str) -> str:
    escaped = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    escaped = escaped.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")
    return escaped.replace("\n", r"\N")


def _timed_words_from_word_timestamps(segment: Dict) -> list[dict[str, object]]:
    raw_words = segment.get("words")
    if not isinstance(raw_words, list):
        return []

    timed_words: list[dict[str, object]] = []
    for item in raw_words:
        if not isinstance(item, dict):
            continue
        text = _plain_token(item.get("text") or item.get("word"))
        if not text:
            continue
        try:
            start = parse_time(item.get("start"))
            end = parse_time(item.get("end"))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        if re.fullmatch(r"[,.;:!?%…]+", text) and timed_words:
            timed_words[-1]["text"] = f"{timed_words[-1]['text']}{text}"
            timed_words[-1]["end"] = max(float(timed_words[-1]["end"]), end)
            continue
        timed_words.append({"text": text, "start": start, "end": end})
    return timed_words


def _timed_words_from_segment_text(segment: Dict, start: float, end: float) -> list[dict[str, object]]:
    text = normalize_subtitle_text(str(segment.get("text", "")))
    words = text.split()
    if not words or end <= start:
        return []
    word_duration = (end - start) / len(words)
    return [
        {
            "text": word,
            "start": start + (index * word_duration),
            "end": start + ((index + 1) * word_duration),
        }
        for index, word in enumerate(words)
    ]


def _rebalance_short_cues(cues: list[list[dict[str, object]]]) -> list[list[dict[str, object]]]:
    index = 0
    while index < len(cues):
        cue = cues[index]
        if len(cue) == 1 and _is_short_connector(str(cue[0]["text"])):
            if index + 1 < len(cues) and _timed_word_cues_can_merge(cue, cues[index + 1]):
                cues[index + 1] = cue + cues[index + 1]
                cues.pop(index)
                continue
            if index > 0 and _timed_word_cues_can_merge(cues[index - 1], cue):
                cues[index - 1].extend(cue)
                cues.pop(index)
                continue
        index += 1

    for index in range(1, len(cues)):
        current = cues[index]
        previous = cues[index - 1]
        if len(current) >= MIN_WORDS_PER_CUE or len(previous) <= MIN_WORDS_PER_CUE:
            continue
        gap = float(current[0]["start"]) - float(previous[-1]["end"])
        if gap >= NATURAL_PAUSE_SECONDS or STRONG_PUNCTUATION_RE.search(str(previous[-1]["text"])):
            continue
        while len(current) < MIN_WORDS_PER_CUE and len(previous) > MIN_WORDS_PER_CUE:
            current.insert(0, previous.pop())
    return [cue for cue in cues if cue]


def _timed_word_cues_can_merge(
    left: list[dict[str, object]],
    right: list[dict[str, object]],
) -> bool:
    if not left or not right:
        return False
    gap = float(right[0]["start"]) - float(left[-1]["end"])
    if gap < 0 or gap >= NATURAL_PAUSE_SECONDS:
        return False
    if STRONG_PUNCTUATION_RE.search(str(left[-1]["text"])):
        return False
    combined_text = " ".join(str(word["text"]) for word in left + right)
    return _subtitle_text_fits_merge_limits(combined_text)


def _chunk_timed_words(words: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    cues: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    for index, word in enumerate(words):
        if current:
            gap = float(word["start"]) - float(current[-1]["end"])
            if gap >= NATURAL_PAUSE_SECONDS:
                cues.append(current)
                current = []

        current.append(word)
        token = str(word["text"])
        next_word = words[index + 1] if index + 1 < len(words) else None
        next_gap = (
            float(next_word["start"]) - float(word["end"])
            if next_word is not None
            else 0.0
        )
        should_break = len(current) >= MAX_WORDS_PER_CUE
        if len(current) >= MIN_WORDS_PER_CUE and STRONG_PUNCTUATION_RE.search(token):
            should_break = True
        elif len(current) >= TARGET_WORDS_PER_CUE and SOFT_PUNCTUATION_RE.search(token):
            should_break = True
        elif len(current) >= MIN_WORDS_PER_CUE and next_gap >= NATURAL_PAUSE_SECONDS:
            should_break = True
        if should_break:
            cues.append(current)
            current = []

    if current:
        cues.append(current)
    return _rebalance_short_cues(cues)


def _clip_timed_words(
    words: list[dict[str, object]],
    clip_start: float,
    clip_end: float,
) -> list[dict[str, object]]:
    clipped: list[dict[str, object]] = []
    for word in words:
        start = max(float(word["start"]), clip_start)
        end = min(float(word["end"]), clip_end)
        if end <= start:
            continue
        clipped.append({**word, "start": start, "end": end})
    return clipped


def build_segment_subtitle_events(
    segment: Dict,
    clip_start: float,
    clip_end: float,
) -> tuple[list[dict[str, object]], bool]:
    try:
        segment_start = parse_time(segment.get("start", "00:00"))
        segment_end = parse_time(segment.get("end", "00:00"))
    except (TypeError, ValueError):
        return [], False
    if segment_end <= clip_start or segment_start >= clip_end or segment_end <= segment_start:
        return [], False

    timestamp_words = _timed_words_from_word_timestamps(segment)
    used_word_timestamps = bool(timestamp_words)
    timed_words = timestamp_words or _timed_words_from_segment_text(segment, segment_start, segment_end)
    clipped_words = _clip_timed_words(timed_words, clip_start, clip_end)
    if not clipped_words:
        return [], used_word_timestamps

    events: list[dict[str, object]] = []
    capitalize_next = False
    for cue_words in _chunk_timed_words(clipped_words):
        text = normalize_subtitle_text(
            " ".join(str(word["text"]) for word in cue_words),
            capitalize=capitalize_next,
        )
        if not text:
            continue
        events.append(
            {
                "start": float(cue_words[0]["start"]),
                "end": float(cue_words[-1]["end"]),
                "text": wrap_subtitle_text(text),
            }
        )
        capitalize_next = bool(STRONG_PUNCTUATION_RE.search(text))
    return events, used_word_timestamps


def ensure_non_overlapping_events(events: List[Dict], clip_duration: float) -> List[Dict]:
    ordered = sorted(events, key=lambda event: (float(event["start"]), float(event["end"])))
    normalized: List[Dict] = []
    for event in ordered:
        start = max(0.0, min(float(event["start"]), clip_duration))
        end = max(0.0, min(float(event["end"]), clip_duration))
        if normalized and start < float(normalized[-1]["end"]):
            normalized[-1]["end"] = max(float(normalized[-1]["start"]), start)
            if float(normalized[-1]["end"]) <= float(normalized[-1]["start"]):
                normalized.pop()
        if end <= start:
            continue
        normalized.append({**event, "start": start, "end": end})
    return normalized


def merge_orphaned_subtitle_events(events: List[Dict]) -> List[Dict]:
    merged_events = [dict(event) for event in events]
    index = 0
    while index < len(merged_events) - 1:
        left = merged_events[index]
        right = merged_events[index + 1]
        left_text = normalize_subtitle_text(str(left.get("text", "")).replace("\n", " "))
        right_text = normalize_subtitle_text(str(right.get("text", "")).replace("\n", " "))
        left_words = left_text.split()
        right_words = right_text.split()
        gap = float(right["start"]) - float(left["end"])
        same_speaker = normalize_speaker(left) == normalize_speaker(right)
        left_is_complete = bool(STRONG_PUNCTUATION_RE.search(left_text))
        right_is_complete = bool(STRONG_PUNCTUATION_RE.search(right_text))
        left_is_orphan = 0 < len(left_words) <= 2 and not left_is_complete
        right_is_orphan = 0 < len(right_words) <= 2 and not right_is_complete
        left_ends_with_comma = bool(re.search(r",[\s\"')\]}]*$", left_text))
        combined_text = normalize_subtitle_text(f"{left_text} {right_text}")

        can_merge = (
            same_speaker
            and 0 <= gap < NATURAL_PAUSE_SECONDS
            and not left_is_complete
            and (left_is_orphan or right_is_orphan or left_ends_with_comma)
            and _subtitle_text_fits_merge_limits(combined_text)
        )
        if not can_merge:
            index += 1
            continue

        merged_events[index : index + 2] = [
            {
                **left,
                "end": float(right["end"]),
                "text": wrap_subtitle_text(combined_text),
                "importance": max(int(left.get("importance", 3)), int(right.get("importance", 3))),
                "chaos": bool(left.get("chaos", False) or right.get("chaos", False)),
            }
        ]
        index = max(0, index - 1)

    return merged_events


def calculate_words_per_second(text: str, duration: float) -> float:
    if duration <= 0 or not text.strip():
        return 0.0
    return len(text.split()) / duration


def should_display_subtitle(segment: Dict, duration: float) -> bool:
    text = normalize_subtitle_text(str(segment.get("text", "")))
    return duration > 0 and bool(text)


def build_subtitle_events(
    transcript: List[Dict],
    segment_start: float,
    segment_duration: float,
    *,
    speaker_smoothing_window: float = DEFAULT_SPEAKER_SMOOTHING_WINDOW,
    content_type_hint: str = "",
    expected_speaker_mode: str = "unknown",
    max_effective_speakers: int | None = None,
    subtitle_correction_mode: str = SUBTITLE_CORRECTION_MODE_OFF,
    semantic_model: str = DEFAULT_SUBTITLE_CORRECTION_MODEL,
    api_key: str | None = None,
    request_timeout: float = 45.0,
) -> List[Dict]:
    events, _metadata = build_subtitle_events_with_metadata(
        transcript,
        segment_start,
        segment_duration,
        speaker_smoothing_window=speaker_smoothing_window,
        content_type_hint=content_type_hint,
        expected_speaker_mode=expected_speaker_mode,
        max_effective_speakers=max_effective_speakers,
        subtitle_correction_mode=subtitle_correction_mode,
        semantic_model=semantic_model,
        api_key=api_key,
        request_timeout=request_timeout,
    )
    return events


def build_subtitle_events_with_metadata(
    transcript: List[Dict],
    segment_start: float,
    segment_duration: float,
    *,
    speaker_smoothing_window: float = DEFAULT_SPEAKER_SMOOTHING_WINDOW,
    content_type_hint: str = "",
    expected_speaker_mode: str = "unknown",
    max_effective_speakers: int | None = None,
    subtitle_correction_mode: str = SUBTITLE_CORRECTION_MODE_OFF,
    semantic_model: str = DEFAULT_SUBTITLE_CORRECTION_MODEL,
    api_key: str | None = None,
    request_timeout: float = 45.0,
) -> Tuple[List[Dict], Dict[str, object]]:
    # Retain legacy call compatibility, but never send transcript text to an external corrector.
    _ = semantic_model, api_key, request_timeout
    normalized_content_type = str(content_type_hint or "").strip().lower()
    raw_events: List[Dict] = []
    segment_end = segment_start + segment_duration
    word_timestamp_segments = 0
    segment_timestamp_fallbacks = 0
    transcript_for_events, smoothing_metadata = smooth_speaker_labels(
        transcript,
        max_flip_duration=speaker_smoothing_window,
        return_metadata=True,
    )

    for item in transcript_for_events:
        importance = int(item.get("importance", 3))
        chaos = bool(item.get("chaos", False))
        speaker = normalize_speaker(item)
        segment_events, used_word_timestamps = build_segment_subtitle_events(
            item,
            segment_start,
            segment_end,
        )
        if not segment_events:
            continue
        if used_word_timestamps:
            word_timestamp_segments += 1
        else:
            segment_timestamp_fallbacks += 1
        for event in segment_events:
            rel_start = float(event["start"]) - segment_start
            rel_end = float(event["end"]) - segment_start
            if not should_display_subtitle({"text": event["text"]}, rel_end - rel_start):
                continue
            raw_events.append(
                {
                    **event,
                    "start": rel_start,
                    "end": rel_end,
                    "speaker": speaker,
                    "importance": importance,
                    "chaos": chaos,
                }
            )

    raw_events = ensure_non_overlapping_events(raw_events, segment_duration)
    raw_events = merge_orphaned_subtitle_events(raw_events)
    raw_events = ensure_non_overlapping_events(raw_events, segment_duration)
    capitalize_next = True
    for event in raw_events:
        text = normalize_subtitle_text(
            str(event.get("text", "")).replace("\n", " "),
            capitalize=capitalize_next,
        )
        event["text"] = wrap_subtitle_text(text)
        capitalize_next = bool(STRONG_PUNCTUATION_RE.search(text))

    stabilized_events, speaker_stability_metadata = stabilize_speaker_events(
        raw_events,
        content_type_hint=content_type_hint,
        expected_speaker_mode=expected_speaker_mode,
        max_effective_speakers=max_effective_speakers,
    )
    requested_correction_mode = str(subtitle_correction_mode or SUBTITLE_CORRECTION_MODE_OFF).strip().lower()
    corrected_events = [dict(event) for event in stabilized_events]
    correction_metadata = {
        "subtitles_corrected": False,
        "subtitle_corrector_used": "off",
        "corrected_segments_count": 0,
        "correction_fallback_reason": (
            ""
            if requested_correction_mode == SUBTITLE_CORRECTION_MODE_OFF
            else "external_subtitle_correction_disabled"
        ),
        "subtitle_correction_requested": requested_correction_mode,
    }

    color_policy = resolve_speaker_color_policy(
        corrected_events,
        expected_speaker_mode=expected_speaker_mode,
    )
    if normalized_content_type == "podcast":
        color_policy = {
            **color_policy,
            "speaker_color_mode": "single_style_fallback",
            "speaker_color_fallback_reason": "podcast_mvp_single_color",
            "speaker_styles_used": [DEFAULT_STYLE_NAME],
            "subtitle_color_policy": "single_color_podcast_mvp",
        }
    else:
        color_policy = {
            **color_policy,
            "subtitle_color_policy": "dynamic_color_policy",
        }
    stable_speakers = set(color_policy.get("speaker_styles_used") or [])
    use_per_speaker = normalized_content_type != "podcast" and color_policy.get("speaker_color_mode") == "stable_per_speaker"

    events: List[Dict] = []
    for event in corrected_events:
        detected_speaker = str(event.get("speaker") or DEFAULT_STYLE_NAME)
        normalized_text = wrap_subtitle_text(normalize_subtitle_text(str(event.get("text", ""))))
        display_speaker = (
            normalize_speaker({"speaker": detected_speaker})
            if use_per_speaker and normalize_speaker({"speaker": detected_speaker}) in stable_speakers
            else DEFAULT_STYLE_NAME
        )
        if normalized_content_type == "podcast":
            display_text = normalized_text
            render_style = DEFAULT_STYLE_NAME
            ass_markup = False
        else:
            display_text = normalized_text if int(event.get("importance", 3)) >= 5 else (
                apply_emphasis(normalized_text, display_speaker)
                if int(event.get("importance", 3)) >= 4
                else normalized_text
            )
            render_style = display_speaker
            ass_markup = display_text != normalized_text
        events.append({
            **event,
            "speaker": display_speaker,
            "detected_speaker": detected_speaker,
            "render_style": render_style,
            "text": display_text,
            "ass_markup": ass_markup,
        })

    speaker_names = collect_speaker_styles(events)
    metadata: Dict[str, object] = {
        **smoothing_metadata,
        **speaker_stability_metadata,
        **correction_metadata,
        **color_policy,
        "speaker_color_map": speaker_color_map(speaker_names),
        "speaker_smoothing_enabled": bool(smoothing_metadata.get("speaker_smoothing_enabled", False)),
        "speaker_smoothing_window": float(smoothing_metadata.get("speaker_smoothing_window", speaker_smoothing_window)),
        "word_timestamp_segments": word_timestamp_segments,
        "segment_timestamp_fallbacks": segment_timestamp_fallbacks,
        "subtitle_cue_count": len(events),
    }
    return events, metadata


def format_ass_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centisecs = int(round((seconds - int(seconds)) * 100))
    if centisecs == 100:
        secs += 1
        centisecs = 0
    return f"{hours}:{minutes:02d}:{secs:02d}.{centisecs:02d}"


def create_style_line(
    name: str,
    color: str,
    font_size: int,
    *,
    outline_color: str = "&H00000000",
    bold: int = -1,
    alignment: int = 2,
) -> str:
    return (
        f"Style: {name},{DEFAULT_FONT},{font_size},{color},&H00000000,{outline_color},&H00000000,"
        f"{bold},0,0,0,100,100,0,0,1,{OUTLINE_WIDTH},{SHADOW_SIZE},{alignment},"
        f"{MARGIN_H},{MARGIN_H},{MARGIN_V},1"
    )


def create_ass_file(events: List[Dict]) -> str:
    lines: List[str] = [
        "[Script Info]",
        "Title: Podcast Shorts Cutter Subtitles",
        "ScriptType: v4.00+",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "Collisions: Normal",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        create_style_line(DEFAULT_STYLE_NAME, speaker_style("Speaker 0")["primary"], BASE_FONT_SIZE),
    ]

    for speaker_name in collect_speaker_styles(events):
        if speaker_name == DEFAULT_STYLE_NAME:
            continue
        style = speaker_style(speaker_name)
        lines.append(
            create_style_line(
                speaker_name,
                style["primary"],
                BASE_FONT_SIZE,
                outline_color=style["outline"],
            )
        )

    lines.append(create_style_line(CHAOS_EMPHASIS_STYLE, CHAOS_EMPHASIS_COLOR, CHAOS_FONT_SIZE, bold=1, alignment=5))
    lines.extend(["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"])

    for event in events:
        start_time = format_ass_time(event["start"])
        end_time = format_ass_time(event["end"])
        style = str(event.get("render_style") or event.get("speaker") or DEFAULT_STYLE_NAME)
        if style != DEFAULT_STYLE_NAME and event.get("importance", 3) >= 5:
            style = CHAOS_EMPHASIS_STYLE
        text = (
            str(event["text"]).replace("\n", r"\N")
            if event.get("ass_markup")
            else escape_ass_text(str(event["text"]))
        )
        lines.append(f"Dialogue: 0,{start_time},{end_time},{style},,0,0,0,,{text}")

    return "\n".join(lines)


def extract_segment_time_from_filename(filename: str) -> Tuple[float, float]:
    name = Path(filename).stem
    pattern = r"segment_\d+_(\d+)-(\d{2}_\d+)_(\d+)-(\d{2}_\d+)"
    match = re.search(pattern, name)
    if not match:
        raise ValueError(f"Could not parse timestamps from filename: {filename}")
    start_minutes = int(match.group(1))
    start_secs = float(match.group(2).replace("_", "."))
    end_minutes = int(match.group(3))
    end_secs = float(match.group(4).replace("_", "."))
    return start_minutes * 60 + start_secs, end_minutes * 60 + end_secs


def _escape_ffmpeg_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", r"\'")


def subtitle_fonts_dir() -> Path | None:
    python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    candidates = (
        Path(sys.prefix) / "Lib" / "site-packages" / "matplotlib" / "mpl-data" / "fonts" / "ttf",
        Path(sys.prefix) / "lib" / python_version / "site-packages" / "matplotlib" / "mpl-data" / "fonts" / "ttf",
    )
    for candidate in candidates:
        if (candidate / "DejaVuSans.ttf").is_file():
            return candidate
    return None


def add_subtitles_to_video(input_video: Path, output_video: Path, ass_file: Path) -> None:
    output_video.parent.mkdir(parents=True, exist_ok=True)
    escaped_path = _escape_ffmpeg_filter_path(ass_file)
    filter_str = f"ass='{escaped_path}'"
    fonts_dir = subtitle_fonts_dir()
    if fonts_dir is not None:
        filter_str += f":fontsdir='{_escape_ffmpeg_filter_path(fonts_dir)}'"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_video),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        filter_str,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_video),
    ]
    print(f"  Adding subtitles: {output_video.name}")
    subprocess.run(cmd, check=True)


def process_cut_file(
    cut_file: Path,
    transcript: List[Dict],
    output_raw: Path,
    output_subs: Path,
    *,
    content_type_hint: str = "",
    expected_speaker_mode: str = "unknown",
    max_effective_speakers: int | None = None,
    subtitle_correction_mode: str = SUBTITLE_CORRECTION_MODE_OFF,
    semantic_model: str = DEFAULT_SUBTITLE_CORRECTION_MODEL,
    api_key: str | None = None,
    request_timeout: float = 45.0,
) -> None:
    output_raw.mkdir(parents=True, exist_ok=True)
    output_subs.mkdir(parents=True, exist_ok=True)

    segment_start, segment_end = extract_segment_time_from_filename(cut_file.name)
    segment_duration = segment_end - segment_start
    events, subtitle_debug = build_subtitle_events_with_metadata(
        transcript,
        segment_start,
        segment_duration,
        content_type_hint=content_type_hint,
        expected_speaker_mode=expected_speaker_mode,
        max_effective_speakers=max_effective_speakers,
        subtitle_correction_mode=subtitle_correction_mode,
        semantic_model=semantic_model,
        api_key=api_key,
        request_timeout=request_timeout,
    )
    if not events:
        print(f"  Warning: no subtitle events for {cut_file.name}")
    if subtitle_debug.get("speaker_flips_smoothed"):
        print(
            f"  Speaker smoothing merged {subtitle_debug['speaker_flips_smoothed']} short flip(s) "
            f"for {cut_file.name}"
        )
    if subtitle_debug.get("merged_low_duration_speakers"):
        merged = ", ".join(subtitle_debug["merged_low_duration_speakers"])
        print(f"  Speaker sanity merged unstable labels ({merged}) for {cut_file.name}")
    if subtitle_debug.get("subtitles_corrected"):
        print(
            f"  Subtitle correction updated {subtitle_debug.get('corrected_segments_count', 0)} segment(s) "
            f"for {cut_file.name}"
        )

    raw_output = output_raw / cut_file.name
    if cut_file.resolve() != raw_output.resolve():
        shutil.copy2(cut_file, raw_output)
        print(f"Saved raw video: {raw_output.name}")
    else:
        print(f"Raw video already present: {raw_output.name}")

    ass_file = cut_file.parent / f"{cut_file.stem}.ass"
    with open(ass_file, "w", encoding="utf-8-sig", newline="\n") as file_handle:
        file_handle.write(create_ass_file(events))

    subs_output = output_subs / cut_file.name
    try:
        add_subtitles_to_video(raw_output, subs_output, ass_file)
        print(f"Added subtitles: {subs_output.name}")
    except subprocess.CalledProcessError as exc:
        print(f"  Error while adding subtitles: {exc}")
    finally:
        if ass_file.exists():
            ass_file.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add podcast subtitles with one stable visual style.")
    parser.add_argument("--transcript", default="transcripts/final_transcript.json", help="Transcript JSON path")
    parser.add_argument("--input-dir", default="cuts", help="Input directory with segment videos")
    parser.add_argument("--output-raw", default="cuts/raw", help="Output directory for raw cuts")
    parser.add_argument("--output-subs", default="cuts/subtitles", help="Output directory for subtitled videos")
    parser.add_argument("--content-type", default="podcast", help="Content type hint. The MVP renders one podcast subtitle style.")
    parser.add_argument("--expected-speaker-mode", default="unknown", help="Expected speaker mode hint: single, multi or unknown")
    parser.add_argument("--max-effective-speakers", type=int, default=0, help="Optional cap for subtitle speaker labels")
    parser.add_argument(
        "--subtitle-correction-mode",
        default="off",
        choices=(SUBTITLE_CORRECTION_MODE_OFF,),
        help="Subtitle text correction is disabled; deterministic formatting is always used.",
    )
    parser.add_argument("--semantic-model", default=DEFAULT_SUBTITLE_CORRECTION_MODEL, help=argparse.SUPPRESS)
    parser.add_argument("--request-timeout", type=float, default=45.0, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    transcript_path = Path(args.transcript)
    input_dir = Path(args.input_dir)
    output_raw = Path(args.output_raw)
    output_subs = Path(args.output_subs)

    if not transcript_path.exists():
        print(f"Missing transcript file: {transcript_path}")
        return
    if not input_dir.exists():
        print(f"Missing input directory: {input_dir}")
        return

    print(f"Loading transcript: {transcript_path}")
    transcript = load_transcript(transcript_path)
    cut_files = sorted(input_dir.glob("segment_*.mp4"))
    if not cut_files:
        print(f"No segment_*.mp4 files found in {input_dir}")
        return

    print(f"Found {len(cut_files)} cut videos")
    print()
    for cut_file in cut_files:
        print(f"Processing: {cut_file.name}")
        process_cut_file(
            cut_file,
            transcript,
            output_raw,
            output_subs,
            content_type_hint=args.content_type,
            expected_speaker_mode=args.expected_speaker_mode,
            max_effective_speakers=args.max_effective_speakers or None,
            subtitle_correction_mode=args.subtitle_correction_mode,
            semantic_model=args.semantic_model,
            api_key=None,
            request_timeout=args.request_timeout,
        )
        print()

    print("Done!")
    print(f"  Raw videos: {output_raw.resolve()}")
    print(f"  Subtitled videos: {output_subs.resolve()}")


if __name__ == "__main__":
    main()
