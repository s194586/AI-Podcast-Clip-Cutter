import math
import re


WORD_RE = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)
SENTENCE_SPLIT_RE = re.compile(r"(?:(?<=[.!?])|(?<=\.\.\.))\s+")

EMOTION_TOKENS = {
    "ale",
    "boom",
    "bro",
    "clutch",
    "co",
    "ez",
    "fuck",
    "go",
    "headshot",
    "hit",
    "holy",
    "jak",
    "jebac",
    "jest",
    "kill",
    "kurde",
    "kurwa",
    "lol",
    "nice",
    "nie",
    "no",
    "oho",
    "omg",
    "please",
    "serio",
    "shot",
    "tak",
    "teraz",
    "what",
    "why",
    "wow",
}

HOOK_TOKENS = {
    "co",
    "czemu",
    "dlaczego",
    "jak",
    "look",
    "patrz",
    "serio",
    "sluchaj",
    "uwazaj",
    "wait",
    "why",
}

PAYOFF_TOKENS = {
    "ban",
    "boom",
    "clutch",
    "done",
    "git",
    "hit",
    "jest",
    "kill",
    "koniec",
    "kurwa",
    "lezy",
    "nice",
    "no",
    "padl",
    "trafiony",
    "win",
}

FILLER_TOKENS = {
    "a",
    "eee",
    "hmm",
    "jakby",
    "mhm",
    "nie",
    "no",
    "okej",
    "tak",
    "yyy",
}

DEFAULT_SCORE_WEIGHTS = {
    "heatmap_avg": 0.34,
    "heatmap_peak": 0.12,
    "importance_score": 0.12,
    "speech_density_score": 0.10,
    "emotion_score": 0.10,
    "punchiness_score": 0.08,
    "hook_score": 0.08,
    "payoff_score": 0.08,
    "speaker_turn_score": 0.05,
    "duration_fit_score": 0.05,
    "chaos_score": 0.04,
    "repetition_penalty": 0.06,
}

REASON_LABELS = {
    "importance_score": "contains high-importance transcript moments",
    "speech_density_score": "good speech density for a short clip",
    "emotion_score": "contains punchy or emotional language",
    "punchiness_score": "has several short punchy lines",
    "hook_score": "starts with a stronger hook signal",
    "payoff_score": "ends with a clearer payoff signal",
    "speaker_turn_score": "has speaker dynamics or conversational turns",
    "duration_fit_score": "fits a strong short-form duration window",
    "chaos_score": "stays relatively clear despite overlap risk",
}


def parse_time(value):
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    parts = [part for part in text.split(":") if part]
    if len(parts) == 1:
        return float(parts[0])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"Invalid timestamp format: {value}")


def clamp(value, lower=0.0, upper=1.0):
    return max(lower, min(upper, value))


def tokenize(text):
    return WORD_RE.findall(str(text or "").lower())


def split_sentences(text):
    compact = " ".join(str(text or "").split())
    if not compact:
        return []
    pieces = [piece.strip() for piece in SENTENCE_SPLIT_RE.split(compact) if piece.strip()]
    return pieces or [compact]


def normalize_transcript_segments(transcript):
    segments = []
    for item in transcript:
        try:
            start = parse_time(item["start"])
            end = parse_time(item["end"])
        except Exception:
            continue
        if end <= start:
            continue
        importance = item.get("importance")
        try:
            importance = int(importance) if importance is not None else 3
        except Exception:
            importance = 3
        segments.append(
            {
                "start": start,
                "end": end,
                "text": " ".join(str(item.get("text", "")).split()),
                "speaker": str(
                    item.get("speaker")
                    or item.get("speaker_id")
                    or item.get("speakerId")
                    or "Speaker 0"
                ).strip()
                or "Speaker 0",
                "importance": importance,
                "chaos": bool(item.get("chaos", False)),
            }
        )
    return segments


def resolve_score_weights(score_weights=None):
    resolved = dict(DEFAULT_SCORE_WEIGHTS)
    if not score_weights:
        return resolved
    for key, value in dict(score_weights).items():
        if key not in resolved:
            continue
        try:
            resolved[key] = float(value)
        except Exception:
            continue
    return resolved


def _segments_for_window(segments, start, end):
    return [segment for segment in segments if segment["end"] > start and segment["start"] < end]


def _peak_heatmap_value(heatmap, start, end):
    peak = 0.0
    for entry in heatmap:
        entry_start = float(entry.get("start_time", 0.0))
        entry_end = float(entry.get("end_time", entry_start))
        if entry_end <= start:
            continue
        if entry_start >= end:
            break
        peak = max(peak, float(entry.get("value", 0.0)))
    return peak


