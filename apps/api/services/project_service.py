from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db.database import configured_database_path, init_database, session_scope
from ..db.models import Project
from ..db.repositories import JobRepository, ProjectRepository
from .clip_service import clip_to_dict
from .project_state import DEFAULT_PROJECT_ID, PROJECT_ROOT, default_project_state, project_state_path


class ProjectNotFoundError(LookupError):
    pass


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def project_to_dict(project: Project, *, include_counts: bool = False, project_repo: ProjectRepository | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": project.id,
        "title": project.title,
        "source_url": project.source_url,
        "status": project.status,
        "source_video_path": project.source_video_path,
        "transcript_path": project.transcript_path,
        "candidate_source_path": project.candidate_source_path,
        "created_at": _iso(project.created_at),
        "updated_at": _iso(project.updated_at),
    }
    if include_counts and project_repo is not None:
        payload["clip_count"] = project_repo.clip_count(project.id)
        payload["accepted_clip_count"] = project_repo.accepted_clip_count(project.id)
    return payload


def initialize_application_state(*, project_root: Path = PROJECT_ROOT) -> None:
    init_database()
    with session_scope() as session:
        from .legacy_import_service import bootstrap_legacy_state_if_needed, stale_demo_warning

        bootstrap_legacy_state_if_needed(session, project_root=project_root)
        warning = stale_demo_warning(session, project_root=project_root)
        if warning:
            print(warning)


def create_project(*, source_url: str, title: str | None = None) -> dict[str, Any]:
    init_database()
    with session_scope() as session:
        project = ProjectRepository(session).create(
            source_url=str(source_url or ""),
            title=str(title).strip() if title else None,
            status="draft",
        )
        return project_to_dict(project)


def list_projects() -> list[dict[str, Any]]:
    init_database()
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        return [
            project_to_dict(project, include_counts=True, project_repo=project_repo)
            for project in project_repo.list_newest()
        ]


def get_project(project_id: int) -> dict[str, Any]:
    init_database()
    with session_scope() as session:
        project = ProjectRepository(session).get(project_id)
        if project is None:
            raise ProjectNotFoundError(f"Unknown project_id: {project_id}")
        return project_to_dict(project)


def get_project_clips(project_id: int) -> list[dict[str, Any]]:
    init_database()
    with session_scope() as session:
        project = ProjectRepository(session).get(project_id)
        if project is None:
            raise ProjectNotFoundError(f"Unknown project_id: {project_id}")
        return [clip_to_dict(clip) for clip in project.clips]


def get_project_status(project_id: int) -> dict[str, Any]:
    init_database()
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        project = project_repo.get(project_id)
        if project is None:
            raise ProjectNotFoundError(f"Unknown project_id: {project_id}")
        return {
            "project_id": project.id,
            "status": project.status,
            "clip_count": project_repo.clip_count(project.id),
            "last_error": JobRepository(session).latest_failed_error(project.id),
        }


def get_default_project_manifest(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    init_database()
    with session_scope() as session:
        from .legacy_import_service import bootstrap_legacy_state_if_needed

        project_repo = ProjectRepository(session)
        project = project_repo.get_default()
        if project is None:
            project = bootstrap_legacy_state_if_needed(session, project_root=project_root)
        if project is None:
            state = default_project_state(DEFAULT_PROJECT_ID)
            state["schema_version"] = 2
            state["source_of_truth"] = "sqlite"
            return state

        state = default_project_state(DEFAULT_PROJECT_ID)
        state.update(
            {
                "schema_version": 2,
                "database_id": project.id,
                "title": project.title,
                "status": project.status,
                "created_at": _iso(project.created_at),
                "updated_at": _iso(project.updated_at),
                "source_of_truth": "sqlite",
            }
        )
        state["source"]["url"] = project.source_url or ""
        state["source"]["video_path"] = project.source_video_path or ""
        state["artifacts"]["transcript_path"] = project.transcript_path or ""
        state["artifacts"]["candidate_source_path"] = project.candidate_source_path or ""
        state["clips"] = [clip_to_dict(clip) for clip in project.clips]
        return state


def compatibility_project_response(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    database_path = configured_database_path()
    return {
        "project": get_default_project_manifest(project_root=project_root),
        "project_state_path": str(project_state_path(DEFAULT_PROJECT_ID, project_root)),
        "database_path": str(database_path) if database_path is not None else None,
        "default_project_resolution": "compatibility endpoints use the earliest SQLite project by database id",
    }
