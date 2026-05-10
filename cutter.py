import argparse
import json
import re
import subprocess
import tempfile
from collections import deque
from pathlib import Path

import cv2
import mediapipe as mp

MAX_SHORT_DURATION = 60.0
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
FACE_SAMPLE_STRIDE = 5
SMOOTHING_WINDOW = 15
REACTION_SILENCE_SECONDS = 3.0
PUNCH_IN_ZOOM = 1.15
REACTION_ZOOM = 1.08
MIN_DETECTION_CONFIDENCE = 0.5
MIN_TRACKING_CONFIDENCE = 0.5
FACE_DETECTOR_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite"
FACE_DETECTOR_MODEL_PATH = Path("models") / "blaze_face_short_range.tflite"
WORD_RE = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)


def parse_time(value):
    if isinstance(value, (int, float)):
        return float(value)
    parts = [part for part in str(value).strip().replace(",", ".").split(":") if part]
    if len(parts) == 1:
        return float(parts[0])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"Invalid timestamp format: {value}")


def clamp(value, lower, upper):
    return max(lower, min(upper, value))


def file_has_audio(path):
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(path),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    return bool(completed.stdout.strip())


def file_has_video(path):
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(path),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    return bool(completed.stdout.strip())


def load_windows(windows_file):
    with open(windows_file, "r", encoding="utf-8") as file_handle:
        windows = json.load(file_handle)
    if not isinstance(windows, list):
        raise ValueError("Windows file must contain a JSON list.")
    return windows


def extract_word_timestamps(segment):
    words = []
    raw_words = segment.get("words")
    if isinstance(raw_words, list):
        for item in raw_words:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or item.get("word") or "").strip()
            if not text:
                continue
            try:
                start = parse_time(item["start"])
                end = parse_time(item["end"])
            except Exception:
                continue
            if end <= start:
                continue
            words.append({"text": text, "start": start, "end": end, "source": "transcript"})
    return words


def approximate_word_timestamps(segment):
    text = str(segment.get("text", "")).strip()
    if not text:
        return []

    start = parse_time(segment["start"])
    end = parse_time(segment["end"])
    duration = end - start
    if duration <= 0:
        return []

    matches = list(WORD_RE.finditer(text))
    if not matches:
        return []

    total_units = sum(max(1, len(match.group(0))) for match in matches)
    cursor = start
    words = []
    consumed_units = 0

    for index, match in enumerate(matches):
        token = match.group(0)
        token_units = max(1, len(token))
        if index == len(matches) - 1:
            word_end = end
        else:
            consumed_units += token_units
            portion = consumed_units / total_units
            word_end = start + duration * portion
        words.append({"text": token, "start": cursor, "end": word_end, "source": "estimated"})
        cursor = word_end

    return words


def load_transcript(transcript_file):
    path = Path(transcript_file)
    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)
    if isinstance(data, dict) and "segments" in data:
        data = data["segments"]
    if not isinstance(data, list):
        return []

    segments = []
    for item in data:
        try:
            start = parse_time(item["start"])
            end = parse_time(item["end"])
        except Exception:
            continue
        if end <= start:
            continue

        text = str(item.get("text", "")).strip()
        words = extract_word_timestamps(item)
        if not words:
            words = approximate_word_timestamps(item)

        importance = item.get("importance")
        try:
            importance = int(importance) if importance is not None else 3
        except Exception:
            importance = 3

        speaker = (
            item.get("speaker")
            or item.get("speaker_id")
            or item.get("speakerId")
            or "Speaker 0"
        )

        segments.append(
            {
                "start": start,
                "end": end,
                "text": text,
                "words": words,
                "importance": importance,
                "speaker": str(speaker).strip() or "Speaker 0",
                "chaos": bool(item.get("chaos", False)),
            }
        )

    return sorted(segments, key=lambda item: item["start"])


def load_cutting_log(log_path):
    path = Path(log_path)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as file_handle:
            data = json.load(file_handle)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_cutting_log(log_path, log):
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file_handle:
        json.dump(log, file_handle, ensure_ascii=False, indent=2)


def upsert_cutter_adjustment(log, entry):
    adjustments = log.setdefault("cutter_adjustments", [])
    adjustments = [item for item in adjustments if item.get("segment_index") != entry.get("segment_index")]
    adjustments.append(entry)
    adjustments.sort(key=lambda item: item.get("segment_index", 0))
    log["cutter_adjustments"] = adjustments


