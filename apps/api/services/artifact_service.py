from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import Clip
from ..db.repositories import ArtifactRepository


VIDEO_MEDIA_TYPE = "video/mp4"


def create_artifact(
    session: Session,
    *,
    project_id: int,
    artifact_type: str,
    path: str,
    clip_id: int | None = None,
    media_type: str | None = None,
) -> dict[str, Any]:
    artifact = ArtifactRepository(session).create(
        project_id=project_id,
        clip_id=clip_id,
        artifact_type=artifact_type,
        path=path,
        filename=Path(path).name,
        media_type=media_type,
    )
    return {
        "id": artifact.id,
        "project_id": artifact.project_id,
        "clip_id": artifact.clip_id,
        "artifact_type": artifact.artifact_type,
        "path": artifact.path,
        "filename": artifact.filename,
        "media_type": artifact.media_type,
        "created_at": artifact.created_at.isoformat(),
    }


def record_render_artifacts(session: Session, clip: Clip, render_result: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for raw_output in render_result.get("raw_outputs") or []:
        artifacts.append(
            create_artifact(
                session,
                project_id=clip.project_id,
                clip_id=clip.id,
                artifact_type="raw_clip",
                path=str(raw_output),
                media_type=VIDEO_MEDIA_TYPE,
            )
        )
    for subtitled_output in render_result.get("subtitled_outputs") or []:
        artifacts.append(
            create_artifact(
                session,
                project_id=clip.project_id,
                clip_id=clip.id,
                artifact_type="subtitled_clip",
                path=str(subtitled_output),
                media_type=VIDEO_MEDIA_TYPE,
            )
        )
    return artifacts
