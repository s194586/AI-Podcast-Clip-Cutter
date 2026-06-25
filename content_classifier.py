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
    "Podcast-only product: every supported source is routed as podcast/talking-head material."
)

WORD_RE = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)
PODCAST_TOKENS = {
    "conversation",
    "dialog",
    "episode",
    "guest",
    "host",
    "interview",
    "podcast",
    "question",
    "rozmowa",
    "rozmowy",
    "gosc",
    "gospodarz",
    "odcinek",
    "pytanie",
    "odpowiedz",
    "historia",
    "temat",
}
QUESTION_PREFIXES = (
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
    "why ",
    "how ",
    "what ",
    "when ",
    "who ",
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
            "features": dict(self.features),
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
            f"Unsupported content type for the podcast-only product: {value}. "
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
    return text


def canonicalize_text(text: str) -> str:
    lowered = str(text or "").lower()
    normalized = unicodedata.normalize("NFKD", lowered)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def tokenize(text: str) -> list[str]:
    return [canonicalize_text(token) for token in WORD_RE.findall(str(text or ""))]


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
            "podcast_keyword_ratio": 0.0,
            "qa_turn_ratio": 0.0,
            "speaker_distribution": {},
        }

    durations = [segment["duration"] for segment in transcript_segments]
    total_span = max(0.01, transcript_segments[-1]["end"] - transcript_segments[0]["start"])
    speech_seconds = sum(durations)
    speaker_sequence = [segment["speaker"] for segment in transcript_segments if segment.get("text")]
    speaker_counts = Counter(speaker_sequence)
    speaker_switches = sum(1 for left, right in zip(speaker_sequence, speaker_sequence[1:]) if left != right)
    dominant_speaker_ratio = (
        speaker_counts.most_common(1)[0][1] / max(len(speaker_sequence), 1) if speaker_counts else 0.0
    )

    total_words = 0
    podcast_hits = 0
    question_count = 0
    exclamation_count = 0
    emotion_count = 0
    qa_turn_count = 0
    tokenized_segments: list[list[str]] = []
    normalized_texts: list[str] = []

    for segment in transcript_segments:
        text = str(segment.get("text", ""))
        tokens = tokenize(text)
        total_words += len(tokens)
        podcast_hits += sum(1 for token in tokens if token in PODCAST_TOKENS)
        question_count += int("?" in text)
        exclamation_count += int("!" in text)
        emotion_count += int("!" in text or int(segment.get("importance", 3) or 3) >= 4)
        tokenized_segments.append(tokens)
        normalized_texts.append(canonicalize_text(text))

    for index, text in enumerate(normalized_texts[:-1]):
        if not any(text.startswith(prefix) for prefix in QUESTION_PREFIXES):
            continue
        next_text = transcript_segments[index + 1].get("text", "")
        next_tokens = tokenized_segments[index + 1]
        if "?" not in str(next_text) and 1 <= len(next_tokens) <= 22:
            qa_turn_count += 1

    return {
        "segment_count": len(transcript_segments),
        "speaker_count": len(speaker_counts),
        "speech_coverage_ratio": round(clamp(speech_seconds / total_span), 4),
        "avg_segment_duration": round(statistics.fmean(durations), 4),
        "median_segment_duration": round(statistics.median(durations), 4),
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
            sum(1 for segment in transcript_segments if int(segment.get("importance", 3) or 3) >= 5)
            / len(transcript_segments),
            4,
        ),
        "emotion_segment_ratio": round(emotion_count / len(transcript_segments), 4),
        "question_ratio": round(question_count / len(transcript_segments), 4),
        "exclamation_ratio": round(exclamation_count / len(transcript_segments), 4),
        "podcast_keyword_ratio": round(podcast_hits / max(total_words, 1), 4),
        "qa_turn_ratio": round(qa_turn_count / len(transcript_segments), 4),
        "speaker_distribution": dict(sorted(speaker_counts.items())),
    }


def extract_heatmap_features(heatmap: list[dict[str, float]]) -> dict[str, Any]:
    if not heatmap:
        return {
            "heatmap_mean": 0.0,
            "heatmap_peak": 0.0,
            "heatmap_std": 0.0,
            "heatmap_high_energy_ratio": 0.0,
            "heatmap_p90": 0.0,
        }

    values = [float(item["value"]) for item in heatmap]
    return {
        "heatmap_mean": round(statistics.fmean(values), 4),
        "heatmap_peak": round(max(values), 4),
        "heatmap_std": round(statistics.pstdev(values) if len(values) > 1 else 0.0, 4),
        "heatmap_high_energy_ratio": round(sum(1 for value in values if value >= 0.65) / len(values), 4),
        "heatmap_p90": round(_percentile(values, 0.9), 4),
    }


def extract_video_features(video_path: str | Path | None, **_: Any) -> dict[str, Any]:
    if not video_path:
        return {"video_analysis_status": "skipped"}
    path = Path(video_path)
    if not path.exists():
        return {"video_analysis_status": "missing", "video_path": str(path)}
    return {
        "video_analysis_status": "present",
        "video_path": str(path),
        "video_extension": path.suffix.lower(),
        "video_size_bytes": path.stat().st_size,
    }


def extract_content_features(
    transcript: str | Path | list[dict[str, Any]] | dict[str, Any],
    heatmap: str | Path | list[dict[str, Any]] | None = None,
    *,
    video_path: str | Path | None = None,
) -> dict[str, Any]:
    transcript_segments = load_transcript(transcript)
    features: dict[str, Any] = {}
    features.update(extract_transcript_features(transcript_segments))
    features.update(extract_heatmap_features(load_heatmap(heatmap)) if heatmap is not None else extract_heatmap_features([]))
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
    parser = argparse.ArgumentParser(description="Build a podcast/talking-head content profile")
    parser.add_argument("--transcript", required=True, help="Transcript JSON path")
    parser.add_argument("--heatmap", default=None, help="Heatmap JSON path")
    parser.add_argument("--video", default=None, help="Video path recorded in the profile")
    parser.add_argument(
        "--content-type",
        default="auto",
        choices=VALID_CONTENT_TYPE_MODES,
        help="auto or podcast. Both route to the podcast/talking-head pipeline.",
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
        print(f"Saved podcast profile to: {args.output}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