def flatten_words(segments):
    words = []
    for segment in segments:
        for word in segment.get("words", []):
            if word["end"] <= word["start"]:
                continue
            words.append(word)
    return sorted(words, key=lambda item: item["start"])


def snap_start_to_word_boundary(timestamp, words):
    for word in words:
        if word["start"] < timestamp < word["end"]:
            return word["start"], f"Moved start to word boundary before '{word['text']}'."
    return timestamp, None


def snap_end_to_word_boundary(timestamp, words):
    for word in words:
        if word["start"] < timestamp < word["end"]:
            return word["end"], f"Extended end to complete word '{word['text']}'."
    return timestamp, None


def latest_word_end_before(words, limit):
    candidates = [word["end"] for word in words if word["end"] <= limit]
    return max(candidates) if candidates else None


def earliest_word_end_after(words, limit):
    candidates = [word["end"] for word in words if word["end"] > limit]
    return min(candidates) if candidates else None


def first_word_start_after(words, timestamp):
    candidates = [word["start"] for word in words if word["start"] >= timestamp]
    return min(candidates) if candidates else None


def enforce_no_mid_word(start, end, segments, *, max_duration=MAX_SHORT_DURATION):
    all_words = flatten_words(segments)
    if not all_words:
        safe_end = min(end, start + max_duration)
        return start, safe_end, ["No word timestamps available, kept raw boundaries with duration cap."], "none"

    decisions = []
    source = "transcript" if any(word.get("source") == "transcript" for word in all_words) else "estimated"

    original_start = start
    original_end = end

    start, start_decision = snap_start_to_word_boundary(start, all_words)
    if start_decision:
        decisions.append(start_decision)

    end, end_decision = snap_end_to_word_boundary(end, all_words)
    if end_decision:
        decisions.append(end_decision)

    if end - start > max_duration:
        strict_limit = start + max_duration
        snapped_end = latest_word_end_before(all_words, strict_limit)
        if snapped_end is None:
            snapped_end = strict_limit
        if snapped_end != end:
            decisions.append(
                f"Shortened end from {original_end:.2f}s to {snapped_end:.2f}s to keep the clip under {max_duration:.0f}s."
            )
        end = snapped_end

    if end <= start:
        next_word_end = earliest_word_end_after(all_words, start)
        if next_word_end is not None:
            end = min(next_word_end, start + max_duration)
            decisions.append("Recovered invalid cut bounds by extending to the next completed word.")
        else:
            end = min(original_end, start + max_duration)
            decisions.append("Recovered invalid cut bounds using the original end timestamp.")

    next_word_start = first_word_start_after(all_words, start)
    if next_word_start is not None and next_word_start > start and not decisions:
        decisions.append("Cut already landed between words, no boundary correction was needed.")

    return start, end, decisions, source


def find_input_video(input_path):
    path = Path(input_path)
    if path.is_file():
        return path

    if path.is_dir():
        candidates = list(path.glob("*.mp4")) + list(path.glob("*.mkv")) + list(path.glob("*.mov")) + list(path.glob("*.webm"))
    else:
        input_dir = Path("input")
        candidates = list(input_dir.glob("*.mp4")) + list(input_dir.glob("*.mkv")) + list(input_dir.glob("*.mov")) + list(input_dir.glob("*.webm"))

    if not candidates:
        raise FileNotFoundError("No input video was found. Pass --video explicitly.")

    scored = []
    for candidate in candidates:
        has_audio = file_has_audio(candidate)
        has_video = file_has_video(candidate)
        scored.append((has_video and has_audio, has_video, has_audio, candidate.stat().st_mtime, candidate))

    scored.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
    best = scored[0][4]
    if not scored[0][0]:
        print(f"Warning: selected file without full AV streams: {best} (video={scored[0][1]}, audio={scored[0][2]})")
    return best


