from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db.database import init_database, session_scope
from ..db.models import Artifact
from ..db.repositories import ArtifactRepository, ProjectRepository
from .project_service import PROJECT_ROOT, ProjectNotFoundError, get_project_workspace_root


class ExportNotFoundError(LookupError):
    pass


class ExportAccessError(ValueError):
    pass


EXPORT_ARTIFACT_TYPES = {"raw_clip", "subtitled_clip"}


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _artifact_file_path(artifact: Artifact, *, project_id: int, project_root: Path) -> Path:
    workspace_root = get_project_workspace_root(project_id, project_root=project_root).resolve()
    stored_path = Path(artifact.path)
    candidate = stored_path.resolve() if stored_path.is_absolute() else (project_root / stored_path).resolve()
    try:
        candidate.relative_to(workspace_root)
    except ValueError as exc:
        raise ExportAccessError("Export artifact is outside the project workspace.") from exc
    if not candidate.is_file():
        raise ExportNotFoundError("Export file is missing.")
    return candidate


def _artifact_to_metadata(artifact: Artifact, *, project_id: int, project_root: Path) -> dict[str, Any]:
    file_path = _artifact_file_path(artifact, project_id=project_id, project_root=project_root)
    clip = artifact.clip
    duration = None
    if clip is not None:
        duration = round(float(clip.edited_end) - float(clip.edited_start), 2)
    return {
        "id": artifact.id,
        "project_id": artifact.project_id,
        "clip_id": clip.external_id if clip is not None else None,
        "clip_database_id": artifact.clip_id,
        "clip_index": clip.clip_index if clip is not None else None,
        "artifact_type": artifact.artifact_type,
        "filename": artifact.filename or file_path.name,
        "media_type": artifact.media_type or "application/octet-stream",
        "created_at": _iso(artifact.created_at),
        "duration": duration,
        "file_size": file_path.stat().st_size,
        "download_url": f"/projects/{project_id}/exports/{artifact.id}/download",
        "preview_url": f"/projects/{project_id}/exports/{artifact.id}/download",
    }


def list_project_exports(project_id: int, *, project_root: Path = PROJECT_ROOT) -> list[dict[str, Any]]:
    init_database()
    with session_scope() as session:
        project = ProjectRepository(session).get(project_id)
        if project is None:
            raise ProjectNotFoundError(f"Unknown project_id: {project_id}")
        artifacts = ArtifactRepository(session).list_for_project(project_id)
        exports = []
        for artifact in artifacts:
            if artifact.artifact_type not in EXPORT_ARTIFACT_TYPES:
                continue
            try:
                exports.append(_artifact_to_metadata(artifact, project_id=project_id, project_root=project_root))
            except ExportNotFoundError:
                continue
        return sorted(exports, key=lambda item: (item.get("created_at") or "", item.get("id") or 0), reverse=True)


def get_project_export_file(
    project_id: int,
    artifact_id: int,
    *,
    project_root: Path = PROJECT_ROOT,
) -> tuple[Path, str, str]:
    init_database()
    with session_scope() as session:
        project = ProjectRepository(session).get(project_id)
        if project is None:
            raise ProjectNotFoundError(f"Unknown project_id: {project_id}")
        artifact = session.get(Artifact, artifact_id)
        if artifact is None or artifact.project_id != project_id or artifact.artifact_type not in EXPORT_ARTIFACT_TYPES:
            raise ExportNotFoundError(f"Unknown export artifact_id: {artifact_id}")
        file_path = _artifact_file_path(artifact, project_id=project_id, project_root=project_root)
        return file_path, artifact.filename or file_path.name, artifact.media_type or "application/octet-stream"
