AI_MODE_LOCAL_ONLY = "local_only"
AI_MODE_GEMINI_OPTIONAL = "gemini_optional"
AI_MODE_GEMINI_ENABLED = "gemini_enabled"

VALID_AI_MODES = (
    AI_MODE_LOCAL_ONLY,
    AI_MODE_GEMINI_OPTIONAL,
    AI_MODE_GEMINI_ENABLED,
)

SUBTITLE_CHECKER_MODE_OFF = "off"
SUBTITLE_CHECKER_MODE_LOCAL_ONLY = "local_only"
SUBTITLE_CHECKER_MODE_LIMITED = "limited"
SUBTITLE_CHECKER_MODE_FULL = "full"

VALID_SUBTITLE_CHECKER_MODES = (
    SUBTITLE_CHECKER_MODE_OFF,
    SUBTITLE_CHECKER_MODE_LOCAL_ONLY,
    SUBTITLE_CHECKER_MODE_LIMITED,
    SUBTITLE_CHECKER_MODE_FULL,
)


def normalize_ai_mode(value, default=AI_MODE_GEMINI_OPTIONAL):
    normalized = str(value or default).strip().lower()
    if normalized not in VALID_AI_MODES:
        raise ValueError(
            f"Unsupported ai mode: {value}. Expected one of: {', '.join(VALID_AI_MODES)}"
        )
    return normalized


def allows_gemini(mode):
    return normalize_ai_mode(mode) != AI_MODE_LOCAL_ONLY


def requires_gemini(mode):
    return normalize_ai_mode(mode) == AI_MODE_GEMINI_ENABLED


def normalize_subtitle_checker_mode(value, default=SUBTITLE_CHECKER_MODE_LOCAL_ONLY):
    normalized = str(value or default).strip().lower()
    if normalized not in VALID_SUBTITLE_CHECKER_MODES:
        raise ValueError(
            "Unsupported subtitle checker mode: "
            f"{value}. Expected one of: {', '.join(VALID_SUBTITLE_CHECKER_MODES)}"
        )
    return normalized


def default_subtitle_checker_mode(ai_mode):
    ai_mode = normalize_ai_mode(ai_mode)
    if ai_mode == AI_MODE_GEMINI_ENABLED:
        return SUBTITLE_CHECKER_MODE_LIMITED
    return SUBTITLE_CHECKER_MODE_LOCAL_ONLY


def subtitle_checker_uses_ai(mode):
    mode = normalize_subtitle_checker_mode(mode)
    return mode in {SUBTITLE_CHECKER_MODE_LIMITED, SUBTITLE_CHECKER_MODE_FULL}


def subtitle_checker_sample_limit(mode, default_full_samples=8):
    mode = normalize_subtitle_checker_mode(mode)
    if mode in {SUBTITLE_CHECKER_MODE_OFF, SUBTITLE_CHECKER_MODE_LOCAL_ONLY}:
        return 0
    if mode == SUBTITLE_CHECKER_MODE_LIMITED:
        return 2
    return max(1, int(default_full_samples))