def extract_audio_segment(video_path, output_path, start, duration):
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(video_path),
        "-t",
        f"{duration:.3f}",
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def encode_frames_to_video(frames_dir, output_path, fps):
    cmd = [
        "ffmpeg",
        "-y",
        "-framerate",
        f"{fps:.6f}",
        "-i",
        str(frames_dir / "frame_%06d.jpg"),
        "-c:v",
        "libx264",
        "-preset",
        "superfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def mux_video_with_audio(video_path, audio_path, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "libx264",
        "-preset",
        "superfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)


def center_crop_ffmpeg(video_path, output_path, start, duration):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-map",
        "0",
        "-vf",
        "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920",
        "-c:v",
        "libx264",
        "-preset",
        "superfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)


def collect_clip_segments(transcript, start, end):
    clip_segments = []
    for segment in transcript:
        if segment["end"] <= start:
            continue
        if segment["start"] >= end:
            break
        clip_segments.append(segment)
    return clip_segments


def find_active_segment(segments, timestamp):
    for segment in segments:
        if segment["start"] <= timestamp <= segment["end"]:
            return segment
    return None


def detect_reaction_mode(current_time, segments, avg_value):
    clip_high_energy = float(avg_value or 0.0) >= 0.45
    if not clip_high_energy:
        return False

    silence_start = current_time
    silence_end = current_time
    for segment in segments:
        if segment["end"] <= current_time:
            silence_start = max(silence_start, segment["end"])
            continue
        if segment["start"] > current_time:
            silence_end = segment["start"]
            break
        if segment["start"] <= current_time <= segment["end"]:
            return False
    return (silence_end - silence_start) >= REACTION_SILENCE_SECONDS


def determine_zoom(active_segment, reaction_mode):
    if active_segment and int(active_segment.get("importance", 3)) >= 5:
        return PUNCH_IN_ZOOM
    if reaction_mode:
        return REACTION_ZOOM
    return 1.0


class FaceAnalyzer:
    def __init__(self):
        model_path = ensure_face_detector_model()
        base_options = mp.tasks.BaseOptions(model_asset_path=str(model_path))
        options = mp.tasks.vision.FaceDetectorOptions(
            base_options=base_options,
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            min_detection_confidence=MIN_DETECTION_CONFIDENCE,
            min_suppression_threshold=0.3,
        )
        self.detector = mp.tasks.vision.FaceDetector.create_from_options(options)

    def close(self):
        self.detector.close()

    def detect(self, frame, timestamp_ms):
        height, width = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        results = self.detector.detect_for_video(mp_image, timestamp_ms)
        faces = []
        for detection in results.detections:
            bbox = detection.bounding_box
            min_x = float(max(0, bbox.origin_x))
            min_y = float(max(0, bbox.origin_y))
            bbox_width = float(bbox.width)
            bbox_height = float(bbox.height)
            if bbox_width <= 1 or bbox_height <= 1:
                continue
            max_x = min(float(width), min_x + bbox_width)
            max_y = min(float(height), min_y + bbox_height)
            score = 0.0
            if getattr(detection, "categories", None):
                score = float(detection.categories[0].score or 0.0)

            faces.append(
                {
                    "center_x": (min_x + max_x) / 2.0,
                    "center_y": (min_y + max_y) / 2.0,
                    "bbox_width": bbox_width,
                    "bbox_height": bbox_height,
                    "area_ratio": (bbox_width * bbox_height) / max(width * height, 1),
                    "expression_score": score,
                }
            )
        return faces


def ensure_face_detector_model():
    if FACE_DETECTOR_MODEL_PATH.exists():
        return FACE_DETECTOR_MODEL_PATH

    FACE_DETECTOR_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"  Downloading MediaPipe face detector model to {FACE_DETECTOR_MODEL_PATH} ...")
    cmd = [
        "curl.exe",
        "--fail",
        "--location",
        "--ssl-no-revoke",
        "--output",
        str(FACE_DETECTOR_MODEL_PATH),
        FACE_DETECTOR_MODEL_URL,
    ]
    subprocess.run(cmd, check=True)
    return FACE_DETECTOR_MODEL_PATH


