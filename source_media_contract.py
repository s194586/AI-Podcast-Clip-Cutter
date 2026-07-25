from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath
from typing import Any


SCHEMA_VERSION = 1
SOURCE = "youtube"


class SourceMediaManifestError(RuntimeError):
    """The existing source media cannot be safely associated with its origin."""


def _non_empty_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SourceMediaManifestError(
            f"Source media manifest is missing a valid {field}."
        )
    return value


def build_source_media_manifest(
    *,
    video_id: str,
    source_url: str,
    media_file: str | Path,
    workspace_path: str | Path,
) -> dict[str, Any]:
    payload = {"video_id": video_id, "source_url": source_url}
    normalized_video_id = _non_empty_string(payload, "video_id")
    normalized_source_url = _non_empty_string(payload, "source_url")
    workspace = Path(workspace_path).resolve()
    media_path = Path(media_file).resolve()
    if not media_path.is_file():
        raise SourceMediaManifestError("Source media file does not exist.")
    try:
        relative_media_path = media_path.relative_to(workspace)
    except ValueError as exc:
        raise SourceMediaManifestError(
            "Source media file is outside the project workspace."
        ) from exc
    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "video_id": normalized_video_id,
        "source_url": normalized_source_url,
        "media_file": relative_media_path.as_posix(),
    }


def load_source_media_manifest(
    path: str | Path,
    *,
    workspace_path: str | Path,
    existing_video: str | Path,
) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as file_handle:
            payload = json.load(file_handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceMediaManifestError(
            "Source media manifest is missing, unreadable, or contains invalid JSON."
        ) from exc
    if not isinstance(payload, dict):
        raise SourceMediaManifestError("Source media manifest must be an object.")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise SourceMediaManifestError("Unsupported source media manifest schema_version.")
    if payload.get("source") != SOURCE:
        raise SourceMediaManifestError("Source media manifest has an invalid source.")

    video_id = _non_empty_string(payload, "video_id")
    source_url = _non_empty_string(payload, "source_url")
    media_file = _non_empty_string(payload, "media_file")
    relative_media_file = Path(media_file)
    if relative_media_file.is_absolute() or PureWindowsPath(media_file).is_absolute():
        raise SourceMediaManifestError("Source media manifest media_file must be relative.")

    workspace = Path(workspace_path).resolve()
    resolved_media_file = (workspace / relative_media_file).resolve()
    try:
        resolved_media_file.relative_to(workspace)
    except ValueError as exc:
        raise SourceMediaManifestError(
            "Source media manifest media_file is outside the project workspace."
        ) from exc
    if not resolved_media_file.is_file():
        raise SourceMediaManifestError(
            "Source media manifest media_file does not point to an existing file."
        )
    if resolved_media_file != Path(existing_video).resolve():
        raise SourceMediaManifestError(
            "Source media manifest does not match the existing media file."
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "video_id": video_id,
        "source_url": source_url,
        "media_file": relative_media_file.as_posix(),
    }
