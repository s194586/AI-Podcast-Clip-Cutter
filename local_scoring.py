import re
from typing import Any


WORD_RE = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)
SENTENCE_SPLIT_RE = re.compile(r"(?:(?<=[.!?])|(?<=\.\.\.))\s+")

ACTIVE_SCORING_STRATEGY = "podcast"

DEFAULT_SCORE_WEIGHTS = {
    "heatmap_avg": 0.12,
    "heatmap_peak": 0.06,
    "importance_score": 0.10,
    "speech_density_score": 0.16,
    "emotion_score": 0.05,
    "punchiness_score": 0.05,
    "hook_score": 0.12,
    "payoff_score": 0.12,
    "speaker_turn_score": 0.14,
    "duration_fit_score": 0.04,
    "chaos_score": 0.10,
    "repetition_penalty": 0.08,
}

EMOTION_TOKENS = {
    "ale",
    "co",
    "dokladnie",
    "haha",
    "jak",
    "kurde",
    "naprawde",
    "nie",
    "no",
    "serio",
    "tak",
    "wow",
}

HOOK_TOKENS = {
    "co",
    "czemu",
    "dlaczego",
    "jak",
    "najwazniejsze",
    "serio",
    "sluchaj",
    "wait",
    "why",
}

PAYOFF_TOKENS = {
    "dlatego",
    "odpowiedz",
    "pointa",
    "puenta",
    "sedno",
    "sens",
    "wniosek",
    "zrozumialem",
    "zrozumialam",
}

PODCAST_DIALOGUE_TOKENS = {
    "czemu",
    "dlaczego",
    "dokladnie",
    "haha",
    "mhm",
    "odpowiedz",
    "powiedz",
    "pytanie",
    "serio",
    "tak",
    "wlasnie",
}

BOUNDARY_CONTINUATION_TOKENS = {
    "a",
    "ale",
    "bo",
    "czyli",
    "i",
    "jakby",
    "no",
    "oraz",
    "to",
    "wiec",
    "ze",
}

CONTEXTLESS_START_TOKENS = {
    "a",
    "ale",
    "bo",
    "czyli",
    "i",
    "no",
    "on",
    "ona",
    "one",
    "oni",
    "takze",
    "tam",
    "ten",
    "to",
    "wtedy",
    "wiec",
}

TRANSITION_ONLY_TOKENS = {
    "dalej",
    "kolejny",
    "nastepnie",
    "potem",
    "teraz",
    "zaraz",
}

AD_LIKE_TOKENS = {
    "case",
    "changer",
    "gift",
    "kod",
    "link",
    "partner",
    "promo",
    "promokod",
    "reklama",
    "skin",
    "skiny",
    "sponsor",
    "sponsorowany",
}

FILLER_TOKENS = {
    "eee",
    "yyy",
    "jakby",
    "generalnie",
    "wiesz",
}