def choose_face(faces, frame_width, frame_height, previous_center_x, reaction_mode):
    if not faces:
        return None

    best_face = None
    best_score = None
    frame_center_x = frame_width / 2.0

    for face in faces:
        area_score = face["area_ratio"] * 6.0
        continuity_score = 0.0
        if previous_center_x is not None:
            continuity_score = max(0.0, 1.5 - abs(face["center_x"] - previous_center_x) / max(frame_width, 1))
        center_bias = max(0.0, 1.0 - abs(face["center_x"] - frame_center_x) / max(frame_width, 1))
        expression_boost = face["expression_score"] * (12.0 if reaction_mode else 2.0)
        vertical_bias = max(0.0, 1.0 - abs(face["center_y"] - (frame_height * 0.45)) / max(frame_height, 1))
        score = area_score + continuity_score + center_bias + expression_boost + vertical_bias
        if best_score is None or score > best_score:
            best_score = score
            best_face = face

    return best_face


def smooth_state(history):
    if not history:
        return None
    return {
        "center_x": sum(item["center_x"] for item in history) / len(history),
        "center_y": sum(item["center_y"] for item in history) / len(history),
        "zoom": sum(item["zoom"] for item in history) / len(history),
    }


def crop_and_resize(frame, state):
    height, width = frame.shape[:2]
    zoom = max(1.0, float(state["zoom"]))
    crop_height = min(height, int(round(height / zoom)))
    crop_width = int(round(crop_height * 9 / 16))
    if crop_width > width:
        crop_width = width
        crop_height = min(height, int(round(crop_width * 16 / 9)))
    crop_width = max(2, crop_width)
    crop_height = max(2, crop_height)

    center_x = clamp(state["center_x"], crop_width / 2.0, width - crop_width / 2.0)
    center_y = clamp(state["center_y"], crop_height / 2.0, height - crop_height / 2.0)
    x1 = int(round(center_x - crop_width / 2.0))
    y1 = int(round(center_y - crop_height / 2.0))
    x1 = int(clamp(x1, 0, max(width - crop_width, 0)))
    y1 = int(clamp(y1, 0, max(height - crop_height, 0)))
    x2 = x1 + crop_width
    y2 = y1 + crop_height

    cropped = frame[y1:y2, x1:x2]
    if cropped.size == 0:
        cropped = frame
    return cv2.resize(cropped, (OUTPUT_WIDTH, OUTPUT_HEIGHT), interpolation=cv2.INTER_LINEAR)


def render_dynamic_segment(video_path, frames_dir, start, duration, clip_segments, window):
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video for face tracking: {video_path}")

    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    frame_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    frame_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if frame_width <= 0 or frame_height <= 0:
        capture.release()
        raise RuntimeError("Could not determine source video dimensions.")

    start_frame = max(0, int(round(start * fps)))
    total_frames = max(1, int(round(duration * fps)))
    capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    frames_dir.mkdir(parents=True, exist_ok=True)

    analyzer = FaceAnalyzer()
    history = deque(maxlen=SMOOTHING_WINDOW)
    current_state = {
        "center_x": frame_width / 2.0,
        "center_y": frame_height / 2.0,
        "zoom": 1.0,
    }
    previous_center_x = None
    detected_frames = 0
    fallback_frames = 0
    reaction_frames = 0
    zoom_frames = 0

    try:
        for frame_index in range(total_frames):
            ok, frame = capture.read()
            if not ok:
                break

            absolute_time = start + (frame_index / max(fps, 1.0))
            active_segment = find_active_segment(clip_segments, absolute_time)
            reaction_mode = detect_reaction_mode(absolute_time, clip_segments, window.get("avg_value"))

            if frame_index % FACE_SAMPLE_STRIDE == 0:
                timestamp_ms = int(round(absolute_time * 1000))
                faces = analyzer.detect(frame, timestamp_ms)
                target_face = choose_face(
                    faces,
                    frame_width,
                    frame_height,
                    previous_center_x,
                    reaction_mode,
                )
                if target_face:
                    current_state = {
                        "center_x": target_face["center_x"],
                        "center_y": target_face["center_y"],
                        "zoom": determine_zoom(active_segment, reaction_mode),
                    }
                    previous_center_x = target_face["center_x"]
                    detected_frames += 1
                    if reaction_mode:
                        reaction_frames += 1
                    if current_state["zoom"] > 1.0:
                        zoom_frames += 1
                else:
                    fallback_frames += 1

            history.append(dict(current_state))
            smoothed = smooth_state(history) or current_state
            framed = crop_and_resize(frame, smoothed)
            frame_path = frames_dir / f"frame_{frame_index + 1:06d}.jpg"
            cv2.imwrite(str(frame_path), framed, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    finally:
        capture.release()
        analyzer.close()
        del capture

    return {
        "fps": fps,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "frames_rendered": total_frames,
        "sampled_detections": detected_frames,
        "fallback_samples": fallback_frames,
        "reaction_samples": reaction_frames,
        "zoom_samples": zoom_frames,
        "sample_stride": FACE_SAMPLE_STRIDE,
        "smoothing_window": SMOOTHING_WINDOW,
    }


def cut_segment(video_path, output_path, start, duration, clip_segments, window):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="face_track_") as temp_dir:
        temp_dir_path = Path(temp_dir)
        frames_dir = temp_dir_path / "frames"
        temp_video_path = temp_dir_path / f"{output_path.stem}_silent.avi"
        temp_audio_path = temp_dir_path / f"{output_path.stem}_audio.m4a"

        render_stats = render_dynamic_segment(video_path, frames_dir, start, duration, clip_segments, window)
        encode_frames_to_video(frames_dir, temp_video_path, render_stats["fps"])
        extract_audio_segment(video_path, temp_audio_path, start, duration)
        mux_video_with_audio(temp_video_path, temp_audio_path, output_path)
        return render_stats


