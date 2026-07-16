from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context import DEFAULT_REVIEW_CONTEXT_SECONDS
from .providers import DEFAULT_GEMINI_MODEL, LOCAL_STUB_MODEL
from .schemas import ReviewMode


DEFAULT_REVIEW_MODE: ReviewMode = "local_stub"
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ReviewConfigError(RuntimeError):
    """Raised when review provider configuration is invalid."""


@dataclass(frozen=True)
class ReviewConfig:
    mode: ReviewMode
    gemini_model: str
    context_seconds: float
    api_key: str | None = field(default=None, repr=False)
    mode_source: str = "default"
    model_source: str = "default"
    context_seconds_source: str = "default"
    api_key_source: str = "unset"
    env_path: Path | None = None
    warnings: tuple[str, ...] = ()

    @property
    def provider(self) -> str:
        return self.mode

    @property
    def model(self) -> str:
        return self.gemini_model if self.mode == "gemini" else LOCAL_STUB_MODEL

    @property
    def api_key_configured(self) -> bool:
        return bool(str(self.api_key or "").strip())

    def require_ready(self) -> None:
        if self.mode == "gemini" and not self.api_key_configured:
            raise ReviewConfigError(
                "CLIP_REVIEW_MODE=gemini requires GEMINI_API_KEY. Set GEMINI_API_KEY to enable real Gemini review."
            )

    def safe_summary(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "mode": self.mode,
            "model": self.model,
            "gemini_model": self.gemini_model,
            "context_seconds": self.context_seconds,
            "mode_source": self.mode_source,
            "model_source": self.model_source,
            "context_seconds_source": self.context_seconds_source,
            "gemini_api_key_configured": self.api_key_configured,
            "gemini_api_key_source": self.api_key_source,
            "env_path": str(self.env_path) if self.env_path else None,
            "warnings": list(self.warnings),
        }


def load_review_config(
    *,
    project_root: Path | str | None = None,
    mode: str | None = None,
    require_api_key: bool = True,
) -> ReviewConfig:
    root = Path(project_root).resolve() if project_root is not None else DEFAULT_PROJECT_ROOT
    env_path = root / ".env"
    dotenv_values = _read_dotenv(env_path)
    warnings = _override_warnings(dotenv_values)

    raw_mode, mode_source = _resolve_value(
        "CLIP_REVIEW_MODE",
        dotenv_values,
        explicit_value=mode,
        explicit_source="explicit",
        default_value=DEFAULT_REVIEW_MODE,
    )
    normalized_mode = normalize_review_mode_value(raw_mode)

    raw_model, model_source = _resolve_value(
        "GEMINI_MODEL",
        dotenv_values,
        default_value=DEFAULT_GEMINI_MODEL,
    )
    gemini_model = str(raw_model or DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL

    raw_context, context_source = _resolve_value(
        "CLIP_REVIEW_CONTEXT_SECONDS",
        dotenv_values,
        default_value=str(DEFAULT_REVIEW_CONTEXT_SECONDS),
    )
    context_seconds = _parse_context_seconds(raw_context)

    api_key, api_key_source = _resolve_value(
        "GEMINI_API_KEY",
        dotenv_values,
        default_value="",
    )
    stripped_key = str(api_key or "").strip() or None

    config = ReviewConfig(
        mode=normalized_mode,
        gemini_model=gemini_model,
        context_seconds=context_seconds,
        api_key=stripped_key,
        mode_source=mode_source,
        model_source=model_source,
        context_seconds_source=context_source,
        api_key_source=api_key_source if stripped_key else "unset",
        env_path=env_path if env_path.exists() else None,
        warnings=tuple(warnings),
    )
    if require_api_key:
        config.require_ready()
    return config


def normalize_review_mode_value(value: str | None) -> ReviewMode:
    raw_value = str(value or DEFAULT_REVIEW_MODE).strip().lower()
    aliases = {
        "local_only": "local_stub",
        "stub": "local_stub",
    }
    normalized = aliases.get(raw_value, raw_value)
    if normalized not in {"local_stub", "gemini"}:
        raise ReviewConfigError(
            f"Unsupported CLIP_REVIEW_MODE={raw_value!r}. Use 'local_stub' or 'gemini'."
        )
    return normalized  # type: ignore[return-value]


def safe_review_config_summary(*, project_root: Path | str | None = None) -> dict[str, Any]:
    try:
        return load_review_config(project_root=project_root, require_api_key=False).safe_summary()
    except ReviewConfigError as exc:
        return {
            "provider": "invalid",
            "mode": "invalid",
            "model": None,
            "gemini_model": None,
            "context_seconds": None,
            "gemini_api_key_configured": False,
            "configuration_error": str(exc),
            "warnings": [],
        }


def _resolve_value(
    name: str,
    dotenv_values: Mapping[str, str | None],
    *,
    explicit_value: str | None = None,
    explicit_source: str = "explicit",
    default_value: str,
) -> tuple[str, str]:
    if explicit_value is not None and str(explicit_value).strip():
        return str(explicit_value), explicit_source
    env_value = os.environ.get(name)
    if env_value is not None and str(env_value).strip():
        return str(env_value), "environment"
    dotenv_value = dotenv_values.get(name)
    if dotenv_value is not None and str(dotenv_value).strip():
        return str(dotenv_value), ".env"
    return default_value, "default"


def _parse_context_seconds(value: str | None) -> float:
    raw_value = str(value or DEFAULT_REVIEW_CONTEXT_SECONDS).strip()
    try:
        return max(0.0, float(raw_value))
    except ValueError as exc:
        raise ReviewConfigError(
            f"CLIP_REVIEW_CONTEXT_SECONDS must be a number, got {raw_value!r}."
        ) from exc


def _override_warnings(dotenv_values: Mapping[str, str | None]) -> list[str]:
    warnings: list[str] = []
    for name in ("CLIP_REVIEW_MODE", "GEMINI_MODEL", "CLIP_REVIEW_CONTEXT_SECONDS", "GEMINI_API_KEY"):
        env_value = os.environ.get(name)
        dotenv_value = dotenv_values.get(name)
        if not str(env_value or "").strip() or not str(dotenv_value or "").strip():
            continue
        if name == "GEMINI_API_KEY":
            if str(env_value).strip() != str(dotenv_value).strip():
                warnings.append("Process environment GEMINI_API_KEY overrides .env GEMINI_API_KEY.")
            continue
        if str(env_value).strip() != str(dotenv_value).strip():
            warnings.append(f"Process environment {name}={env_value!r} overrides .env {name}.")
    return warnings


def _read_dotenv(path: Path) -> dict[str, str | None]:
    if not path.exists():
        return {}
    try:
        from dotenv import dotenv_values

        return dict(dotenv_values(path))
    except Exception:
        return _read_dotenv_manually(path)


def _read_dotenv_manually(path: Path) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        values[key] = value
    return values
