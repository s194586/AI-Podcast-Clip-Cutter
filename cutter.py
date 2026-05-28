import argparse
import json
import re
import subprocess
import tempfile
from collections import deque
from pathlib import Path

import cv2
import mediapipe as mp
from layout import VALID_LAYOUT_MODES, get_layout_profile, is_vertical_9_16, normalize_layout_mode

MAX_SHORT_DURATION = 90.0
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


def full_frame_blur_background_ffmpeg(video_path, output_path, start, duration):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filter_graph = (
        f"[0:v]scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={OUTPUT_WIDTH}:{OUTPUT_HEIGHT},boxblur=20:8[bg];"
        f"[0:v]scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(video_path),
        "-filter_complex",
        filter_graph,
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "superfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)


def inspect_video_stream(video_path):
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        frame_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        frame_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        capture.release()
    if frame_width <= 0 or frame_height <= 0:
        raise RuntimeError("Could not determine source video dimensions.")
    return fps, frame_width, frame_height


def probe_output_video(path):
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout or "{}")
    streams = payload.get("streams") or []
    if not streams:
        raise RuntimeError(f"Could not inspect output video stream: {path}")
    stream = streams[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    vertical_9_16 = is_vertical_9_16(width, height)
    return {
        "output_width": width,
        "output_height": height,
        "output_aspect_ratio": "9:16" if vertical_9_16 else (f"{width}:{height}" if width and height else None),
        "is_vertical_9_16": vertical_9_16,
    }


def resolve_render_plan(cutting_log, requested_layout_mode="auto"):
    content_routing = cutting_log.get("content_routing", {}) if isinstance(cutting_log, dict) else {}
    strategy = content_routing.get("strategy", {}) if isinstance(content_routing, dict) else {}
    strategy_layout = strategy.get("layout") or {}
    content_type = (
        content_routing.get("content_type")
        or strategy.get("content_type")
        or strategy_layout.get("content_type")
        or "podcast"
    )
    effective_layout_mode = normalize_layout_mode(
        requested_layout_mode
        if normalize_layout_mode(requested_layout_mode) != "auto"
        else content_routing.get("requested_layout_mode") or strategy_layout.get("layout_mode") or "auto"
    )
    layout_profile = get_layout_profile(content_type, effective_layout_mode)
    render_hints = {
        **dict(strategy.get("render_hints") or {}),
        **layout_profile.to_render_hints(),
    }
    render_hints["content_type"] = content_type
    render_hints["requested_layout_mode"] = normalize_layout_mode(requested_layout_mode)
    render_hints["layout_mode"] = layout_profile.layout_mode
    render_hints["crop_mode"] = (
        str(render_hints.get("crop_mode") or layout_profile.layout_mode).strip() or layout_profile.layout_mode
    )
    render_hints["crop_priority"] = (
        str(render_hints.get("crop_priority") or layout_profile.crop_priority).strip() or layout_profile.crop_priority
    )
    render_hints["face_tracking_allowed"] = bool(layout_profile.allow_face_tracking)
    render_hints["allow_face_tracking"] = bool(layout_profile.allow_face_tracking)
    render_hints["face_tracking_weight"] = float(layout_profile.face_tracking_weight)
    render_hints["preserve_full_frame"] = bool(layout_profile.preserve_full_frame)
    render_hints["blur_background"] = bool(layout_profile.blur_background)
    render_hints["safe_center_crop"] = bool(layout_profile.safe_center_crop)
    render_hints["output_width"] = int(layout_profile.output_width)
    render_hints["output_height"] = int(layout_profile.output_height)
    render_hints["output_aspect_ratio"] = str(layout_profile.output_aspect_ratio)
    render_hints["max_crop_motion"] = float(layout_profile.max_crop_motion)
    render_hints["smoothing_strength"] = float(layout_profile.smoothing_strength)
    render_hints["min_face_area_for_tracking"] = float(layout_profile.min_face_area_for_tracking)
    render_hints["ignore_edge_faces"] = bool(layout_profile.ignore_edge_faces)
    return render_hints


def build_static_render_stats(video_path, start, duration, *, render_hints, tracking_mode, output_path=None, fallback_reason=""):
    fps, frame_width, frame_height = inspect_video_stream(video_path)
    total_frames = max(1, int(round(duration * fps)))
    full_frame_preserved = bool(render_hints.get("preserve_full_frame")) and tracking_mode == "full_frame_blur_background"
    stats = {
        "fps": fps,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "frames_rendered": total_frames,
        "sampled_detections": 0,
        "fallback_samples": 0,
        "reaction_samples": 0,
        "zoom_samples": 0,
        "ignored_faces_count": 0,
        "sample_stride": FACE_SAMPLE_STRIDE,
        "smoothing_window": SMOOTHING_WINDOW,
        "layout_mode": render_hints.get("layout_mode"),
        "layout_policy": render_hints.get("layout_policy"),
        "layout_mode_used": tracking_mode if tracking_mode in {"full_frame_blur_background", "safe_center_crop", "center_fallback"} else render_hints.get("layout_mode"),
        "crop_mode": render_hints.get("crop_mode"),
        "crop_priority": render_hints.get("crop_priority"),
        "face_tracking_allowed": bool(render_hints.get("allow_face_tracking")),
        "face_tracking_used": False,
        "preserve_full_frame": bool(render_hints.get("preserve_full_frame")),
        "full_frame_preserved": full_frame_preserved,
        "blur_background": bool(render_hints.get("blur_background")),
        "safe_center_crop": bool(render_hints.get("safe_center_crop")),
        "tracking_mode": tracking_mode,
        "crop_stabilized": True,
        "fallback_reason": str(fallback_reason or ""),
        "center_x_mean_norm": 0.5,
        "center_y_mean_norm": 0.5,
        "center_x_std_norm": 0.0,
        "center_y_std_norm": 0.0,
        "tracked_frame_ratio": 0.0,
        "output_width": int(render_hints.get("output_width") or OUTPUT_WIDTH),
        "output_height": int(render_hints.get("output_height") or OUTPUT_HEIGHT),
        "output_aspect_ratio": str(render_hints.get("output_aspect_ratio") or "9:16"),
        "is_vertical_9_16": True,
    }
    if output_path and Path(output_path).exists():
        try:
            stats.update(probe_output_video(Path(output_path)))
        except Exception as exc:
            stats["output_probe_error"] = str(exc)
    return stats


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


def filter_faces_for_render_mode(faces, frame_width, frame_height, render_hints):
    if not faces:
        return [], 0

    crop_priority = str(render_hints.get("crop_priority") or "").lower()
    crop_mode = str(render_hints.get("crop_mode") or "").lower()
    ignore_edge_faces = bool(render_hints.get("ignore_edge_faces"))
    min_face_area = float(render_hints.get("min_face_area_for_tracking") or 0.0)

    filtered = []
    ignored_faces = 0
    for face in faces:
        x_ratio = face["center_x"] / max(frame_width, 1)
        y_ratio = face["center_y"] / max(frame_height, 1)
        area_ratio = face["area_ratio"]
        if area_ratio < min_face_area:
            ignored_faces += 1
            continue

        near_horizontal_edge = x_ratio <= 0.18 or x_ratio >= 0.82
        near_vertical_edge = y_ratio <= 0.18 or y_ratio >= 0.82
        likely_facecam_overlay = (
            (crop_priority == "gameplay" or crop_mode == "gameplay_balanced")
            and area_ratio <= 0.12
            and near_horizontal_edge
            and near_vertical_edge
        )
        if likely_facecam_overlay or (ignore_edge_faces and near_horizontal_edge and near_vertical_edge):
            ignored_faces += 1
            continue
        filtered.append(face)

    return filtered, ignored_faces


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


def choose_face(faces, frame_width, frame_height, previous_center_x, reaction_mode, render_hints):
    if not faces:
        return None, 0

    faces, ignored_faces = filter_faces_for_render_mode(faces, frame_width, frame_height, render_hints)
    if not faces:
        return None, ignored_faces

    best_face = None
    best_score = None
    frame_center_x = frame_width / 2.0
    face_tracking_weight = float(render_hints.get("face_tracking_weight") or 0.0)
    crop_priority = str(render_hints.get("crop_priority") or "").lower()

    for face in faces:
        area_score = face["area_ratio"] * (10.0 if crop_priority == "speaker_face" else 5.0)
        continuity_score = 0.0
        if previous_center_x is not None:
            continuity_score = max(0.0, 1.5 - abs(face["center_x"] - previous_center_x) / max(frame_width, 1))
        center_bias = max(0.0, 1.0 - abs(face["center_x"] - frame_center_x) / max(frame_width, 1))
        expression_boost = face["expression_score"] * (12.0 if reaction_mode else 2.0)
        vertical_bias = max(0.0, 1.0 - abs(face["center_y"] - (frame_height * 0.45)) / max(frame_height, 1))
        weighted_center_bias = center_bias * max(0.2, 1.0 - face_tracking_weight)
        weighted_expression = expression_boost * max(0.3, face_tracking_weight)
        score = area_score + continuity_score + weighted_center_bias + weighted_expression + vertical_bias
        if best_score is None or score > best_score:
            best_score = score
            best_face = face

    return best_face, ignored_faces


def update_tracking_state(current_state, target_face, frame_width, frame_height, active_segment, reaction_mode, render_hints):
    if target_face is None:
        return dict(current_state)

    desired_zoom = determine_zoom(active_segment, reaction_mode)
    smoothing_strength = clamp(float(render_hints.get("smoothing_strength") or 0.0), 0.0, 1.0)
    max_crop_motion = max(0.0, float(render_hints.get("max_crop_motion") or 0.0))
    face_tracking_weight = clamp(float(render_hints.get("face_tracking_weight") or 0.0), 0.0, 1.0)
    responsiveness = clamp((1.0 - (0.5 * smoothing_strength)) * max(0.35, face_tracking_weight or 0.35), 0.25, 1.0)
    max_step_x = frame_width * max_crop_motion if max_crop_motion > 0 else frame_width
    max_step_y = frame_height * max_crop_motion if max_crop_motion > 0 else frame_height

    delta_x = clamp(target_face["center_x"] - current_state["center_x"], -max_step_x, max_step_x)
    delta_y = clamp(target_face["center_y"] - current_state["center_y"], -max_step_y, max_step_y)
    zoom_delta = desired_zoom - float(current_state.get("zoom", 1.0))

    return {
        "center_x": current_state["center_x"] + (delta_x * responsiveness),
        "center_y": current_state["center_y"] + (delta_y * responsiveness),
        "zoom": float(current_state.get("zoom", 1.0)) + (zoom_delta * max(0.25, responsiveness)),
    }


def smooth_state(history):
    if not history:
        return None
    return {
        "center_x": sum(item["center_x"] for item in history) / len(history),
        "center_y": sum(item["center_y"] for item in history) / len(history),
        "zoom": sum(item["zoom"] for item in history) / len(history),
    }


def _std_dev(values):
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return variance ** 0.5


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


def render_dynamic_segment(video_path, frames_dir, start, duration, clip_segments, window, render_hints):
    if not render_hints.get("allow_face_tracking"):
        raise RuntimeError("Face tracking disabled for this layout mode.")

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
    ignored_faces_count = 0
    center_x_samples = []
    center_y_samples = []

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
                target_face, ignored_faces = choose_face(
                    faces,
                    frame_width,
                    frame_height,
                    previous_center_x,
                    reaction_mode,
                    render_hints,
                )
                ignored_faces_count += ignored_faces
                if target_face:
                    current_state = update_tracking_state(
                        current_state,
                        target_face,
                        frame_width,
                        frame_height,
                        active_segment,
                        reaction_mode,
                        render_hints,
                    )
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
            center_x_samples.append(smoothed["center_x"] / max(frame_width, 1))
            center_y_samples.append(smoothed["center_y"] / max(frame_height, 1))
            framed = crop_and_resize(frame, smoothed)
            frame_path = frames_dir / f"frame_{frame_index + 1:06d}.jpg"
            cv2.imwrite(str(frame_path), framed, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    finally:
        capture.release()
        analyzer.close()
        del capture

    face_tracking_used = detected_frames > 0
    fallback_reason = ""
    layout_mode_used = str(render_hints.get("layout_mode") or "")
    if not face_tracking_used:
        fallback_reason = "no_faces_detected_center_crop"
        layout_mode_used = "safe_center_crop"

    return {
        "fps": fps,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "frames_rendered": total_frames,
        "sampled_detections": detected_frames,
        "fallback_samples": fallback_frames,
        "reaction_samples": reaction_frames,
        "zoom_samples": zoom_frames,
        "ignored_faces_count": ignored_faces_count,
        "sample_stride": FACE_SAMPLE_STRIDE,
        "smoothing_window": SMOOTHING_WINDOW,
        "layout_mode": render_hints.get("layout_mode"),
        "layout_policy": render_hints.get("layout_policy"),
        "layout_mode_used": layout_mode_used,
        "crop_mode": render_hints.get("crop_mode"),
        "crop_priority": render_hints.get("crop_priority"),
        "face_tracking_allowed": bool(render_hints.get("allow_face_tracking")),
        "face_tracking_used": face_tracking_used,
        "preserve_full_frame": bool(render_hints.get("preserve_full_frame")),
        "full_frame_preserved": False,
        "blur_background": bool(render_hints.get("blur_background")),
        "safe_center_crop": bool(render_hints.get("safe_center_crop")),
        "tracking_mode": "dynamic_face_tracking" if face_tracking_used else "center_fallback_no_faces",
        "crop_stabilized": bool(SMOOTHING_WINDOW > 1 and float(render_hints.get("smoothing_strength") or 0.0) > 0.0),
        "fallback_reason": fallback_reason,
        "center_x_mean_norm": round(sum(center_x_samples) / len(center_x_samples), 4) if center_x_samples else 0.5,
        "center_y_mean_norm": round(sum(center_y_samples) / len(center_y_samples), 4) if center_y_samples else 0.5,
        "center_x_std_norm": round(_std_dev(center_x_samples), 4),
        "center_y_std_norm": round(_std_dev(center_y_samples), 4),
        "tracked_frame_ratio": round(detected_frames / max(1, detected_frames + fallback_frames), 4),
        "output_width": int(render_hints.get("output_width") or OUTPUT_WIDTH),
        "output_height": int(render_hints.get("output_height") or OUTPUT_HEIGHT),
        "output_aspect_ratio": str(render_hints.get("output_aspect_ratio") or "9:16"),
        "is_vertical_9_16": True,
    }


def cut_segment(video_path, output_path, start, duration, clip_segments, window, render_hints):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="face_track_") as temp_dir:
        temp_dir_path = Path(temp_dir)
        frames_dir = temp_dir_path / "frames"
        temp_video_path = temp_dir_path / f"{output_path.stem}_silent.avi"
        temp_audio_path = temp_dir_path / f"{output_path.stem}_audio.m4a"

        render_stats = render_dynamic_segment(video_path, frames_dir, start, duration, clip_segments, window, render_hints)
        encode_frames_to_video(frames_dir, temp_video_path, render_stats["fps"])
        extract_audio_segment(video_path, temp_audio_path, start, duration)
        mux_video_with_audio(temp_video_path, temp_audio_path, output_path)
        try:
            render_stats.update(probe_output_video(output_path))
        except Exception as exc:
            render_stats["output_probe_error"] = str(exc)
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
    parser.add_argument("--max-duration", type=float, default=MAX_SHORT_DURATION, help="Maximum rendered clip duration in seconds")
    parser.add_argument(
        "--layout-mode",
        default="auto",
        choices=VALID_LAYOUT_MODES,
        help="Layout override for final 9:16 rendering.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    video_path = find_input_video(args.video) if args.video else find_input_video("input")
    windows = load_windows(args.windows)
    transcript = load_transcript(args.transcript)
    cutting_log = load_cutting_log(args.cutting_log)
    render_hints = resolve_render_plan(cutting_log, args.layout_mode)

    for idx, window in enumerate(windows, start=1):
        start = float(window["start"])
        end = float(window["end"])
        start, end, decisions, word_source = enforce_no_mid_word(start, end, transcript, max_duration=args.max_duration)
        duration = end - start
        clip_segments = collect_clip_segments(transcript, start, end)

        output_path = Path(args.output_dir) / f"segment_{idx}_{format_filename_time(start)}_{format_filename_time(end)}.mp4"
        try:
            print(f"Cutting segment {idx}: {start:.2f}s - {end:.2f}s -> {output_path}")
            layout_mode = str(render_hints.get("layout_mode") or "safe_center_crop")
            if layout_mode == "full_frame_blur_background":
                framing_mode = "full_frame_blur_background"
                full_frame_blur_background_ffmpeg(video_path, output_path, start, duration)
                render_stats = build_static_render_stats(
                    video_path,
                    start,
                    duration,
                    render_hints=render_hints,
                    tracking_mode="full_frame_blur_background",
                    output_path=output_path,
                )
            elif not render_hints.get("allow_face_tracking") or layout_mode in {"safe_center_crop", "vertical_crop"}:
                framing_mode = "safe_center_crop"
                center_crop_ffmpeg(video_path, output_path, start, duration)
                render_stats = build_static_render_stats(
                    video_path,
                    start,
                    duration,
                    render_hints=render_hints,
                    tracking_mode="safe_center_crop",
                    output_path=output_path,
                )
            else:
                framing_mode = "face_tracking"
                render_stats = cut_segment(video_path, output_path, start, duration, clip_segments, window, render_hints)
        except Exception as exc:
            framing_mode = "center_fallback"
            render_stats = build_static_render_stats(
                video_path,
                start,
                duration,
                render_hints=render_hints,
                tracking_mode="center_fallback",
                output_path=output_path if output_path.exists() else None,
                fallback_reason=str(exc),
            )
            render_stats["error"] = str(exc)
            print(f"  Warning: face tracking failed for segment {idx}, falling back to center crop. Reason: {exc}")
            center_crop_ffmpeg(video_path, output_path, start, duration)
            try:
                render_stats.update(probe_output_video(output_path))
            except Exception as probe_exc:
                render_stats["output_probe_error"] = str(probe_exc)

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
                "layout_mode": render_hints.get("layout_mode"),
                "layout_policy": render_hints.get("layout_policy"),
                "render_hints": render_hints,
                "face_tracking": render_stats,
                "boundary_metadata": window.get("boundary_metadata") or {
                    "original_start": round(float(window.get("heatmap_start", window.get("start", start))), 4),
                    "original_end": round(float(window.get("heatmap_end", window.get("end", end))), 4),
                    "refined_start": round(float(start), 4),
                    "refined_end": round(float(end), 4),
                    "boundary_adjustment_reason": decisions,
                    "sentence_boundary_used": bool(window.get("boundary_metadata", {}).get("sentence_boundary_used", False)),
                    "speaker_turn_boundary_used": bool(window.get("boundary_metadata", {}).get("speaker_turn_boundary_used", False)),
                },
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