def format_filename_time(seconds):
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes:02d}-{secs:05.2f}".replace(".", "_")


def parse_args():
    parser = argparse.ArgumentParser(description="Cut raw short clips using face-aware framing.")
    parser.add_argument("--video", default=None, help="Path to source video")
    parser.add_argument("--windows", default="top_windows.json", help="JSON file with start/end windows")
    parser.add_argument("--transcript", default="transcripts/final_transcript.json", help="Transcript JSON for boundary protection")
    parser.add_argument("--output-dir", default="cuts/raw", help="Output directory for raw cuts")
    parser.add_argument("--cutting-log", default="metadata/cutting_logic.json", help="Log file for Smart Context Cutter decisions")
    return parser.parse_args()


def main():
    args = parse_args()
    video_path = find_input_video(args.video) if args.video else find_input_video("input")
    windows = load_windows(args.windows)
    transcript = load_transcript(args.transcript)
    cutting_log = load_cutting_log(args.cutting_log)

    for idx, window in enumerate(windows, start=1):
        start = float(window["start"])
        end = float(window["end"])
        start, end, decisions, word_source = enforce_no_mid_word(start, end, transcript)
        duration = end - start
        clip_segments = collect_clip_segments(transcript, start, end)

        output_path = Path(args.output_dir) / f"segment_{idx}_{format_filename_time(start)}_{format_filename_time(end)}.mp4"
        framing_mode = "face_tracking"
        try:
            print(f"Cutting segment {idx}: {start:.2f}s - {end:.2f}s -> {output_path}")
            render_stats = cut_segment(video_path, output_path, start, duration, clip_segments, window)
        except Exception as exc:
            framing_mode = "center_fallback"
            render_stats = {"error": str(exc)}
            print(f"  Warning: face tracking failed for segment {idx}, falling back to center crop. Reason: {exc}")
            center_crop_ffmpeg(video_path, output_path, start, duration)

        upsert_cutter_adjustment(
            cutting_log,
            {
                "segment_index": idx,
                "source_window": {
                    "start": window.get("start"),
                    "end": window.get("end"),
                    "heatmap_start": window.get("heatmap_start"),
                    "heatmap_end": window.get("heatmap_end"),
                    "summary": window.get("summary"),
                    "ai_reason": window.get("ai_reason"),
                    "hook_reason": window.get("hook_reason"),
                    "ending_reason": window.get("ending_reason"),
                    "avg_value": window.get("avg_value"),
                },
                "final_start": start,
                "final_end": end,
                "final_duration": duration,
                "word_boundary_source": word_source,
                "decisions": decisions,
                "framing_mode": framing_mode,
                "face_tracking": render_stats,
                "clip_signals": {
                    "contains_high_importance": any(int(segment.get("importance", 3)) >= 5 for segment in clip_segments),
                    "speakers": sorted({segment.get("speaker", "Speaker 0") for segment in clip_segments}),
                },
            },
        )

    save_cutting_log(args.cutting_log, cutting_log)
    print(f"Done. Files saved in {Path(args.output_dir).resolve()}")


if __name__ == "__main__":
    main()
