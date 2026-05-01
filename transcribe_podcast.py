#!/usr/bin/env python3
"""
transcribe_podcast.py

Silent operation: prints only progress lines like "Fragment X/Y przetworzony".

Behavior:
- Splits audio by silence intervals (ffmpeg silencedetect).
- Groups segments so each uploaded chunk stays under --max-upload-mb (default 25MB).
- Ensures cuts fall on detected silences where possible; tries additional detection inside long segments.
- Transcribes chunks via Google Gemini (google.generativeai) with robust JSON extraction.
- Merges per-chunk JSONs into final output and removes temporary files immediately.

Run example:
  python transcribe_podcast.py --file "input/JAK WYGLĄDAŁO PIERWSZE SPOTKANIE Z OJCEM？ -Naruciak.mp3" \
    --out transcripts/Naruciak_Final.json
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from dotenv import load_dotenv

try:
    import google.generativeai as genai
except Exception:
    try:
        import google.genai as genai
    except Exception:
        genai = None


def run(cmd, capture=False):
    if capture:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return subprocess.run(cmd)


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
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out = p.stdout.strip()
    try:
        return float(out)
    except Exception:
        raise RuntimeError(f"Could not read duration of {path}: {out}")


def detect_silences(path: Path, silence_thresh: float, silence_len: float, start: float = None, end: float = None):
    # runs ffmpeg silencedetect on entire file or on a time range
    cmd = ["ffmpeg", "-hide_banner"]
    if start is not None:
        cmd += ["-ss", str(start)]
    if end is not None and start is not None:
        cmd += ["-to", str(end)]
    cmd += ["-i", str(path), "-af", f"silencedetect=noise={silence_thresh}dB:d={silence_len}", "-f", "null", "-"]
    p = run(cmd, capture=True)
    out = p.stdout or ""

    silences = []
    open_start = None
    for line in out.splitlines():
        line = line.strip()
        m_start = re.search(r"silence_start: (\d+(?:\.\d+)?)", line)
        m_end = re.search(r"silence_end: (\d+(?:\.\d+)?) \| silence_duration: (\d+(?:\.\d+)?)", line)
        if m_start:
            open_start = float(m_start.group(1))
        if m_end:
            end_s = float(m_end.group(1))
            if open_start is None:
                start_s = 0.0
            else:
                start_s = open_start
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

    # Merge tiny segments into previous to avoid extremely short fragments
    merged = []
    for s, e in segments:
        if not merged:
            merged.append([s, e])
            continue
        if e - s < min_segment_len:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return [(float(s), float(e)) for s, e in merged]


def sec_to_hms(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def parse_time_to_seconds(t: str) -> float:
    parts = t.split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


def extract_json(text: str):
    if not text:
        raise ValueError("Empty response from model")
    
    # Try direct parsing
    try:
        return json.loads(text.strip())
    except Exception:
        pass

    # Try to find the first '[' and last ']'
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        cand = text[start:end+1]
        try:
            return json.loads(cand)
        except Exception:
            # If it still fails, it might be due to markdown code blocks like ```json ... ```
            # or some trailing text. Let's try to remove ```json and ``` if they are inside our candidate
            # although usually they are outside.
            pass

    # More aggressive cleanup for markdown and common issues
    cleaned = text
    # Remove markdown code fences
    cleaned = re.sub(r'```(?:json)?\s*(.*?)\s*```', r'\1', cleaned, flags=re.S)
    
    # Try again after stripping markdown
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        cand = cleaned[start:end+1]
        try:
            return json.loads(cand)
        except Exception:
            # Last ditch effort: try to fix common JSON issues like trailing commas
            # (though Gemini usually doesn't do this, it's good for robustness)
            try:
                # Remove trailing commas before closing brackets
                fixed = re.sub(r',\s*([\]}])', r'\1', cand)
                return json.loads(fixed)
            except Exception:
                pass

    raise ValueError(f"Nie udało się wyodrębnić poprawnego JSON z odpowiedzi modelu. Otrzymany tekst: {text[:200]}...")


def merge_transcripts(chunk_jsons, offsets):
    merged = []
    for jpath, offset in zip(chunk_jsons, offsets):
        try:
            with open(jpath, "r", encoding="utf-8") as fh:
                items = json.load(fh)
        except Exception:
            continue
        for it in items:
            try:
                s = parse_time_to_seconds(it["start"]) + offset
                e = parse_time_to_seconds(it["end"]) + offset
                merged.append({"start": sec_to_hms(s), "end": sec_to_hms(e), "text": it.get("text", "")})
            except Exception:
                continue
    merged.sort(key=lambda x: parse_time_to_seconds(x["start"]))
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
    if genai is None:
        raise RuntimeError("google.genai / google.generativeai is not installed")
    if hasattr(genai, 'configure'):
        genai.configure(api_key=api_key)
    uploaded = None
    for attempt in range(1, max_retries + 1):
        try:
            uploaded = genai.upload_file(str(chunk_path))
            break
        except Exception as exc:
            print(f"upload_file attempt {attempt} failed: {exc}", file=sys.stderr)
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)

    model = genai.GenerativeModel(model_name)
    prompt = (
        "Przetranskrybuj załączony plik audio. Zwróć WYŁĄCZNIE czyste JSON-owe pole będące tablicą obiektów. "
        "Każdy obiekt musi mieć klucze \"start\", \"end\", \"text\". Czas w formacie MM:SS (np. \"00:00\"). "
        "Nie dodawaj żadnego dodatkowego tekstu ani formatowania."
    )

    for attempt in range(1, max_retries + 1):
        try:
            result = model.generate_content([uploaded, prompt])
            text = getattr(result, 'text', None)
            if not text:
                cand = None
                if hasattr(result, 'candidates') and result.candidates:
                    cand = result.candidates[0]
                if cand is not None:
                    text = str(cand)
            if not text:
                raise ValueError("Model response contains no text")
            parsed = extract_json(text)
            return parsed
        except Exception as exc:
            print(f"transcribe_chunk attempt {attempt} failed: {exc}", file=sys.stderr)
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)


def main():
    print("Starting transcription...")
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Input audio file (mp3)")
    parser.add_argument("--out", default=None, help="Output merged JSON file path")
    parser.add_argument("--max-upload-mb", type=float, default=50.0, help="Approx max upload size per chunk in MB")
    parser.add_argument("--max-chunk-seconds", type=float, default=1200.0, help="Maximum duration for a single chunk in seconds")
    parser.add_argument("--silence-threshold", type=float, default=-30.0, help="dB threshold for silence detection")
    parser.add_argument("--min-silence-len", type=float, default=0.8, help="minimum silence to detect (s)")
    parser.add_argument("--min-segment-len", type=float, default=5.0, help="minimum kept segment length (s)")
    parser.add_argument("--model", default='models/gemini-2.5-flash', help="Model name for Gemini")
    args = parser.parse_args()

    inp = Path(args.file)
    if not inp.exists():
        print(f"Input file not found: {inp}", file=sys.stderr)
        sys.exit(2)

    script_dir = Path(__file__).parent
    dotenv_path = script_dir / '.env'
    if dotenv_path.exists():
        load_dotenv(dotenv_path)

    api_key = os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY') or os.environ.get('API_KEY')
    if not api_key:
        print('Missing GOOGLE_API_KEY in environment', file=sys.stderr)
        sys.exit(1)

    if genai is None:
        print('google.genai / google.generativeai not installed', file=sys.stderr)
        sys.exit(1)

    duration = get_duration(inp)
    print(f"Duration: {duration}")
    file_size = inp.stat().st_size
    bytes_per_second = file_size / max(duration, 1.0)
    max_upload_bytes = int(args.max_upload_mb * 1024 * 1024)
    if bytes_per_second <= 0:
        max_seconds = 300
    else:
        # use small margin
        max_seconds = max(30.0, (max_upload_bytes / bytes_per_second) * 0.95)
    max_seconds = min(max_seconds, args.max_chunk_seconds)
    print(f"Max chunk seconds: {max_seconds:.1f}")
    silences = detect_silences(inp, args.silence_threshold, args.min_silence_len)
    print(f"Detected {len(silences)} silences")
    segments = build_segments_from_silences(silences, duration, args.min_segment_len)

    # Group segments into chunks not exceeding max_seconds when possible
    groups = []
    cur_s = None
    cur_e = None
    cur_len = 0.0
    for s, e in segments:
        seg_len = e - s
        if cur_s is None:
            cur_s, cur_e = s, e
            cur_len = seg_len
            continue
        if cur_len + seg_len <= max_seconds:
            cur_e = e
            cur_len += seg_len
        else:
            groups.append((cur_s, cur_e))
            cur_s, cur_e = s, e
            cur_len = seg_len
    if cur_s is not None:
        groups.append((cur_s, cur_e))

    # For groups longer than max_seconds, attempt to find internal silences and split further
    final_groups = []
    for g_s, g_e in groups:
        g_len = g_e - g_s
        if g_len <= max_seconds:
            final_groups.append((g_s, g_e))
            continue
        # look for finer silences inside this range
        inner_silences = detect_silences(inp, args.silence_threshold - 5, max(0.4, args.min_silence_len/2), start=g_s, end=g_e)
        if inner_silences:
            inner_segments = build_segments_from_silences(inner_silences, g_e - g_s, args.min_segment_len)
            # inner_segments are relative to g_s; convert and group
            rel_segs = [(g_s + a, g_s + b) for a, b in inner_segments]
            cur = None
            cur_len = 0.0
            for s2, e2 in rel_segs:
                segl = e2 - s2
                if cur is None:
                    cur = [s2, e2]
                    cur_len = segl
                    continue
                if cur_len + segl <= max_seconds:
                    cur[1] = e2
                    cur_len += segl
                else:
                    final_groups.append((cur[0], cur[1]))
                    cur = [s2, e2]
                    cur_len = segl
            if cur is not None:
                final_groups.append((cur[0], cur[1]))
        else:
            # try to split at nearest silence after max_seconds within 30s window
            splits = []
            pos = g_s
            while pos < g_e:
                target = pos + max_seconds
                # search for silence in window [target, target+30]
                window_end = min(g_e, target + 30)
                window_silences = detect_silences(inp, args.silence_threshold, args.min_silence_len, start=target, end=window_end)
                cut_at = None
                if window_silences:
                    # pick first silence end in window
                    cut_at = window_silences[0][1]
                else:
                    # search backwards [target-30, target]
                    window_start = max(pos, target - 30)
                    window_silences = detect_silences(inp, args.silence_threshold, args.min_silence_len, start=window_start, end=target)
                    if window_silences:
                        cut_at = window_silences[-1][0]
                if cut_at is None:
                    # last resort: force cut at target (may split speech)
                    cut_at = min(g_e, target)
                splits.append((pos, min(cut_at, g_e)))
                pos = min(cut_at, g_e)
                if pos >= g_e - 0.01:
                    break
            # append splits
            for a, b in splits:
                if b - a > 0.5:
                    final_groups.append((a, b))

    # prepare temp dirs
    cache_dir = Path("transcripts/cache") / inp.stem
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp_transcripts = cache_dir / "chunks"
    tmp_transcripts.mkdir(parents=True, exist_ok=True)

    chunk_jsons = []
    offsets = []

    total = len(final_groups)
    for idx, (s, e) in enumerate(final_groups):
        out_chunk_json = tmp_transcripts / f"chunk{idx:04d}.json"
        
        # Check if already processed (and has actual transcription data)
        if out_chunk_json.exists():
            try:
                with open(out_chunk_json, "r", encoding="utf-8") as fh:
                    test_load = json.load(fh)
                    # Only skip if file has non-empty transcription
                    if test_load and len(test_load) > 0:
                        print(f"Chunk {idx+1}/{total} found in cache, skipping.")
                        chunk_jsons.append(out_chunk_json)
                        offsets.append(s)
                        continue
                    # If file exists but is empty, delete it to retry
                    if not test_load or len(test_load) == 0:
                        out_chunk_json.unlink(missing_ok=True)
            except Exception:
                # If file is corrupted, delete and retry
                out_chunk_json.unlink(missing_ok=True)

        print(f"Processing chunk {idx+1}/{total}: {s:.1f}s - {e:.1f}s")
        chunk_path = cache_dir / f"chunk{idx:04d}.mp3"
        if not chunk_path.exists():
            cut_chunk(inp, chunk_path, s, e)
        
        # transcribe
        parsed = None
        try:
            parsed = transcribe_chunk(chunk_path, args.model, api_key)
        except Exception as exc:
            print(f"⚠ Chunk {idx+1}/{total} failed: {exc}", file=sys.stderr)
            parsed = None
        
        # Only save to cache if transcription succeeded (non-empty)
        if parsed is not None and len(parsed) > 0:
            try:
                with open(out_chunk_json, "w", encoding="utf-8") as fh:
                    json.dump(parsed, fh, ensure_ascii=False, indent=2)
                chunk_jsons.append(out_chunk_json)
                offsets.append(s)
            except Exception as exc:
                print(f"✗ Failed to write chunk JSON {out_chunk_json}: {exc}", file=sys.stderr)
        else:
            # Don't save empty results; mark as failed for later validation
            print(f"⚠ Chunk {idx+1}/{total}: No transcription data received", file=sys.stderr)
            chunk_jsons.append(None)  # Mark as failed
            offsets.append(s)
        
        print(f"Fragment {idx+1}/{total} przetworzony")

    # Validate: check if all chunks were successfully transcribed
    failed_chunks = [i for i, cj in enumerate(chunk_jsons) if cj is None]
    if failed_chunks:
        print(f"\n✗ BŁĄD: Transkrypcja nie powiodła się dla {len(failed_chunks)} fragmentów:", file=sys.stderr)
        for idx in failed_chunks:
            s, e = final_groups[idx]
            print(f"  - Chunk {idx+1}/{total}: {s:.1f}s - {e:.1f}s", file=sys.stderr)
        print(f"\n✗ Przerwanie przed zapisaniem final_transcript.json", file=sys.stderr)
        sys.exit(1)
    
    # merge and output
    merged = merge_transcripts(chunk_jsons, offsets)
    
    # Final check: ensure merged data is not empty
    if not merged or len(merged) == 0:
        print(f"\n✗ BŁĄD: Brak wyników transkrypcji po scaleniu wszystkich fragmentów", file=sys.stderr)
        sys.exit(1)
    
    out_path = Path(args.out) if args.out else Path("transcripts") / (inp.stem + "_Final.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=2)

    print(f"✓ Transcription finished! Saved to {out_path}")


if __name__ == "__main__":
    main()
