#!/usr/bin/env python3
"""
transcribe_podcast.py

Silent transcription pipeline:
- splits audio by silence,
- groups chunks to fit upload limits,
- sends chunks to Gemini with retry/backoff,
- merges transcript JSON into one final file.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from gemini_transport import bootstrap_ssl_certificates, generate_file_text_with_transport, get_api_key

try:
    import google.generativeai as genai
except Exception:
    try:
        import google.genai as genai
    except Exception:
        genai = None


RETRY_DELAYS_SECONDS = (5, 10, 20)


def run(cmd, capture=False):
    if capture:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return subprocess.run(cmd)


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
    print(f"{operation} attempt {attempt} failed ({reason}); retrying in {delay}s", file=sys.stderr)
    time.sleep(delay)


def upload_file_with_backoff(path: Path, max_retries: int = 3):
    for attempt in range(1, max_retries + 1):
        try:
            return genai.upload_file(str(path))
        except Exception as exc:
            if attempt == max_retries:
                raise
            wait_before_retry(exc, attempt, max_retries, "upload_file")
    raise RuntimeError("Gemini upload failed")


def generate_content_with_backoff(model, payload, max_retries: int = 3):
    for attempt in range(1, max_retries + 1):
        try:
            return model.generate_content(payload)
        except Exception as exc:
            if attempt == max_retries:
                raise
            wait_before_retry(exc, attempt, max_retries, "generate_content")
    raise RuntimeError("Gemini generation failed")


def get_duration(path: Path) -> float:
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
    completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out = completed.stdout.strip()
    try:
        return float(out)
    except Exception as exc:
        raise RuntimeError(f"Could not read duration of {path}: {out}") from exc


def detect_silences(path: Path, silence_thresh: float, silence_len: float, start: float = None, end: float = None):
    cmd = ["ffmpeg", "-hide_banner"]
    if start is not None:
        cmd += ["-ss", str(start)]
    if end is not None and start is not None:
        cmd += ["-to", str(end)]
    cmd += ["-i", str(path), "-af", f"silencedetect=noise={silence_thresh}dB:d={silence_len}", "-f", "null", "-"]
    completed = run(cmd, capture=True)
    output = completed.stdout or ""

    silences = []
    open_start = None
    for line in output.splitlines():
        line = line.strip()
        start_match = re.search(r"silence_start: (\d+(?:\.\d+)?)", line)
        end_match = re.search(r"silence_end: (\d+(?:\.\d+)?) \| silence_duration: (\d+(?:\.\d+)?)", line)
        if start_match:
            open_start = float(start_match.group(1))
        if end_match:
            end_s = float(end_match.group(1))
            start_s = 0.0 if open_start is None else open_start
            silences.append((start_s, end_s))
            open_start = None
    return silences


def build_segments_from_silences(silences, duration, min_segment_len: float = 1.0):
    segments = []
    prev = 0.0
    for start, end in silences:
        if start - prev > 0.05:
            segments.append((prev, start))
        prev = end
    if duration - prev > 0.05:
        segments.append((prev, duration))
    if not segments:
        return [(0.0, duration)]

    merged = []
    for start, end in segments:
        if not merged:
            merged.append([start, end])
            continue
        if end - start < min_segment_len:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(float(start), float(end)) for start, end in merged]


def sec_to_hms(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:05.2f}"
    return f"{minutes:02d}:{secs:05.2f}"


def parse_time_to_seconds(value: str) -> float:
    parts = [float(part) for part in str(value).strip().replace(",", ".").split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


def normalize_speaker_label(value) -> str:
    text = str(value or "").strip()
    if not text:
        return "Speaker 0"

    match = re.search(r"(\d+)", text)
    if match:
        return f"Speaker {int(match.group(1))}"

    if text.lower().startswith("speaker "):
        suffix = text.split()[-1].strip().upper()
        if len(suffix) == 1 and "A" <= suffix <= "Z":
            return f"Speaker {ord(suffix) - ord('A')}"

    return f"Speaker {text}"


def extract_json(text: str):
    if not text:
        raise ValueError("Empty response from model")

    try:
        return json.loads(text.strip())
    except Exception:
        pass

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except Exception:
            pass

    cleaned = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", text, flags=re.S)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidate = cleaned[start : end + 1]
        try:
            return json.loads(candidate)
        except Exception:
            fixed = re.sub(r",\s*([\]}])", r"\1", candidate)
            return json.loads(fixed)

    raise ValueError(f"Could not extract valid JSON from model response: {text[:240]}...")


def normalize_word_items(words, offset=0.0):
    normalized = []
    if not isinstance(words, list):
        return normalized
    for word in words:
        if not isinstance(word, dict):
            continue
        text = str(word.get("text") or word.get("word") or "").strip()
        if not text:
            continue
        try:
            start = parse_time_to_seconds(word["start"]) + offset
            end = parse_time_to_seconds(word["end"]) + offset
        except Exception:
            continue
        if end <= start:
            continue
        normalized.append({"start": sec_to_hms(start), "end": sec_to_hms(end), "text": text})
    return normalized


def normalize_segment_item(item, offset=0.0):
    start = parse_time_to_seconds(item["start"]) + offset
    end = parse_time_to_seconds(item["end"]) + offset
    if end <= start:
        raise ValueError("Segment end must be greater than start.")

    normalized = {
        "start": sec_to_hms(start),
        "end": sec_to_hms(end),
        "text": str(item.get("text", "")).strip(),
    }

    speaker = normalize_speaker_label(item.get("speaker") or item.get("speaker_id") or item.get("speakerId"))
    normalized["speaker"] = speaker

    importance = item.get("importance")
    if importance is not None:
        try:
            normalized["importance"] = int(importance)
        except Exception:
            pass

    chaos = item.get("chaos")
    if isinstance(chaos, bool):
        normalized["chaos"] = chaos

    words = normalize_word_items(item.get("words"), offset=offset)
    if words:
        normalized["words"] = words

    return normalized


def merge_transcripts(chunk_jsons, offsets):
    merged = []
    for json_path, offset in zip(chunk_jsons, offsets):
        try:
            with open(json_path, "r", encoding="utf-8") as file_handle:
                items = json.load(file_handle)
        except Exception:
            continue
        for item in items:
            try:
                merged.append(normalize_segment_item(item, offset=offset))
            except Exception:
                continue
    merged.sort(key=lambda item: parse_time_to_seconds(item["start"]))
    return merged


def cut_chunk(input_path: Path, out_path: Path, start: float, end: float):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(start),
        "-to",
        str(end),
        "-i",
        str(input_path),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-y",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)


def transcribe_chunk(chunk_path: Path, model_name: str, api_key: str, max_retries: int = 3):
    prompt = (
        "Transcribe the attached audio file and return JSON only.\n"
        "Return a JSON array of segment objects.\n"
        "Each segment MUST contain:\n"
        '- "start": MM:SS.ss\n'
        '- "end": MM:SS.ss\n'
        '- "text": transcript text\n'
        '- "speaker": stable diarization label like "Speaker 0", "Speaker 1"\n'
        '- "importance": integer 1-5\n'
        '- "chaos": boolean\n'
        '- "words": array of word-level timestamps, each with "start", "end", "text" in MM:SS.ss\n\n'
        "Rules:\n"
        "1. Keep speaker labels consistent across the chunk and always use numeric labels: Speaker 0, Speaker 1, Speaker 2...\n"
        "2. importance=5 means emotionally charged, punchy, vulgar, shouted, or key narrative words.\n"
        "3. chaos=true when multiple people overlap, the audio is messy, or words are hard to isolate.\n"
        '4. "words" is required for every segment whenever possible. Be as precise as you can.\n'
        "5. Return JSON only, with no markdown.\n"
    )

    text = generate_file_text_with_transport(
        chunk_path,
        prompt,
        model_name,
        api_key,
        "Gemini chunk transcription",
        mime_type="audio/mpeg",
        response_mime_type="application/json",
        max_retries=max_retries,
    )
    if not text:
        raise ValueError("Model response contains no text")
    parsed = extract_json(text)
    if not isinstance(parsed, list):
        raise ValueError("Gemini response must be a JSON list of segments")
    return parsed


def build_final_groups(input_path: Path, duration: float, args) -> list:
    silences = detect_silences(input_path, args.silence_threshold, args.min_silence_len)
    print(f"Detected {len(silences)} silences")
    segments = build_segments_from_silences(silences, duration, args.min_segment_len)

    groups = []
    cur_s = None
    cur_e = None
    cur_len = 0.0
    for start, end in segments:
        segment_len = end - start
        if cur_s is None:
            cur_s, cur_e = start, end
            cur_len = segment_len
            continue
        if cur_len + segment_len <= args.max_seconds:
            cur_e = end
            cur_len += segment_len
        else:
            groups.append((cur_s, cur_e))
            cur_s, cur_e = start, end
            cur_len = segment_len
    if cur_s is not None:
        groups.append((cur_s, cur_e))

    final_groups = []
    for group_start, group_end in groups:
        group_len = group_end - group_start
        if group_len <= args.max_seconds:
            final_groups.append((group_start, group_end))
            continue

        inner_silences = detect_silences(
            input_path,
            args.silence_threshold - 5,
            max(0.4, args.min_silence_len / 2),
            start=group_start,
            end=group_end,
        )
        if inner_silences:
            inner_segments = build_segments_from_silences(inner_silences, group_end - group_start, args.min_segment_len)
            relative_segments = [(group_start + left, group_start + right) for left, right in inner_segments]
            cur = None
            cur_len = 0.0
            for start, end in relative_segments:
                segment_len = end - start
                if cur is None:
                    cur = [start, end]
                    cur_len = segment_len
                    continue
                if cur_len + segment_len <= args.max_seconds:
                    cur[1] = end
                    cur_len += segment_len
                else:
                    final_groups.append((cur[0], cur[1]))
                    cur = [start, end]
                    cur_len = segment_len
            if cur is not None:
                final_groups.append((cur[0], cur[1]))
            continue

        pos = group_start
        while pos < group_end:
            target = pos + args.max_seconds
            window_end = min(group_end, target + 30)
            window_silences = detect_silences(input_path, args.silence_threshold, args.min_silence_len, start=target, end=window_end)
            cut_at = None
            if window_silences:
                cut_at = window_silences[0][1]
            else:
                window_start = max(pos, target - 30)
                backward = detect_silences(input_path, args.silence_threshold, args.min_silence_len, start=window_start, end=target)
                if backward:
                    cut_at = backward[-1][0]
            if cut_at is None:
                cut_at = min(group_end, target)
            final_groups.append((pos, min(cut_at, group_end)))
            pos = min(cut_at, group_end)
            if pos >= group_end - 0.01:
                break

    return [(start, end) for start, end in final_groups if end - start > 0.5]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Input audio file")
    parser.add_argument("--out", default=None, help="Output merged JSON file path")
    parser.add_argument("--max-upload-mb", type=float, default=50.0, help="Approx max upload size per chunk in MB")
    parser.add_argument("--max-chunk-seconds", type=float, default=1200.0, help="Maximum duration for a single chunk in seconds")
    parser.add_argument("--silence-threshold", type=float, default=-30.0, help="dB threshold for silence detection")
    parser.add_argument("--min-silence-len", type=float, default=0.8, help="Minimum silence to detect in seconds")
    parser.add_argument("--min-segment-len", type=float, default=5.0, help="Minimum kept segment length in seconds")
    parser.add_argument("--model", default="models/gemini-2.5-flash", help="Gemini model name")
    return parser.parse_args()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    print("Starting transcription...")
    args = parse_args()

    input_path = Path(args.file)
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        sys.exit(2)

    script_dir = Path(__file__).parent
    dotenv_path = script_dir / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path)
    bootstrap_ssl_certificates()

    api_key = get_api_key()
    if not api_key:
        print("Missing GOOGLE_API_KEY/GEMINI_API_KEY/API_KEY in environment", file=sys.stderr)
        sys.exit(1)

    duration = get_duration(input_path)
    print(f"Duration: {duration}")
    file_size = input_path.stat().st_size
    bytes_per_second = file_size / max(duration, 1.0)
    max_upload_bytes = int(args.max_upload_mb * 1024 * 1024)
    if bytes_per_second <= 0:
        max_seconds = 300.0
    else:
        max_seconds = max(30.0, (max_upload_bytes / bytes_per_second) * 0.95)
    max_seconds = min(max_seconds, args.max_chunk_seconds)
    args.max_seconds = max_seconds
    print(f"Max chunk seconds: {max_seconds:.1f}")

    final_groups = build_final_groups(input_path, duration, args)
    cache_dir = Path("transcripts/cache") / input_path.stem
    cache_dir.mkdir(parents=True, exist_ok=True)
    temp_json_dir = cache_dir / "chunks"
    temp_json_dir.mkdir(parents=True, exist_ok=True)

    chunk_jsons = []
    offsets = []
    total = len(final_groups)

    for index, (start, end) in enumerate(final_groups):
        out_chunk_json = temp_json_dir / f"chunk{index:04d}.json"
        if out_chunk_json.exists():
            try:
                with open(out_chunk_json, "r", encoding="utf-8") as file_handle:
                    cached = json.load(file_handle)
                if cached and len(cached) > 0:
                    print(f"Chunk {index + 1}/{total} found in cache, skipping.")
                    chunk_jsons.append(out_chunk_json)
                    offsets.append(start)
                    continue
                out_chunk_json.unlink(missing_ok=True)
            except Exception:
                out_chunk_json.unlink(missing_ok=True)

        print(f"Processing chunk {index + 1}/{total}: {start:.1f}s - {end:.1f}s")
        chunk_path = cache_dir / f"chunk{index:04d}.mp3"
        if not chunk_path.exists():
            cut_chunk(input_path, chunk_path, start, end)

        parsed = None
        try:
            parsed = transcribe_chunk(chunk_path, args.model, api_key)
        except Exception as exc:
            print(f"Chunk {index + 1}/{total} failed: {exc}", file=sys.stderr)

        if parsed is not None and len(parsed) > 0:
            try:
                with open(out_chunk_json, "w", encoding="utf-8") as file_handle:
                    json.dump(parsed, file_handle, ensure_ascii=False, indent=2)
                chunk_jsons.append(out_chunk_json)
                offsets.append(start)
            except Exception as exc:
                print(f"Failed to write chunk JSON {out_chunk_json}: {exc}", file=sys.stderr)
                chunk_jsons.append(None)
                offsets.append(start)
        else:
            print(f"Chunk {index + 1}/{total}: no transcription data received", file=sys.stderr)
            chunk_jsons.append(None)
            offsets.append(start)

        print(f"Fragment {index + 1}/{total} przetworzony")

    failed_chunks = [idx for idx, chunk in enumerate(chunk_jsons) if chunk is None]
    if failed_chunks:
        print(f"\nERROR: Transcription failed for {len(failed_chunks)} chunk(s):", file=sys.stderr)
        for failed_index in failed_chunks:
            start, end = final_groups[failed_index]
            print(f"  - Chunk {failed_index + 1}/{total}: {start:.1f}s - {end:.1f}s", file=sys.stderr)
        print("\nAborting before writing final transcript JSON", file=sys.stderr)
        sys.exit(1)

    merged = merge_transcripts(chunk_jsons, offsets)
    if not merged:
        print("\nERROR: No merged transcription data was produced", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out) if args.out else Path("transcripts") / f"{input_path.stem}_Final.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as file_handle:
        json.dump(merged, file_handle, ensure_ascii=False, indent=2)

    print(f"Transcription finished! Saved to {out_path}")


if __name__ == "__main__":
    main()
