from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROJECT_ID = "local"
PROJECTS_DIR = PROJECT_ROOT / "data" / "projects"
STATE_FILENAME = "project_state.json"


class ProjectStateError(RuntimeError):
    """Raised when the local editor project state cannot be read or written."""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def project_dir(project_id: str = DEFAULT_PROJECT_ID, project_root: Path = PROJECT_ROOT) -> Path:
    safe_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in str(project_id or DEFAULT_PROJECT_ID))
    return project_root / "data" / "projects" / (safe_id or DEFAULT_PROJECT_ID)


def project_state_path(project_id: str = DEFAULT_PROJECT_ID, project_root: Path = PROJECT_ROOT) -> Path:
    return project_dir(project_id, project_root) / STATE_FILENAME


def default_project_state(project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any]:
    timestamp = now_iso()
    return {
        "schema_version": 1,
        "project_id": project_id,
        "product": "podcast_shorts_cutter",
        "created_at": timestamp,
        "updated_at": timestamp,
        "source": {
            "url": "",
            "video_path": "",
            "audio_path": "",
            "metadata_path": "",
        },
        "artifacts": {
            "transcript_path": "transcripts/final_transcript.json",
            "heatmap_path": "metadata/heatmap.json",
            "content_profile_path": "metadata/content_profile.json",
            "cutting_logic_path": "metadata/cutting_logic.json",
            "candidate_source_path": "",
        },
        "clips": [],
        "render_jobs": [],
    }


def load_project_state(
    project_id: str = DEFAULT_PROJECT_ID,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    path = project_state_path(project_id, project_root)
    if not path.exists():
        return default_project_state(project_id)
    try:
        with open(path, "r", encoding="utf-8") as file_handle:
            payload = json.load(file_handle)
    except json.JSONDecodeError as exc:
        raise ProjectStateError(f"Could not parse {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProjectStateError(f"{path} must contain a JSON object.")
    payload.setdefault("project_id", project_id)
    payload.setdefault("product", "podcast_shorts_cutter")
    payload.setdefault("clips", [])
    payload.setdefault("render_jobs", [])
    payload.setdefault("artifacts", {})
    payload.setdefault("source", {})
    return payload


def save_project_state(
    state: dict[str, Any],
    project_id: str | None = None,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    resolved_project_id = str(project_id or state.get("project_id") or DEFAULT_PROJECT_ID)
    state["project_id"] = resolved_project_id
    state.setdefault("created_at", now_iso())
    state["updated_at"] = now_iso()
    path = project_state_path(resolved_project_id, project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file_handle:
        json.dump(state, file_handle, ensure_ascii=False, indent=2)
        file_handle.write("\n")
    return state


def relative_to_project_root(path: Path, project_root: Path = PROJECT_ROOT) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def append_render_job(
    state: dict[str, Any],
    *,
    clip_id: str,
    status: str,
    output_dir: str,
    raw_outputs: list[str],
    subtitled_outputs: list[str],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    job = {
        "clip_id": clip_id,
        "status": status,
        "output_dir": output_dir,
        "raw_outputs": list(raw_outputs),
        "subtitled_outputs": list(subtitled_outputs),
        "warnings": list(warnings or []),
        "created_at": now_iso(),
    }
    state.setdefault("render_jobs", []).append(job)
    return job
