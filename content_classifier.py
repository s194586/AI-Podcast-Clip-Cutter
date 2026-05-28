from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import statistics
from typing import Any
import unicodedata


VALID_CONTENT_TYPES = ("podcast",)
VALID_CONTENT_TYPE_MODES = ("auto",) + VALID_CONTENT_TYPES
PODCAST_ONLY_MVP_REASON = (
    "Podcast-only MVP: active routing is locked to podcast/talking-head material."
)

WORD_RE = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)

GAMEPLAY_TOKENS = {
    "ace",
    "aim",
    "banan",
    "bomba",
    "bombsite",
    "clutch",
    "eco",
    "enemy",
    "flash",
    "flasha",
    "frag",
    "gala",
    "gameplay",
    "granat",
    "headshot",
    "hud",
    "kill",
    "kibel",
    "long",
    "mid",
    "monster",
    "peek",
    "phoenix",
    "push",
    "reload",
    "round",
    "runda",
    "rush",
    "scout",
    "short",
    "site",
    "smoke",
    "sniper",
    "spray",
    "team",
}

TUTORIAL_TOKENS = {
    "ekran",
    "instalacja",
    "kanwa",
    "kliknij",
    "klikamy",
    "krok",
    "menu",
    "nastepnie",
    "następnie",
    "opcja",
    "otworz",
    "opcje",
    "panel",
    "pokaze",
    "pokażę",
    "pokazuje",
    "poradnik",
    "prezentacja",
    "prezentacje",
    "przycisk",
    "projekt",
    "potem",
    "szablon",
    "szablony",
    "tutorial",
    "tutorialu",
    "ustaw",
    "ustawienia",
    "utworz",
    "wciśnij",
    "wybierz",
    "wejdz",
    "wpisz",
    "wyswietl",
    "zaloguj",
    "zaloz",
    "zarejestruj",
    "zaznacz",
    "zobacz",
    "zrob",
}

PODCAST_TOKENS = {
    "dialog",
    "gosc",
    "historia",
    "opowiesc",
    "myślę",
    "opowieść",
    "odcinek",
    "podcast",
    "powiedzial",
    "powiedział",
    "rozmowa",
    "rozmowy",
    "temat",
    "wspomnienie",
    "wspomnienia",
}

COMMENTARY_TOKENS = {
    "analiza",
    "armia",
    "atak",
    "front",
    "gospodarka",
    "kanal",
    "komentarz",
    "minister",
    "panstwo",
    "parada",
    "polityka",
    "prezydent",
    "premier",
    "putin",
    "raport",
    "rosja",
    "rosyjski",
    "rzad",
    "ukraina",
    "ukrainski",
    "wiadomosci",
    "wojna",
    "wojsko",
    "wybory",
}

DIRECT_ADDRESS_TOKENS = {
    "ci",
    "ciebie",
    "kliknij",
    "pokaze",
    "tobie",
    "twoich",
    "twoja",
    "twoje",
    "twoj",
    "ty",
    "wybierz",
    "zaloguj",
    "zapraszam",
}

INSTRUCTION_TOKENS = TUTORIAL_TOKENS | {
    "kliknij",
    "klikamy",
    "krok",
    "mozemy",
    "nastepnie",
    "otworz",
    "pokaze",
    "przycisk",
    "teraz",
    "utworz",
    "wejdz",
    "wpisz",
    "wybierz",
    "zaloguj",
    "zrob",
}

PODCAST_QUESTION_PREFIXES = (
    "a co ",
    "a ty ",
    "co ",
    "czy ",
    "dlaczego ",
    "jak ",
    "kiedy ",
    "kto ",
    "no ale ",
    "o co ",
    "po co ",
    "to co ",
)


@dataclass
class ContentClassificationResult:
    content_type: str
    confidence: float
    reasons: list[str]
    features: dict[str, Any]
    scores: dict[str, float]
    source: str
    strategy_name: str
    forced_content_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "content_type": self.content_type,
            "confidence": round(float(self.confidence), 4),
            "reasons": list(self.reasons),
            "features": self.features,
            "scores": {key: round(float(value), 4) for key, value in self.scores.items()},
            "source": self.source,
            "strategy_name": self.strategy_name,
        }
        if self.forced_content_type:
            payload["forced_content_type"] = self.forced_content_type
        return payload