def _overlap_duration(segment, start, end):
    return max(0.0, min(segment["end"], end) - max(segment["start"], start))


def _importance_score(window_segments, start, end):
    weighted = 0.0
    total = 0.0
    for segment in window_segments:
        overlap = _overlap_duration(segment, start, end)
        if overlap <= 0:
            continue
        total += overlap
        weighted += overlap * clamp((int(segment.get("importance", 3)) - 1) / 4.0)
    if total <= 0:
        return 0.5
    return clamp(weighted / total)


def _speaker_turn_score(window_segments):
    speakers = [segment.get("speaker") or "Speaker 0" for segment in window_segments if segment.get("text")]
    if len(speakers) < 2:
        return 0.0, 1 if speakers else 0, 0
    switches = sum(1 for left, right in zip(speakers, speakers[1:]) if left != right)
    unique_speakers = len(set(speakers))
    return clamp(switches / 3.0), unique_speakers, switches


def _chaos_score(window_segments, start, end):
    total_overlap = 0.0
    chaos_overlap = 0.0
    for segment in window_segments:
        overlap = _overlap_duration(segment, start, end)
        if overlap <= 0:
            continue
        total_overlap += overlap
        if segment.get("chaos"):
            chaos_overlap += overlap
    if total_overlap <= 0:
        return 1.0, 0.0
    chaos_ratio = clamp(chaos_overlap / total_overlap)
    return clamp(1.0 - chaos_ratio * 0.6), chaos_ratio


def _speech_density_score(word_count, duration):
    if duration <= 0:
        return 0.0, 0.0
    words_per_second = word_count / duration
    score = 1.0 - min(abs(words_per_second - 3.1) / 3.1, 1.0)
    return clamp(score), words_per_second


def _duration_fit_score(duration):
    if duration <= 0:
        return 0.0
    return clamp(1.0 - min(abs(duration - 36.0) / 24.0, 1.0))


def _emotion_score(words, text):
    emotion_hits = sum(1 for word in words if word in EMOTION_TOKENS)
    punctuation_hits = text.count("!") + text.count("?")
    return clamp((emotion_hits + punctuation_hits) / 6.0), emotion_hits, punctuation_hits


def _punchiness_score(sentences):
    if not sentences:
        return 0.0, 0
    short_sentences = sum(1 for sentence in sentences if 2 <= len(tokenize(sentence)) <= 10)
    return clamp(short_sentences / max(1, len(sentences))), short_sentences


def _hook_score(sentences, words):
    if not sentences:
        return 0.0
    first_sentence = sentences[0].lower()
    first_words = set(words[:12])
    score = 0.0
    if "?" in first_sentence or "!" in first_sentence:
        score += 0.5
    if first_words.intersection(HOOK_TOKENS):
        score += 0.5
    return clamp(score)


def _payoff_score(sentences, words):
    if not sentences:
        return 0.0
    last_sentence = sentences[-1].lower()
    last_words = set(words[-12:])
    score = 0.0
    if last_sentence.endswith(("!", "?")):
        score += 0.4
    if last_words.intersection(PAYOFF_TOKENS):
        score += 0.6
    return clamp(score)


def _repetition_penalty(words):
    if not words:
        return 0.0, 0.0
    unique_ratio = len(set(words)) / len(words)
    filler_ratio = sum(1 for word in words if word in FILLER_TOKENS) / len(words)
    penalty = clamp((1.0 - unique_ratio) * 0.8 + filler_ratio * 0.6)
    return penalty, filler_ratio


def _build_reasons(features, score_weights):
    reasons = []
    heatmap_strength = max(features.get("heatmap_avg", 0.0), features.get("heatmap_peak", 0.0))
    if heatmap_strength >= 0.55 and (score_weights.get("heatmap_avg", 0.0) + score_weights.get("heatmap_peak", 0.0)) >= 0.12:
        reasons.append("strong heatmap support")

    content_candidates = [
        (
            features.get(feature_key, 0.0) * max(0.0, float(score_weights.get(feature_key, 0.0))),
            label,
        )
        for feature_key, label in REASON_LABELS.items()
    ]
    for contribution, label in sorted(content_candidates, reverse=True):
        if contribution <= 0.04:
            continue
        if label not in reasons:
            reasons.append(label)
        if len(reasons) >= 3:
            break

    if len(reasons) < 3 and features.get("heatmap_peak", 0.0) >= 0.75 and "strong heatmap support" not in reasons:
        reasons.append("high local heatmap peak")
    if features.get("repetition_penalty", 0.0) >= 0.45 and score_weights.get("repetition_penalty", 0.0) > 0:
        reasons.append("penalized for repetitive or filler-heavy wording")
    return reasons


