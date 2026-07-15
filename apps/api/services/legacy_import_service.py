from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import Project
from ..db.repositories import ArtifactRepository, ClipRepository, ProjectRepository
from .clips import ClipValidationError, _load_clips_from_candidate_files, _normalize_project_clip
from .project_state import DEFAULT_PROJECT_ID, PROJECT_ROOT, load_project_state, project_state_path


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _artifact_filename(path: str) -> str:
    return Path(path).name


def _create_artifact_if_present(
    artifact_repo: ArtifactRepository,
    *,
    project_id: int,
    artifact_type: str,
    path: Any,
    clip_id: int | None = None,
    media_type: str | None = None,
) -> None:
    if not path:
        return
    artifact_repo.create(
        project_id=project_id,
        clip_id=clip_id,
        artifact_type=artifact_type,
        path=str(path),
        filename=_artifact_filename(str(path)),
        media_type=media_type,
    )


def _apply_project_timestamps(project: Project, payload: dict[str, Any]) -> None:
    created_at = _parse_datetime(payload.get("created_at"))
    updated_at = _parse_datetime(payload.get("updated_at"))
    if created_at is not None:
        project.created_at = created_at
    if updated_at is not None:
        project.updated_at = updated_at


def import_project_state(
    session: Session,
    *,
    project_root: Path = PROJECT_ROOT,
    project_id: str = DEFAULT_PROJECT_ID,
) -> Project | None:
    path = project_state_path(project_id, project_root)
    if not path.exists():
        return None

    state = load_project_state(project_id, project_root)
    source = state.get("source") if isinstance(state.get("source"), dict) else {}
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    state_clips = state.get("clips") if isinstance(state.get("clips"), list) else []

    project = ProjectRepository(session).create(
        source_url=str(source.get("url") or state.get("source_url") or ""),
        title=str(state.get("title") or "Local podcast project"),
        status=str(state.get("status") or ("ready" if state_clips else "draft")),
        source_video_path=str(source.get("video_path") or "") or None,
        transcript_path=str(artifacts.get("transcript_path") or "") or None,
        candidate_source_path=str(artifacts.get("candidate_source_path") or "") or None,
    )
    _apply_project_timestamps(project, state)

    clip_repo = ClipRepository(session)
    artifact_repo = ArtifactRepository(session)

    _create_artifact_if_present(
        artifact_repo,
        project_id=project.id,
        artifact_type="source_video",
        path=project.source_video_path,
        media_type="video/mp4",
    )
    _create_artifact_if_present(
        artifact_repo,
        project_id=project.id,
        artifact_type="transcript",
        path=project.transcript_path,
        media_type="application/json",
    )
    _create_artifact_if_present(
        artifact_repo,
        project_id=project.id,
        artifact_type="candidate_windows",
        path=project.candidate_source_path,
        media_type="application/json",
    )

    for index, clip_payload in enumerate(state_clips, start=1):
        if not isinstance(clip_payload, dict):
            continue
        clip = clip_repo.create_from_dict(project.id, _normalize_project_clip(clip_payload, index))
        for raw_output in clip.raw_outputs or []:
            _create_artifact_if_present(
                artifact_repo,
                project_id=project.id,
                clip_id=clip.id,
                artifact_type="raw_clip",
                path=raw_output,
                media_type="video/mp4",
            )
        for subtitled_output in clip.subtitled_outputs or []:
            _create_artifact_if_present(
                artifact_repo,
                project_id=project.id,
                clip_id=clip.id,
                artifact_type="subtitled_clip",
                path=subtitled_output,
                media_type="video/mp4",
            )

    return project


def import_candidate_windows(session: Session, *, project_root: Path = PROJECT_ROOT) -> Project | None:
    try:
        clips, source = _load_clips_from_candidate_files(project_root)
    except ClipValidationError:
        return None

    project = ProjectRepository(session).create(
        source_url="",
        title="Local podcast project",
        status="ready" if clips else "draft",
        candidate_source_path=source,
    )
    artifact_repo = ArtifactRepository(session)
    _create_artifact_if_present(
        artifact_repo,
        project_id=project.id,
        artifact_type="candidate_windows",
        path=source,
        media_type="application/json",
    )

    clip_repo = ClipRepository(session)
    for clip_payload in clips:
        clip_repo.create_from_dict(project.id, clip_payload)
    return project


def bootstrap_legacy_state_if_needed(
    session: Session,
    *,
    project_root: Path = PROJECT_ROOT,
    project_id: str = DEFAULT_PROJECT_ID,
) -> Project | None:
    project_repo = ProjectRepository(session)
    if project_repo.count() > 0:
        return project_repo.get_default()

    imported = import_project_state(session, project_root=project_root, project_id=project_id)
    if imported is not None:
        return imported
    return import_candidate_windows(session, project_root=project_root)
