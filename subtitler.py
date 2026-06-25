#!/usr/bin/env python3

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

from semantic_clip_director import (
    SUBTITLE_CORRECTION_MODE_OFF,
    VALID_SUBTITLE_CORRECTION_MODES,
    build_subtitle_corrector,
    normalize_subtitle_correction_mode,
)

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
DEFAULT_FONT = "Arial Black"
BASE_FONT_SIZE = 24
CHAOS_FONT_SIZE = int(BASE_FONT_SIZE * 1.65)
OUTLINE_WIDTH = 3
SHADOW_SIZE = 1
MARGIN_V = 280
EMPHASIS_COLOR = "&H0000FF00"
CHAOS_EMPHASIS_COLOR = "&H0000FF66"
KEYWORD_PATTERNS = [
    r"\d+\s*(?:zl|pln|euro|usd|dollar)",
    r"\b[A-ZÀ-ŽĄĆĘŁŃÓŚŹŻ][a-zà-žąćęłńóśźż]+\b",
    r"(?:wow|super|fantastycz|niesamowit|genialn|straszn|okropn)",
]
KEYWORD_REGEX = re.compile("|".join(KEYWORD_PATTERNS), re.IGNORECASE | re.UNICODE)
DEFAULT_SPEAKER_SMOOTHING_WINDOW = 1.25
DEFAULT_SUBTITLE_CORRECTION_MODEL = "models/gemini-2.5-flash"


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


def calculate_words_per_second(text: str, duration: float) -> float:
    if duration <= 0 or not text.strip():
        return 0.0
    return len(text.split()) / duration


def should_display_subtitle(segment: Dict, duration: float) -> bool:
    chaos = bool(segment.get("chaos", False))
    importance = int(segment.get("importance", 3))
    text = str(segment.get("text", "")).strip()
    if chaos and importance < 5:
        return False
    if calculate_words_per_second(text, duration) > 4.0 and importance < 4:
        return False
    return True


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
    normalized_content_type = str(content_type_hint or "").strip().lower()
    raw_events: List[Dict] = []
    segment_end = segment_start + segment_duration
    transcript_for_events, smoothing_metadata = smooth_speaker_labels(
        transcript,
        max_flip_duration=speaker_smoothing_window,
        return_metadata=True,
    )

    for item in transcript_for_events:
        seg_start = parse_time(item.get("start", "00:00"))
        seg_end = parse_time(item.get("end", "00:00"))
        text = str(item.get("text", "")).strip()
        importance = int(item.get("importance", 3))
        chaos = bool(item.get("chaos", False))
        speaker = normalize_speaker(item)

        if not text or seg_end <= segment_start or seg_start >= segment_end:
            continue

        overlap_start = max(seg_start, segment_start)
        overlap_end = min(seg_end, segment_end)
        rel_start = overlap_start - segment_start
        rel_end = overlap_end - segment_start
        if rel_end <= rel_start:
            continue
        if not should_display_subtitle(item, rel_end - rel_start):
            continue

        raw_events.append(
            {
                "start": rel_start,
                "end": rel_end,
                "text": text,
                "speaker": speaker,
                "importance": importance,
                "chaos": chaos,
            }
        )

    stabilized_events, speaker_stability_metadata = stabilize_speaker_events(
        raw_events,
        content_type_hint=content_type_hint,
        expected_speaker_mode=expected_speaker_mode,
        max_effective_speakers=max_effective_speakers,
    )
    correction_mode = normalize_subtitle_correction_mode(subtitle_correction_mode)
    if correction_mode == SUBTITLE_CORRECTION_MODE_OFF:
        corrected_events = [dict(event) for event in stabilized_events]
        correction_metadata = {
            "subtitles_corrected": False,
            "subtitle_corrector_used": "off",
            "corrected_segments_count": 0,
            "correction_fallback_reason": "",
        }
    else:
        corrector = build_subtitle_corrector(
            mode=correction_mode,
            model_name=semantic_model,
            request_timeout=request_timeout,
            api_key=api_key,
        )
        corrected_events, correction_metadata = corrector.correct_subtitle_text(
            stabilized_events,
            mode=correction_mode,
            content_type_hint=content_type_hint,
        )

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
        display_speaker = (
            normalize_speaker({"speaker": detected_speaker})
            if use_per_speaker and normalize_speaker({"speaker": detected_speaker}) in stable_speakers
            else DEFAULT_STYLE_NAME
        )
        if normalized_content_type == "podcast":
            display_text = str(event["text"])
            render_style = DEFAULT_STYLE_NAME
        else:
            display_text = event["text"] if int(event.get("importance", 3)) >= 5 else (
                apply_emphasis(str(event["text"]), display_speaker)
                if int(event.get("importance", 3)) >= 4
                else event["text"]
            )
            render_style = display_speaker
        events.append({
            **event,
            "speaker": display_speaker,
            "detected_speaker": detected_speaker,
            "render_style": render_style,
            "text": display_text,
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
    bold: int = 0,
    alignment: int = 2,
) -> str:
    return (
        f"Style: {name},{DEFAULT_FONT},{font_size},{color},&H00000000,{outline_color},&H00000000,"
        f"{bold},0,0,0,100,100,0,0,1,{OUTLINE_WIDTH},{SHADOW_SIZE},{alignment},70,70,{MARGIN_V},1"
    )


def create_ass_file(events: List[Dict]) -> str:
    lines: List[str] = [
        "[Script Info]",
        "Title: Podcast Shorts Cutter Subtitles",
        "ScriptType: v4.00+",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "Collisions: Normal",
        "PlayResX: 1920",
        "PlayResY: 1080",
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
        text = str(event["text"]).replace("\n", r"\N")
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


def add_subtitles_to_video(input_video: Path, output_video: Path, ass_file: Path) -> None:
    output_video.parent.mkdir(parents=True, exist_ok=True)
    escaped_path = str(ass_file).replace("\\", "\\\\").replace(":", "\\:")
    filter_str = f"ass='{escaped_path}'"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_video),
        "-vf",
        filter_str,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
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
        choices=VALID_SUBTITLE_CORRECTION_MODES,
        help="Experimental subtitle correction prototype. Disabled by default and not part of the recommended production flow.",
    )
    parser.add_argument("--semantic-model", default=DEFAULT_SUBTITLE_CORRECTION_MODEL, help="Experimental Gemini model for the prototype subtitle corrector")
    parser.add_argument("--request-timeout", type=float, default=45.0, help="Timeout in seconds for optional subtitle correction")
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