REASON_LABELS = {
    "speech_density_score": "strong spoken pacing",
    "speaker_turn_score": "clear speaker exchange",
    "hook_score": "starts with a strong hook",
    "payoff_score": "ends with a clearer payoff",
    "importance_score": "overlaps important transcript segments",
    "emotion_score": "emotionally elevated delivery",
    "punchiness_score": "compact sentence rhythm",
    "duration_fit_score": "fits short-form duration",
    "chaos_score": "low transcript chaos",
}


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def parse_time(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    parts = [part for part in str(value).strip().replace(",", ".").split(":") if part]
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def normalize_scoring_strategy(strategy_name: str | None) -> str:
    return ACTIVE_SCORING_STRATEGY


def resolve_score_weights(score_weights: dict[str, float] | None = None) -> dict[str, float]:
    resolved = dict(DEFAULT_SCORE_WEIGHTS)
    if score_weights:
        for key, value in score_weights.items():
            if key in resolved:
                resolved[key] = float(value)
    return resolved


def normalize_token(token: str) -> str:
    replacements = {
        "ą": "a",
        "ć": "c",
        "ę": "e",
        "ł": "l",
        "ń": "n",
        "ó": "o",
        "ś": "s",
        "ź": "z",
        "ż": "z",
    }
    lowered = str(token or "").lower()
    return "".join(replacements.get(char, char) for char in lowered)


def tokenize(text: str) -> list[str]:
    return [normalize_token(token) for token in WORD_RE.findall(str(text or ""))]


def split_sentences(text: str) -> list[str]:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return []
    return [part.strip() for part in SENTENCE_SPLIT_RE.split(normalized) if part.strip()]


def normalize_transcript_segments(transcript: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    raw_segments = transcript.get("segments", []) if isinstance(transcript, dict) else transcript
    normalized: list[dict[str, Any]] = []
    for item in raw_segments or []:
        if not isinstance(item, dict):
            continue
        try:
            start = parse_time(item.get("start", 0.0))
            end = parse_time(item.get("end", start))
        except Exception:
            continue
        if end <= start:
            continue
        normalized.append(
            {
                "start": start,
                "end": end,
                "text": " ".join(str(item.get("text", "")).split()),
                "speaker": str(item.get("speaker") or item.get("speaker_id") or item.get("speakerId") or ""),
                "importance": int(item.get("importance", 3) or 3),
                "chaos": bool(item.get("chaos", False)),
            }
        )
    return sorted(normalized, key=lambda item: item["start"])


def _overlap_seconds(start: float, end: float, item_start: float, item_end: float) -> float:
    return max(0.0, min(end, item_end) - max(start, item_start))


def _segments_for_window(transcript_segments: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    return [
        segment
        for segment in transcript_segments
        if _overlap_seconds(start, end, float(segment["start"]), float(segment["end"])) > 0
    ]


def _peak_heatmap_value(heatmap: list[dict[str, Any]], start: float, end: float) -> float:
    values: list[float] = []
    for item in heatmap or []:
        try:
            item_start = float(item.get("start_time", item.get("start", 0.0)))
            item_end = float(item.get("end_time", item.get("end", item_start)))
            value = float(item.get("value", 0.0))
        except Exception:
            continue
        if _overlap_seconds(start, end, item_start, item_end) > 0:
            values.append(value)
    return max(values) if values else 0.0


def _importance_score(window_segments: list[dict[str, Any]], start: float, end: float) -> float:
    weighted_sum = 0.0
    total_weight = 0.0
    for segment in window_segments:
        overlap = _overlap_seconds(start, end, float(segment["start"]), float(segment["end"]))
        if overlap <= 0:
            continue
        importance = clamp((float(segment.get("importance", 3) or 3) - 1.0) / 4.0)
        weighted_sum += importance * overlap
        total_weight += overlap
    return weighted_sum / total_weight if total_weight else 0.0


def _speech_density_score(word_count: int, duration: float) -> tuple[float, float]:
    words_per_second = word_count / max(duration, 0.01)
    if word_count <= 0:
        return 0.0, words_per_second
    target = 3.1
    return clamp(1.0 - abs(words_per_second - target) / target), words_per_second


def _emotion_score(words: list[str], text: str) -> tuple[float, int, int]:
    emotion_hits = sum(1 for word in words if word in EMOTION_TOKENS)
    punctuation_hits = str(text or "").count("!") + str(text or "").count("?")
    return clamp((emotion_hits + punctuation_hits) / 6.0), emotion_hits, punctuation_hits


def _punchiness_score(sentences: list[str]) -> tuple[float, int]:
    if not sentences:
        return 0.0, 0
    short_sentences = sum(1 for sentence in sentences if 2 <= len(tokenize(sentence)) <= 10)
    return clamp(short_sentences / len(sentences)), short_sentences


def _hook_score(sentences: list[str], words: list[str]) -> float:
    if not sentences:
        return 0.0
    first_sentence = sentences[0]
    first_words = tokenize(first_sentence)[:12]
    score = 0.0
    if "?" in first_sentence or "!" in first_sentence:
        score += 0.5
    if any(word in HOOK_TOKENS for word in first_words):
        score += 0.5
    return clamp(score)


def _payoff_score(sentences: list[str], words: list[str]) -> float:
    if not sentences:
        return 0.0
    last_sentence = sentences[-1].strip()
    last_words = tokenize(last_sentence)[-12:]
    score = 0.0
    if last_sentence.endswith(("!", "?")):
        score += 0.4
    if any(word in PAYOFF_TOKENS for word in last_words):
        score += 0.6
    return clamp(score)


def _speaker_turn_score(window_segments: list[dict[str, Any]]) -> tuple[float, int, int]:
    speakers = [str(segment.get("speaker") or "") for segment in window_segments if segment.get("text")]
    unique_speakers = len({speaker for speaker in speakers if speaker})
    switches = sum(1 for left, right in zip(speakers, speakers[1:]) if left and right and left != right)
    return clamp(switches / 3.0), unique_speakers, switches


def _duration_fit_score(duration: float) -> float:
    target = 36.0
    return clamp(1.0 - abs(float(duration) - target) / 24.0)


def _chaos_score(window_segments: list[dict[str, Any]], start: float, end: float) -> tuple[float, float]:
    if not window_segments:
        return 1.0, 0.0
    chaos_seconds = 0.0
    total_seconds = 0.0
    for segment in window_segments:
        overlap = _overlap_seconds(start, end, float(segment["start"]), float(segment["end"]))
        total_seconds += overlap
        if segment.get("chaos"):
            chaos_seconds += overlap
    ratio = chaos_seconds / max(total_seconds, 0.01)
    return clamp(1.0 - ratio * 0.6), ratio


def _repetition_penalty(words: list[str]) -> tuple[float, float]:
    if not words:
        return 0.0, 0.0
    unique_ratio = len(set(words)) / len(words)
    filler_ratio = sum(1 for word in words if word in FILLER_TOKENS) / len(words)
    return clamp((1.0 - unique_ratio) * 0.8 + filler_ratio * 0.6), filler_ratio


def _boundary_completeness_score(sentences: list[str]) -> tuple[float, float, float]:
    if not sentences:
        return 0.0, 0.0, 0.0
    first_words = tokenize(sentences[0])
    last_words = tokenize(sentences[-1])
    start_penalty = 0.0
    end_penalty = 0.0
    if first_words and first_words[0] in BOUNDARY_CONTINUATION_TOKENS:
        start_penalty += 0.45
    if last_words and last_words[-1] in BOUNDARY_CONTINUATION_TOKENS:
        end_penalty += 0.45
    if first_words and len(first_words) <= 2:
        start_penalty += 0.15
    if last_words and len(last_words) <= 2:
        end_penalty += 0.15
    return clamp(1.0 - min(1.0, start_penalty + end_penalty)), start_penalty, end_penalty


def _ad_like_penalty(words: list[str], text: str) -> tuple[float, int]:
    lower_text = str(text or "").lower()
    token_hits = sum(1 for word in set(words) if word in AD_LIKE_TOKENS)
    phrase_hits = sum(
        1
        for phrase in ("kod promo", "link w opisie", "material sponsorowany", "partnerem odcinka")
        if phrase in lower_text
    )
    return clamp(token_hits * 0.3 + phrase_hits * 0.15), token_hits + phrase_hits


def _podcast_dialogue_payoff_score(
    sentences: list[str],
    words: list[str],
    *,
    speaker_switches: int,
    hook_score: float,
    payoff_score: float,
) -> tuple[float, int, int]:
    question_present = any("?" in sentence for sentence in sentences[:3])
    dialogue_hits = sum(1 for word in words if word in PODCAST_DIALOGUE_TOKENS)
    score = (
        (0.30 if question_present else 0.0)
        + clamp(speaker_switches / 2.0) * 0.25
        + clamp(dialogue_hits / 3.0) * 0.20
        + hook_score * 0.10
        + payoff_score * 0.15
    )
    return clamp(score), dialogue_hits, int(question_present)


def _contextless_penalty(
    sentences: list[str],
    *,
    hook_score: float,
    payoff_score: float,
    boundary_start_penalty: float,
    speaker_switches: int,
) -> float:
    if not sentences:
        return 0.0
    first_words = tokenize(sentences[0])
    first_word = first_words[0] if first_words else ""
    penalty = 0.0
    if first_word in CONTEXTLESS_START_TOKENS and hook_score < 0.45:
        penalty += 0.22
    if boundary_start_penalty >= 0.3 and hook_score < 0.45:
        penalty += 0.18
    if payoff_score < 0.25:
        penalty += 0.10
    if speaker_switches <= 0 and len(sentences) >= 3:
        penalty += 0.08
    return clamp(penalty)


def _preamble_penalty(sentences: list[str], *, hook_score: float) -> float:
    if not sentences:
        return 0.0
    first_words = tokenize(sentences[0])
    if first_words and first_words[0] in TRANSITION_ONLY_TOKENS and hook_score < 0.35:
        return 0.14
    return 0.0


def _low_payoff_penalty(*, payoff_score: float, podcast_dialogue_payoff_score: float) -> float:
    if podcast_dialogue_payoff_score < 0.40 and payoff_score < 0.35:
        return 0.24
    return 0.0


def _build_reasons(features: dict[str, Any], score_weights: dict[str, float]) -> list[str]:
    reasons: list[str] = []
    heatmap_strength = max(features.get("heatmap_avg", 0.0), features.get("heatmap_peak", 0.0))
    if heatmap_strength >= 0.55 and (score_weights["heatmap_avg"] + score_weights["heatmap_peak"]) >= 0.12:
        reasons.append("strong heatmap support")

    candidates = [
        (features.get(key, 0.0) * max(0.0, score_weights.get(key, 0.0)), label)
        for key, label in REASON_LABELS.items()
    ]
    for contribution, label in sorted(candidates, reverse=True):
        if contribution <= 0.04:
            continue
        if label not in reasons:
            reasons.append(label)
        if len(reasons) >= 3:
            break

    if (
        features.get("podcast_dialogue_payoff_score", 0.0) >= 0.55
        and "contains stronger dialogue question/response shape" not in reasons
    ):
        reasons.append("contains stronger dialogue question/response shape")
    if features.get("repetition_penalty", 0.0) >= 0.45:
        reasons.append("penalized for repetitive or filler-heavy wording")
    if features.get("ad_like_penalty", 0.0) >= 0.25:
        reasons.append("penalized for ad/sponsor-like wording")
    if features.get("low_payoff_penalty", 0.0) >= 0.20:
        reasons.append("penalized for weak payoff")
    if features.get("contextless_penalty", 0.0) >= 0.20:
        reasons.append("penalized for weak context")
    return reasons


def score_candidate(
    candidate: dict[str, Any],
    transcript_segments: list[dict[str, Any]],
    heatmap: list[dict[str, Any]],
    *,
    score_weights: dict[str, float] | None = None,
    strategy_name: str = "podcast",
) -> dict[str, Any]:
    strategy_name = normalize_scoring_strategy(strategy_name)
    weights = resolve_score_weights(score_weights)
    start = float(candidate["start"])
    end = float(candidate["end"])
    duration = max(0.01, float(candidate.get("duration", end - start)))
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
    boundary_completeness_score, boundary_start_penalty, boundary_end_penalty = _boundary_completeness_score(sentences)
    ad_like_penalty, ad_like_hits = _ad_like_penalty(words, text)
    podcast_dialogue_payoff_score, podcast_dialogue_hits, podcast_question_present = _podcast_dialogue_payoff_score(
        sentences,
        words,
        speaker_switches=speaker_switches,
        hook_score=hook_score,
        payoff_score=payoff_score,
    )
    preamble_penalty = _preamble_penalty(sentences, hook_score=hook_score)
    contextless_penalty = _contextless_penalty(
        sentences,
        hook_score=hook_score,
        payoff_score=payoff_score,
        boundary_start_penalty=boundary_start_penalty,
        speaker_switches=speaker_switches,
    )
    low_payoff_penalty = _low_payoff_penalty(
        payoff_score=payoff_score,
        podcast_dialogue_payoff_score=podcast_dialogue_payoff_score,
    )

    weighted_score = (
        heatmap_avg * weights["heatmap_avg"]
        + heatmap_peak * weights["heatmap_peak"]
        + importance_score * weights["importance_score"]
        + speech_density_score * weights["speech_density_score"]
        + emotion_score * weights["emotion_score"]
        + punchiness_score * weights["punchiness_score"]
        + hook_score * weights["hook_score"]
        + payoff_score * weights["payoff_score"]
        + speaker_turn_score * weights["speaker_turn_score"]
        + duration_fit_score * weights["duration_fit_score"]
        + chaos_score * weights["chaos_score"]
        - repetition_penalty * weights["repetition_penalty"]
    )
    weighted_score += (boundary_completeness_score - 0.5) * 0.08
    weighted_score += podcast_dialogue_payoff_score * 0.05
    weighted_score -= ad_like_penalty * 0.12
    weighted_score -= low_payoff_penalty * 0.12
    weighted_score -= contextless_penalty * 0.10
    weighted_score -= preamble_penalty * 0.08
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
        "boundary_completeness_score": round(boundary_completeness_score, 4),
        "boundary_start_penalty": round(boundary_start_penalty, 4),
        "boundary_end_penalty": round(boundary_end_penalty, 4),
        "ad_like_penalty": round(ad_like_penalty, 4),
        "ad_like_hits": ad_like_hits,
        "podcast_dialogue_payoff_score": round(podcast_dialogue_payoff_score, 4),
        "podcast_dialogue_hits": podcast_dialogue_hits,
        "podcast_question_present": podcast_question_present,
        "setup_penalty": 0.0,
        "setup_hits": 0,
        "preamble_penalty": round(preamble_penalty, 4),
        "contextless_penalty": round(contextless_penalty, 4),
        "low_payoff_penalty": round(low_payoff_penalty, 4),
        "segment_count": len(window_segments),
    }

    scored = dict(candidate)
    scored["local_score"] = local_score
    scored["local_features"] = features
    scored["selection_reasons"] = _build_reasons(features, weights)
    scored["selection_source"] = "local_scoring"
    scored["selection_strategy"] = strategy_name
    return scored


def score_candidates(
    candidates: list[dict[str, Any]],
    transcript: list[dict[str, Any]] | dict[str, Any],
    heatmap: list[dict[str, Any]],
    *,
    score_weights: dict[str, float] | None = None,
    strategy_name: str = "podcast",
) -> list[dict[str, Any]]:
    strategy_name = normalize_scoring_strategy(strategy_name)
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
    scored.sort(
        key=lambda item: (
            float(item.get("local_score", 0.0)),
            float(item.get("avg_value", 0.0)),
            -float(item.get("duration", 0.0)),
        ),
        reverse=True,
    )
    for index, candidate in enumerate(scored, start=1):
        candidate["local_rank"] = index
    return scored
