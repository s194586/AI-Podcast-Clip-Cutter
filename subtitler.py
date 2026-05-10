#!/usr/bin/env python3

import argparse
import json
import re
import shutil
import subprocess
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


def collect_speaker_styles(events: List[Dict]) -> List[str]:
    speaker_names = {DEFAULT_STYLE_NAME, "Speaker 0"}
    for event in events:
        speaker = normalize_speaker({"speaker": event.get("speaker")})
        if speaker != DEFAULT_STYLE_NAME:
            speaker_names.add(speaker)
    return sorted(
        speaker_names,
        key=lambda name: (-1 if name == DEFAULT_STYLE_NAME else int(re.search(r"(\d+)", name).group(1))),
    )


def ass_color(color_value: str) -> str:
    return f"\\c{color_value}&" if color_value.endswith("&") else f"\\c{color_value}&"


def apply_emphasis(text: str, speaker_name: str) -> str:
    color_tag = f"\\c{EMPHASIS_COLOR}&"
    reset_style = speaker_name if speaker_name != DEFAULT_STYLE_NAME else "Speaker 0"

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


def build_subtitle_events(transcript: List[Dict], segment_start: float, segment_duration: float) -> List[Dict]:
    events: List[Dict] = []
    segment_end = segment_start + segment_duration

    for item in transcript:
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

        display_text = text if importance >= 5 else (apply_emphasis(text, speaker) if importance >= 4 else text)
        events.append(
            {
                "start": rel_start,
                "end": rel_end,
                "text": display_text,
                "speaker": speaker,
                "importance": importance,
                "chaos": chaos,
            }
        )

    return events


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
        "Title: Viral Cutter AI Subtitles",
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
        style = CHAOS_EMPHASIS_STYLE if event.get("importance", 3) >= 5 else event["speaker"]
        text = str(event["text"]).replace("\n", r"\N")
        lines.append(f"Dialogue: 0,{start_time},{end_time},{style},,0,0,0,,{text}")

    return "\n".join(lines)


def extract_segment_time_from_filename(filename: str) -> Tuple[float, float]:
    name = Path(filename).stem
    pattern = r"segment_\d+_(\d{2})-(\d{2}_\d+)_(\d{2})-(\d{2}_\d+)"
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


def process_cut_file(cut_file: Path, transcript: List[Dict], output_raw: Path, output_subs: Path) -> None:
    output_raw.mkdir(parents=True, exist_ok=True)
    output_subs.mkdir(parents=True, exist_ok=True)

    segment_start, segment_end = extract_segment_time_from_filename(cut_file.name)
    segment_duration = segment_end - segment_start
    events = build_subtitle_events(transcript, segment_start, segment_duration)
    if not events:
        print(f"  Warning: no subtitle events for {cut_file.name}")

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
    parser = argparse.ArgumentParser(description="Add premium subtitles with speaker diarization.")
    parser.add_argument("--transcript", default="transcripts/final_transcript.json", help="Transcript JSON path")
    parser.add_argument("--input-dir", default="cuts", help="Input directory with segment videos")
    parser.add_argument("--output-raw", default="cuts/raw", help="Output directory for raw cuts")
    parser.add_argument("--output-subs", default="cuts/subtitles", help="Output directory for subtitled videos")
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
        process_cut_file(cut_file, transcript, output_raw, output_subs)
        print()

    print("Done!")
    print(f"  Raw videos: {output_raw.resolve()}")
    print(f"  Subtitled videos: {output_subs.resolve()}")


if __name__ == "__main__":
    main()
