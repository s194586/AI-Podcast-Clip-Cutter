from __future__ import annotations


VALID_CONTENT_TYPES = ("podcast",)
VALID_CONTENT_TYPE_MODES = ("auto",) + VALID_CONTENT_TYPES


def normalize_content_type_mode(value: str | None, default: str = "auto") -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in VALID_CONTENT_TYPE_MODES:
        raise ValueError(
            f"Unsupported content type for the podcast-only product: {value}. "
            f"Expected one of: {', '.join(VALID_CONTENT_TYPE_MODES)}"
        )
    return normalized