def score_candidate(candidate, transcript_segments, heatmap, *, score_weights=None, strategy_name="generic"):
    score_weights = resolve_score_weights(score_weights)
    start = float(candidate["start"])
    end = float(candidate["end"])
    duration = max(0.01, float(candidate["duration"]))
    window_segments = _segments_for_window(transcript_segments, start, end)
    text = " ".join(str(candidate.get("text") or "").split())
    words = tokenize(text)
    sentences = split_sentences(text)
    word_count = len(words)

    heatmap_avg = clamp(float(candidate.get("avg_value", 0.0)))
    heatmap_peak = clamp(_peak_heatmap_value(heatmap, start, end))
    importance_score = _importance_score(window_segments, start, end)
    speech_density_score, words_per_second = _speech_density_score(word_count, duration)
    emotion_score, emotion_hits, punctuation_hits = _emotion_score(words, text)
    punchiness_score, short_sentences = _punchiness_score(sentences)
    hook_score = _hook_score(sentences, words)
    payoff_score = _payoff_score(sentences, words)
    speaker_turn_score, speaker_count, speaker_switches = _speaker_turn_score(window_segments)
    duration_fit_score = _duration_fit_score(duration)
    chaos_score, chaos_ratio = _chaos_score(window_segments, start, end)
    repetition_penalty, filler_ratio = _repetition_penalty(words)

    weighted_score = (
        heatmap_avg * score_weights["heatmap_avg"]
        + heatmap_peak * score_weights["heatmap_peak"]
        + importance_score * score_weights["importance_score"]
        + speech_density_score * score_weights["speech_density_score"]
        + emotion_score * score_weights["emotion_score"]
        + punchiness_score * score_weights["punchiness_score"]
        + hook_score * score_weights["hook_score"]
        + payoff_score * score_weights["payoff_score"]
        + speaker_turn_score * score_weights["speaker_turn_score"]
        + duration_fit_score * score_weights["duration_fit_score"]
        + chaos_score * score_weights["chaos_score"]
        - repetition_penalty * score_weights["repetition_penalty"]
    )
    local_score = round(clamp(weighted_score, 0.0, 1.0) * 100.0, 2)

    features = {
        "heatmap_avg": round(heatmap_avg, 4),
        "heatmap_peak": round(heatmap_peak, 4),
        "importance_score": round(importance_score, 4),
        "speech_density_score": round(speech_density_score, 4),
        "words_per_second": round(words_per_second, 3),
        "word_count": word_count,
        "emotion_score": round(emotion_score, 4),
        "emotion_hits": emotion_hits,
        "punctuation_hits": punctuation_hits,
        "punchiness_score": round(punchiness_score, 4),
        "short_sentences": short_sentences,
        "sentence_count": len(sentences),
        "hook_score": round(hook_score, 4),
        "payoff_score": round(payoff_score, 4),
        "speaker_turn_score": round(speaker_turn_score, 4),
        "speaker_count": speaker_count,
        "speaker_switches": speaker_switches,
        "duration_fit_score": round(duration_fit_score, 4),
        "chaos_score": round(chaos_score, 4),
        "chaos_ratio": round(chaos_ratio, 4),
        "repetition_penalty": round(repetition_penalty, 4),
        "filler_ratio": round(filler_ratio, 4),
        "segment_count": len(window_segments),
    }

    scored = dict(candidate)
    scored["local_score"] = local_score
    scored["local_features"] = features
    scored["selection_reasons"] = _build_reasons(features, score_weights)
    scored["selection_source"] = "local_scoring"
    scored["selection_strategy"] = strategy_name
    return scored


def score_candidates(candidates, transcript, heatmap, *, score_weights=None, strategy_name="generic"):
    transcript_segments = normalize_transcript_segments(transcript)
    scored = [
        score_candidate(
            candidate,
            transcript_segments,
            heatmap,
            score_weights=score_weights,
            strategy_name=strategy_name,
        )
        for candidate in candidates
    ]
    scored.sort(key=lambda item: (item["local_score"], item.get("avg_value", 0.0), -item.get("duration", 0.0)), reverse=True)
    for index, candidate in enumerate(scored, start=1):
        candidate["local_rank"] = index
    return scored