def normalize_content_type_mode(value: str | None, default: str = "auto") -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in VALID_CONTENT_TYPE_MODES:
        raise ValueError(
            f"Unsupported content type for the podcast-only MVP: {value}. "
            f"Expected one of: {', '.join(VALID_CONTENT_TYPE_MODES)}"
        )
    return normalized


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def parse_time(value: str | float | int) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    parts = [float(part) for part in str(value).strip().replace(",", ".").split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


def normalize_speaker_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "Speaker 0"
    match = re.search(r"(\d+)", text)
    if match:
        return f"Speaker {int(match.group(1))}"
    return "Speaker 0"


def canonicalize_text(text: str) -> str:
    lowered = str(text or "").lower()
    normalized = unicodedata.normalize("NFKD", lowered)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def tokenize(text: str) -> list[str]:
    return [canonicalize_text(token) for token in WORD_RE.findall(str(text or "").lower())]


def load_transcript(path_or_data: str | Path | list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(path_or_data, list):
        data = path_or_data
    elif isinstance(path_or_data, dict):
        data = path_or_data.get("segments", path_or_data)
    else:
        with open(path_or_data, "r", encoding="utf-8") as file_handle:
            raw = json.load(file_handle)
        data = raw.get("segments", raw) if isinstance(raw, dict) else raw

    if not isinstance(data, list):
        return []

    segments: list[dict[str, Any]] = []
    for item in data:
        try:
            start = parse_time(item["start"])
            end = parse_time(item["end"])
        except Exception:
            continue
        if end <= start:
            continue
        text = " ".join(str(item.get("text", "")).split()).strip()
        segments.append(
            {
                "start": start,
                "end": end,
                "duration": end - start,
                "text": text,
                "speaker": normalize_speaker_label(
                    item.get("speaker") or item.get("speaker_id") or item.get("speakerId")
                ),
                "importance": int(item.get("importance", 3) or 3),
                "chaos": bool(item.get("chaos", False)),
            }
        )
    return sorted(segments, key=lambda item: item["start"])


def load_heatmap(path_or_data: str | Path | list[dict[str, Any]]) -> list[dict[str, float]]:
    if isinstance(path_or_data, list):
        data = path_or_data
    else:
        with open(path_or_data, "r", encoding="utf-8") as file_handle:
            data = json.load(file_handle)
    if not isinstance(data, list):
        return []
    cleaned: list[dict[str, float]] = []
    for item in data:
        try:
            cleaned.append(
                {
                    "start_time": float(item.get("start_time", 0.0)),
                    "end_time": float(item.get("end_time", item.get("start_time", 0.0))),
                    "value": float(item.get("value", 0.0)),
                }
            )
        except Exception:
            continue
    return cleaned


def extract_transcript_features(transcript_segments: list[dict[str, Any]]) -> dict[str, Any]:
    if not transcript_segments:
        return {
            "segment_count": 0,
            "speaker_count": 0,
            "speech_coverage_ratio": 0.0,
            "avg_segment_duration": 0.0,
            "median_segment_duration": 0.0,
            "short_segment_ratio": 0.0,
            "long_segment_ratio": 0.0,
            "avg_words_per_second": 0.0,
            "avg_words_per_segment": 0.0,
            "speaker_switch_rate_per_minute": 0.0,
            "speaker_switch_ratio": 0.0,
            "dominant_speaker_ratio": 0.0,
            "chaos_ratio": 0.0,
            "high_importance_ratio": 0.0,
            "emotion_segment_ratio": 0.0,
            "question_ratio": 0.0,
            "exclamation_ratio": 0.0,
            "gameplay_keyword_ratio": 0.0,
            "tutorial_keyword_ratio": 0.0,
            "podcast_keyword_ratio": 0.0,
            "commentary_keyword_ratio": 0.0,
            "instruction_segment_ratio": 0.0,
            "direct_address_ratio": 0.0,
            "commentary_segment_ratio": 0.0,
            "qa_turn_ratio": 0.0,
            "speaker_distribution": {},
        }

    durations = [segment["duration"] for segment in transcript_segments]
    total_span = max(0.01, transcript_segments[-1]["end"] - transcript_segments[0]["start"])
    speech_seconds = sum(durations)
    total_words = 0
    gameplay_hits = 0
    tutorial_hits = 0
    podcast_hits = 0
    commentary_hits = 0
    question_count = 0
    exclamation_count = 0
    emotion_segment_count = 0
    instruction_segment_count = 0
    direct_address_segment_count = 0
    commentary_segment_count = 0
    question_turn_count = 0
    speaker_sequence: list[str] = []
    segment_tokens: list[list[str]] = []
    normalized_segments: list[str] = []

    for segment in transcript_segments:
        text = segment["text"]
        normalized_text = canonicalize_text(text)
        tokens = tokenize(text)
        token_set = set(tokens)
        total_words += len(tokens)
        gameplay_hits += sum(1 for token in tokens if token in GAMEPLAY_TOKENS)
        tutorial_hits += sum(1 for token in tokens if token in TUTORIAL_TOKENS)
        podcast_hits += sum(1 for token in tokens if token in PODCAST_TOKENS)
        commentary_hits += sum(1 for token in tokens if token in COMMENTARY_TOKENS)
        if "?" in text:
            question_count += 1
        if "!" in text:
            exclamation_count += 1
        if int(segment.get("importance", 3)) >= 4:
            emotion_segment_count += 1
        if token_set.intersection(INSTRUCTION_TOKENS):
            instruction_segment_count += 1
        if token_set.intersection(DIRECT_ADDRESS_TOKENS):
            direct_address_segment_count += 1
        if token_set.intersection(COMMENTARY_TOKENS):
            commentary_segment_count += 1
        if text:
            speaker_sequence.append(segment["speaker"])
        segment_tokens.append(tokens)
        normalized_segments.append(normalized_text)

    for index, segment in enumerate(transcript_segments[:-1]):
        text = segment["text"]
        normalized_text = normalized_segments[index]
        if "?" not in text:
            continue
        if not any(normalized_text.startswith(prefix) for prefix in PODCAST_QUESTION_PREFIXES):
            continue
        next_tokens = segment_tokens[index + 1]
        next_text = transcript_segments[index + 1]["text"]
        if "?" in next_text:
            continue
        if 1 <= len(next_tokens) <= 18:
            question_turn_count += 1

    speaker_counts = Counter(speaker_sequence)
    speaker_switches = sum(1 for left, right in zip(speaker_sequence, speaker_sequence[1:]) if left != right)
    dominant_speaker_ratio = (
        speaker_counts.most_common(1)[0][1] / max(len(speaker_sequence), 1) if speaker_counts else 0.0
    )

    return {
        "segment_count": len(transcript_segments),
        "speaker_count": len(speaker_counts),
        "speech_coverage_ratio": round(clamp(speech_seconds / total_span), 4),
        "avg_segment_duration": round(statistics.fmean(durations), 4),
        "median_segment_duration": round(statistics.median(durations), 4),
        "short_segment_ratio": round(sum(1 for duration in durations if duration <= 1.25) / len(durations), 4),
        "long_segment_ratio": round(sum(1 for duration in durations if duration >= 3.5) / len(durations), 4),
        "avg_words_per_second": round(total_words / max(speech_seconds, 0.01), 4),
        "avg_words_per_segment": round(total_words / max(len(transcript_segments), 1), 4),
        "speaker_switch_rate_per_minute": round(speaker_switches / max(total_span / 60.0, 0.01), 4),
        "speaker_switch_ratio": round(speaker_switches / max(len(speaker_sequence) - 1, 1), 4),
        "dominant_speaker_ratio": round(dominant_speaker_ratio, 4),
        "chaos_ratio": round(
            sum(1 for segment in transcript_segments if segment.get("chaos")) / len(transcript_segments),
            4,
        ),
        "high_importance_ratio": round(
            sum(1 for segment in transcript_segments if int(segment.get("importance", 3)) >= 5)
            / len(transcript_segments),
            4,
        ),
        "emotion_segment_ratio": round(emotion_segment_count / len(transcript_segments), 4),
        "question_ratio": round(question_count / len(transcript_segments), 4),
        "exclamation_ratio": round(exclamation_count / len(transcript_segments), 4),
        "gameplay_keyword_ratio": round(gameplay_hits / max(total_words, 1), 4),
        "tutorial_keyword_ratio": round(tutorial_hits / max(total_words, 1), 4),
        "podcast_keyword_ratio": round(podcast_hits / max(total_words, 1), 4),
        "commentary_keyword_ratio": round(commentary_hits / max(total_words, 1), 4),
        "instruction_segment_ratio": round(instruction_segment_count / len(transcript_segments), 4),
        "direct_address_ratio": round(direct_address_segment_count / len(transcript_segments), 4),
        "commentary_segment_ratio": round(commentary_segment_count / len(transcript_segments), 4),
        "qa_turn_ratio": round(question_turn_count / len(transcript_segments), 4),
        "speaker_distribution": dict(sorted(speaker_counts.items())),
    }


def extract_heatmap_features(heatmap: list[dict[str, float]]) -> dict[str, Any]:
    if not heatmap:
        return {
            "heatmap_mean": 0.0,
            "heatmap_peak": 0.0,
            "heatmap_std": 0.0,
            "heatmap_volatility": 0.0,
            "heatmap_high_energy_ratio": 0.0,
            "heatmap_p90": 0.0,
        }

    values = [float(item["value"]) for item in heatmap]
    diffs = [abs(right - left) for left, right in zip(values, values[1:])]
    return {
        "heatmap_mean": round(statistics.fmean(values), 4),
        "heatmap_peak": round(max(values), 4),
        "heatmap_std": round(statistics.pstdev(values) if len(values) > 1 else 0.0, 4),
        "heatmap_volatility": round(statistics.fmean(diffs) if diffs else 0.0, 4),
        "heatmap_high_energy_ratio": round(sum(1 for value in values if value >= 0.65) / len(values), 4),
        "heatmap_p90": round(_percentile(values, 0.9), 4),
    }


def extract_video_features(
    video_path: str | Path | None,
    *,
    max_samples: int = 36,
    max_face_samples: int = 16,
) -> dict[str, Any]:
    if not video_path:
        return {
            "video_analysis_status": "skipped",
            "video_duration_seconds": 0.0,
            "frame_samples": 0,
            "motion_score": 0.0,
            "scene_change_rate": 0.0,
            "face_presence_ratio": 0.0,
            "avg_faces_per_frame": 0.0,
            "avg_face_area_ratio": 0.0,
            "face_stability": 0.0,
            "face_overlay_ratio": 0.0,
            "face_large_ratio": 0.0,
        }

    try:
        import cv2
    except Exception as exc:
        return {
            "video_analysis_status": "error_cv2",
            "video_analysis_error": str(exc),
            "video_duration_seconds": 0.0,
            "frame_samples": 0,
            "motion_score": 0.0,
            "scene_change_rate": 0.0,
            "face_presence_ratio": 0.0,
            "avg_faces_per_frame": 0.0,
            "avg_face_area_ratio": 0.0,
            "face_stability": 0.0,
            "face_overlay_ratio": 0.0,
            "face_large_ratio": 0.0,
        }

    path = Path(video_path)
    if not path.exists():
        return {
            "video_analysis_status": "missing",
            "video_duration_seconds": 0.0,
            "frame_samples": 0,
            "motion_score": 0.0,
            "scene_change_rate": 0.0,
            "face_presence_ratio": 0.0,
            "avg_faces_per_frame": 0.0,
            "avg_face_area_ratio": 0.0,
            "face_stability": 0.0,
            "face_overlay_ratio": 0.0,
            "face_large_ratio": 0.0,
        }

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return {
            "video_analysis_status": "error_open",
            "video_duration_seconds": 0.0,
            "frame_samples": 0,
            "motion_score": 0.0,
            "scene_change_rate": 0.0,
            "face_presence_ratio": 0.0,
            "avg_faces_per_frame": 0.0,
            "avg_face_area_ratio": 0.0,
            "face_stability": 0.0,
            "face_overlay_ratio": 0.0,
            "face_large_ratio": 0.0,
        }

    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
    duration = float(frame_count / max(fps, 1.0)) if frame_count else 0.0
    if duration <= 0:
        duration = 0.0

    sample_count = max(1, min(max_samples, int(duration / 2.5) + 1 if duration else 12))
    sample_times = [duration * index / max(sample_count - 1, 1) for index in range(sample_count)] if duration else [0.0]

    grayscale_samples: list[Any] = []
    face_frames: list[Any] = []
    face_stride = max(1, math.ceil(sample_count / max(max_face_samples, 1)))

    for index, sample_time in enumerate(sample_times):
        capture.set(cv2.CAP_PROP_POS_MSEC, sample_time * 1000.0)
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (96, 54), interpolation=cv2.INTER_AREA)
        grayscale_samples.append(gray)
        if index % face_stride == 0:
            face_frames.append((sample_time, frame))
    capture.release()

    motion_values: list[float] = []
    for left, right in zip(grayscale_samples, grayscale_samples[1:]):
        diff = float((abs(right.astype("float32") - left.astype("float32")).mean()) / 255.0)
        motion_values.append(diff)

    scene_change_rate = (
        sum(1 for value in motion_values if value >= 0.16) / max(len(motion_values), 1) if motion_values else 0.0
    )
    motion_score = clamp((statistics.fmean(motion_values) if motion_values else 0.0) / 0.2)

    face_presence_ratio = 0.0
    avg_faces_per_frame = 0.0
    avg_face_area_ratio = 0.0
    face_stability = 0.0
    face_overlay_ratio = 0.0
    face_large_ratio = 0.0
    face_status = "skipped"
    face_error = None

    if face_frames:
        try:
            from cutter import FaceAnalyzer

            analyzer = FaceAnalyzer()
            face_counts: list[int] = []
            primary_faces: list[tuple[float, float, float]] = []
            for sample_time, frame in face_frames:
                faces = analyzer.detect(frame, int(sample_time * 1000))
                face_counts.append(len(faces))
                if not faces:
                    continue
                primary = max(faces, key=lambda item: item.get("area_ratio", 0.0))
                primary_faces.append(
                    (
                        float(primary.get("center_x", 0.0)) / max(frame.shape[1], 1),
                        float(primary.get("center_y", 0.0)) / max(frame.shape[0], 1),
                        float(primary.get("area_ratio", 0.0)),
                    )
                )
            analyzer.close()

            detections = len(primary_faces)
            face_presence_ratio = detections / max(len(face_frames), 1)
            avg_faces_per_frame = statistics.fmean(face_counts) if face_counts else 0.0
            avg_face_area_ratio = statistics.fmean(face[2] for face in primary_faces) if primary_faces else 0.0
            if len(primary_faces) > 1:
                center_x_std = statistics.pstdev(face[0] for face in primary_faces)
                center_y_std = statistics.pstdev(face[1] for face in primary_faces)
                face_stability = clamp(1.0 - ((center_x_std + center_y_std) / 0.55))
            face_overlay_ratio = (
                sum(1 for _, _, area_ratio in primary_faces if 0.005 <= area_ratio <= 0.06) / max(detections, 1)
                if primary_faces
                else 0.0
            )
            face_large_ratio = (
                sum(1 for _, _, area_ratio in primary_faces if area_ratio >= 0.12) / max(detections, 1)
                if primary_faces
                else 0.0
            )
            face_status = "applied"
        except Exception as exc:
            face_status = "error"
            face_error = str(exc)

    features = {
        "video_analysis_status": "applied",
        "video_duration_seconds": round(duration, 3),
        "frame_samples": len(grayscale_samples),
        "motion_score": round(motion_score, 4),
        "scene_change_rate": round(scene_change_rate, 4),
        "face_presence_ratio": round(face_presence_ratio, 4),
        "avg_faces_per_frame": round(avg_faces_per_frame, 4),
        "avg_face_area_ratio": round(avg_face_area_ratio, 4),
        "face_stability": round(face_stability, 4),
        "face_overlay_ratio": round(face_overlay_ratio, 4),
        "face_large_ratio": round(face_large_ratio, 4),
        "face_analysis_status": face_status,
    }
    if face_error:
        features["face_analysis_error"] = face_error
    return features


def extract_content_features(
    transcript: str | Path | list[dict[str, Any]] | dict[str, Any],
    heatmap: str | Path | list[dict[str, Any]] | None = None,
    *,
    video_path: str | Path | None = None,
) -> dict[str, Any]:
    transcript_segments = load_transcript(transcript)
    features = {}
    features.update(extract_transcript_features(transcript_segments))
    if heatmap is not None:
        features.update(extract_heatmap_features(load_heatmap(heatmap)))
    else:
        features.update(extract_heatmap_features([]))
    features.update(extract_video_features(video_path))
    return features


def classify_from_features(
    features: dict[str, Any],
    *,
    forced_content_type: str = "auto",
) -> ContentClassificationResult:
    mode = normalize_content_type_mode(forced_content_type)
    forced = "podcast" if mode == "podcast" else None
    return ContentClassificationResult(
        content_type="podcast",
        confidence=1.0 if forced else 0.95,
        reasons=[
            f"Content type manually forced to {forced}." if forced else PODCAST_ONLY_MVP_REASON
        ],
        features=features,
        scores={"podcast": 1.0},
        source="manual_override" if forced else "podcast_only_mvp",
        strategy_name="podcast",
        forced_content_type=forced,
    )

    # Deprecated multi-type heuristic path kept as dead code until the old
    # feature extraction internals can be removed without touching imports.
    scores = {
        "podcast": 0.1,
        "gameplay": 0.12,
        "tutorial": 0.12,
        "commentary": 0.1,
        "generic": 0.16,
    }
    reasons_by_type: dict[str, list[str]] = {key: [] for key in scores}

    def adjust(content_type: str, amount: float, reason: str | None = None) -> None:
        scores[content_type] += amount
        if reason and amount > 0:
            reasons_by_type[content_type].append(reason)

    speech_ratio = float(features.get("speech_coverage_ratio", 0.0))
    avg_segment_duration = float(features.get("avg_segment_duration", 0.0))
    short_segment_ratio = float(features.get("short_segment_ratio", 0.0))
    long_segment_ratio = float(features.get("long_segment_ratio", 0.0))
    speaker_count = int(features.get("speaker_count", 0) or 0)
    switch_rate = float(features.get("speaker_switch_rate_per_minute", 0.0))
    dominant_speaker_ratio = float(features.get("dominant_speaker_ratio", 0.0))
    chaos_ratio = float(features.get("chaos_ratio", 0.0))
    emotion_ratio = float(features.get("emotion_segment_ratio", 0.0))
    question_ratio = float(features.get("question_ratio", 0.0))
    words_per_second = float(features.get("avg_words_per_second", 0.0))
    gameplay_keyword_ratio = float(features.get("gameplay_keyword_ratio", 0.0))
    tutorial_keyword_ratio = float(features.get("tutorial_keyword_ratio", 0.0))
    podcast_keyword_ratio = float(features.get("podcast_keyword_ratio", 0.0))
    commentary_keyword_ratio = float(features.get("commentary_keyword_ratio", 0.0))
    instruction_segment_ratio = float(features.get("instruction_segment_ratio", 0.0))
    direct_address_ratio = float(features.get("direct_address_ratio", 0.0))
    commentary_segment_ratio = float(features.get("commentary_segment_ratio", 0.0))
    qa_turn_ratio = float(features.get("qa_turn_ratio", 0.0))
    motion_score = float(features.get("motion_score", 0.0))
    scene_change_rate = float(features.get("scene_change_rate", 0.0))
    face_presence_ratio = float(features.get("face_presence_ratio", 0.0))
    face_stability = float(features.get("face_stability", 0.0))
    face_overlay_ratio = float(features.get("face_overlay_ratio", 0.0))
    face_large_ratio = float(features.get("face_large_ratio", 0.0))
    heatmap_volatility = float(features.get("heatmap_volatility", 0.0))
    heatmap_high_energy_ratio = float(features.get("heatmap_high_energy_ratio", 0.0))

    if speech_ratio >= 0.75:
        adjust("podcast", 0.1, "Speech coverage is high enough for a talk-led format.")
        adjust("tutorial", 0.08, "Speech dominates the material, which fits guided instruction.")
        adjust("commentary", 0.16, "Speech coverage is high enough for narrator-led commentary.")
    if avg_segment_duration >= 1.6:
        adjust("podcast", 0.06, "Utterances are long enough to form complete spoken turns.")
        adjust("tutorial", 0.06, "Utterances are long enough to carry explanations.")
        adjust("commentary", 0.08, "Utterances are long enough to form explanatory monologue beats.")
    if long_segment_ratio >= 0.12:
        adjust("commentary", 0.08, "The transcript contains many longer explanatory turns.")
    if question_ratio >= 0.04:
        adjust("podcast", 0.18, "The transcript contains many explicit question turns.")
    elif question_ratio >= 0.025:
        adjust("podcast", 0.08, "The transcript includes recurring question turns.")
    if qa_turn_ratio >= 0.035:
        adjust("podcast", 0.22, "Questions are frequently followed by short answer-like responses.")
    if podcast_keyword_ratio >= 0.002:
        adjust("podcast", 0.16, "Transcript contains explicit conversation or podcast cues.")
    if direct_address_ratio >= 0.02:
        adjust("podcast", 0.06, "Speakers address each other or the viewer directly.")
    if 2.2 <= words_per_second <= 3.2 and avg_segment_duration >= 1.5:
        adjust("podcast", 0.06, "Delivery pacing matches a spoken conversation.")
    if face_presence_ratio >= 0.15 and face_stability >= 0.45:
        adjust("podcast", 0.05, "Faces stay on screen long enough to support conversation framing.")
    if instruction_segment_ratio >= 0.08:
        scores["podcast"] -= 0.14
    if commentary_segment_ratio >= 0.04 and question_ratio <= 0.025:
        scores["podcast"] -= 0.18
    if question_ratio <= 0.015:
        scores["podcast"] -= 0.08
    if gameplay_keyword_ratio >= 0.003 and short_segment_ratio >= 0.35:
        scores["podcast"] -= 0.08

    if tutorial_keyword_ratio >= 0.005:
        adjust("tutorial", 0.22, "Transcript contains clear instructional vocabulary.")
    if instruction_segment_ratio >= 0.12:
        adjust("tutorial", 0.24, "Instruction-like segments appear throughout the material.")
    elif instruction_segment_ratio >= 0.06:
        adjust("tutorial", 0.14, "The transcript repeatedly uses step-by-step instruction patterns.")
    if direct_address_ratio >= 0.03:
        adjust("tutorial", 0.12, "The speaker frequently addresses the viewer directly.")
    elif direct_address_ratio >= 0.015:
        adjust("tutorial", 0.06, "Viewer-directed phrasing appears repeatedly.")
    if words_per_second <= 3.1:
        adjust("tutorial", 0.06, "Delivery speed is compatible with instructional pacing.")
    if question_ratio <= 0.015:
        adjust("tutorial", 0.05, "The transcript focuses on explanation rather than dialogue.")
    if face_presence_ratio <= 0.18:
        adjust("tutorial", 0.06, "The video looks more screen-led than face-led.")
    if face_large_ratio <= 0.35:
        adjust("tutorial", 0.04, "The frame is not dominated by a large talking head.")
    if qa_turn_ratio >= 0.03:
        scores["tutorial"] -= 0.1
    if commentary_segment_ratio >= 0.04:
        scores["tutorial"] -= 0.1
    if gameplay_keyword_ratio >= 0.003:
        scores["tutorial"] -= 0.06

    if gameplay_keyword_ratio >= 0.003:
        adjust("gameplay", 0.24, "Transcript contains gameplay-oriented vocabulary.")
    elif gameplay_keyword_ratio >= 0.0015 and short_segment_ratio >= 0.35:
        adjust("gameplay", 0.12, "Reactive short lines still resemble gameplay comms.")
    if motion_score >= 0.75 and scene_change_rate >= 0.28:
        adjust("gameplay", 0.12, "Visual motion and scene changes are highly dynamic.")
    elif motion_score >= 0.55 and scene_change_rate >= 0.18:
        adjust("gameplay", 0.06, "The visual layer is dynamic enough to support gameplay.")
    if emotion_ratio >= 0.24:
        adjust("gameplay", 0.12, "Speech contains many emotionally elevated segments.")
    if short_segment_ratio >= 0.45:
        adjust("gameplay", 0.12, "Many short reactive lines match gameplay comms.")
    elif short_segment_ratio >= 0.3:
        adjust("gameplay", 0.06, "Short reactive lines appear often enough for gameplay.")
    if chaos_ratio >= 0.12:
        adjust("gameplay", 0.08, "The transcript has some overlap or chaotic exchanges.")
    if face_overlay_ratio >= 0.25 and speech_ratio <= 0.75:
        adjust("gameplay", 0.06, "Detected faces look more like a smaller facecam overlay.")
    if heatmap_volatility >= 0.11 or heatmap_high_energy_ratio >= 0.16:
        adjust("gameplay", 0.08, "Energy changes are dynamic enough for gameplay.")
    if speech_ratio >= 0.82 and gameplay_keyword_ratio < 0.002 and short_segment_ratio < 0.28:
        scores["gameplay"] -= 0.18
    if instruction_segment_ratio >= 0.08:
        scores["gameplay"] -= 0.12
    if question_ratio >= 0.04 and qa_turn_ratio >= 0.035:
        scores["gameplay"] -= 0.1

    if commentary_segment_ratio >= 0.04 or commentary_keyword_ratio >= 0.0025:
        adjust("commentary", 0.2, "Transcript contains repeated public-affairs or commentary cues.")
    elif commentary_segment_ratio >= 0.012 or commentary_keyword_ratio >= 0.0012:
        adjust("commentary", 0.1, "Some transcript segments resemble commentary-style analysis.")
    if question_ratio <= 0.025:
        adjust("commentary", 0.12, "There are few explicit question turns, which fits monologue delivery.")
    if qa_turn_ratio <= 0.02:
        adjust("commentary", 0.1, "Question-to-answer turn-taking is limited.")
    if instruction_segment_ratio <= 0.04 and tutorial_keyword_ratio <= 0.004:
        adjust("commentary", 0.06, "Instructional language is scarce.")
    if direct_address_ratio <= 0.015:
        adjust("commentary", 0.06, "Direct address to a viewer or co-host is limited.")
    if question_ratio >= 0.04 and qa_turn_ratio >= 0.03:
        scores["commentary"] -= 0.14
    if instruction_segment_ratio >= 0.08:
        scores["commentary"] -= 0.16
    if gameplay_keyword_ratio >= 0.003:
        scores["commentary"] -= 0.12

    scores["podcast"] = clamp(scores["podcast"], 0.0, 1.2)
    scores["gameplay"] = clamp(scores["gameplay"], 0.0, 1.2)
    scores["tutorial"] = clamp(scores["tutorial"], 0.0, 1.2)
    scores["commentary"] = clamp(scores["commentary"], 0.0, 1.2)

    if speaker_count == 0 or speech_ratio < 0.35:
        adjust("generic", 0.2, "Speech structure is too weak for a more specific class.")
    specialized_scores = [
        scores["podcast"],
        scores["gameplay"],
        scores["tutorial"],
        scores["commentary"],
    ]
    sorted_specialized_scores = sorted(specialized_scores, reverse=True)
    if sorted_specialized_scores[0] - sorted_specialized_scores[1] < 0.08:
        adjust("generic", 0.14, "The content signals are fairly ambiguous.")
    if all(score < 0.56 for score in specialized_scores):
        adjust("generic", 0.18, "No specialized class is confident enough yet.")
    if (
        gameplay_keyword_ratio < 0.002
        and tutorial_keyword_ratio < 0.004
        and podcast_keyword_ratio < 0.002
        and commentary_keyword_ratio < 0.0015
    ):
        adjust("generic", 0.08, "Keyword evidence is weak, so a generic fallback stays safer.")

    ranked = sorted(
        ((content_type, score) for content_type, score in scores.items() if content_type != "generic"),
        key=lambda item: item[1],
        reverse=True,
    )
    best_non_generic, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    score_margin = best_score - second_score

    if best_score < 0.52 or score_margin < 0.08:
        selected_type = "generic"
        confidence = clamp(0.45 + scores["generic"] * 0.25 + max(score_margin, 0.0) * 0.2, 0.45, 0.78)
        reasons = reasons_by_type["generic"][:3] or [
            f"Signals for {best_non_generic} were not strong enough to avoid a safe generic strategy."
        ]
        source = "heuristic_classifier_fallback"
        strategy_name = "generic"
    else:
        selected_type = best_non_generic
        confidence = clamp(0.48 + best_score * 0.28 + score_margin * 0.55, 0.5, 0.97)
        reasons = reasons_by_type[selected_type][:3] or [f"Matched the {selected_type} heuristic profile."]
        source = "heuristic_classifier"
        strategy_name = selected_type

    return ContentClassificationResult(
        content_type=selected_type,
        confidence=confidence,
        reasons=reasons,
        features=features,
        scores={key: round(value, 4) for key, value in scores.items()},
        source=source,
        strategy_name=strategy_name,
    )


def classify_content(
    transcript: str | Path | list[dict[str, Any]] | dict[str, Any],
    heatmap: str | Path | list[dict[str, Any]] | None = None,
    *,
    video_path: str | Path | None = None,
    forced_content_type: str = "auto",
) -> ContentClassificationResult:
    features = extract_content_features(transcript, heatmap, video_path=video_path)
    return classify_from_features(features, forced_content_type=forced_content_type)


def save_content_profile(result: ContentClassificationResult, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file_handle:
        json.dump(result.to_dict(), file_handle, ensure_ascii=False, indent=2)


def load_content_profile(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file_handle:
        return json.load(file_handle)


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * clamp(quantile)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local content classifier for Viral Cutter AI")
    parser.add_argument("--transcript", required=True, help="Transcript JSON path")
    parser.add_argument("--heatmap", default=None, help="Heatmap JSON path")
    parser.add_argument("--video", default=None, help="Video path used for lightweight visual analysis")
    parser.add_argument(
        "--content-type",
        default="auto",
        choices=VALID_CONTENT_TYPE_MODES,
        help="auto or podcast. The MVP routes supported material as podcast/talking-head.",
    )
    parser.add_argument("--output", default=None, help="Optional JSON output path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = classify_content(
        args.transcript,
        args.heatmap,
        video_path=args.video,
        forced_content_type=args.content_type,
    )
    payload = result.to_dict()
    if args.output:
        save_content_profile(result, args.output)
        print(f"Saved content profile to: {args.output}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
