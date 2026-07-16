from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from ..db.database import configured_database_path, init_database, session_scope
from ..db.models import Project
from ..db.repositories import JobRepository, ProjectRepository
from .clip_service import clip_to_dict
from .project_state import DEFAULT_PROJECT_ID, PROJECT_ROOT, default_project_state, project_state_path


class ProjectNotFoundError(LookupError):
    pass


class ProjectValidationError(ValueError):
    pass


STAGE_MESSAGES = {
    "waiting": "Waiting to start",
    "downloading": "Downloading source media",
    "transcribing": "Transcribing podcast",
    "validating_transcript": "Validating transcript",
    "generating_candidates": "Generating candidate clips",
    "importing_candidates": "Importing candidate clips",
    "reviewing_with_ai": "Reviewing boundaries with AI",
    "ready": "Ready for review",
    "failed": "Failed",
    "cancelled": "Cancelled",
}
MEDIA_FORMAT_VARIANT_RE = re.compile(r"\.f\d+$", re.IGNORECASE)
PROJECT_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm"}


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _effective_stage_and_progress(
    project: Project,
    *,
    project_repo: ProjectRepository | None = None,
    clip_count: int | None = None,
) -> tuple[str, float]:
    stage = project.current_stage or "waiting"
    progress = float(project.progress_percent or 0.0)
    if clip_count is None and project_repo is not None:
        clip_count = project_repo.clip_count(project.id)
    if project.status == "ready" and (clip_count or 0) > 0:
        if stage in {"", "waiting"}:
            stage = "ready"
        if progress < 100.0:
            progress = 100.0
    return stage, progress


def project_to_dict(project: Project, *, include_counts: bool = False, project_repo: ProjectRepository | None = None) -> dict[str, Any]:
    clip_count = project_repo.clip_count(project.id) if include_counts and project_repo is not None else None
    stage, progress = _effective_stage_and_progress(project, project_repo=project_repo, clip_count=clip_count)
    payload: dict[str, Any] = {
        "id": project.id,
        "title": project.title,
        "source_url": project.source_url,
        "status": project.status,
        "current_stage": stage,
        "stage": stage,
        "progress_percent": progress,
        "workspace_path": project.workspace_path,
        "error_message": project.error_message,
        "auto_review": bool(project.auto_review),
        "source_video_path": project.source_video_path,
        "transcript_path": project.transcript_path,
        "candidate_source_path": project.candidate_source_path,
        "created_at": _iso(project.created_at),
        "started_at": _iso(project.started_at),
        "completed_at": _iso(project.completed_at),
        "updated_at": _iso(project.updated_at),
    }
    if include_counts and project_repo is not None:
        payload["clip_count"] = clip_count or 0
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


def validate_source_url(source_url: str) -> str:
    value = str(source_url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProjectValidationError("source_url must be an absolute http(s) URL.")
    if len(value) > 2048:
        raise ProjectValidationError("source_url is too long.")
    return value


def project_workspace_path(project_id: int, *, project_root: Path = PROJECT_ROOT) -> Path:
    return Path(project_root) / "data" / "projects" / str(int(project_id)) / "workspace"


def ensure_project_workspace(project_id: int, *, project_root: Path = PROJECT_ROOT) -> Path:
    workspace = project_workspace_path(project_id, project_root=project_root)
    for relative in (
        "input",
        "metadata",
        "transcripts",
        "cuts/raw",
        "cuts/subtitles",
        "outputs",
        "logs",
    ):
        (workspace / relative).mkdir(parents=True, exist_ok=True)
    return workspace


def get_project_workspace_root(project_id: int, *, project_root: Path = PROJECT_ROOT) -> Path:
    init_database()
    with session_scope() as session:
        project = ProjectRepository(session).get(project_id)
        if project is None:
            raise ProjectNotFoundError(f"Unknown project_id: {project_id}")
        if project.workspace_path:
            stored_path = Path(project.workspace_path)
            path = stored_path.resolve() if stored_path.is_absolute() else (Path(project_root) / stored_path).resolve()
        else:
            path = project_workspace_path(project.id, project_root=project_root)
    return path


def get_project_source_video_path(project_id: int, *, project_root: Path = PROJECT_ROOT) -> Path | None:
    init_database()
    with session_scope() as session:
        project = ProjectRepository(session).get(project_id)
        if project is None:
            raise ProjectNotFoundError(f"Unknown project_id: {project_id}")
        workspace = _workspace_path_for_project(project, project_root=project_root)
        stored_path = project.source_video_path

    preferred_workspace_video = _preferred_workspace_video(workspace)

    if stored_path:
        candidate = Path(stored_path)
        resolved = candidate.resolve() if candidate.is_absolute() else (Path(project_root) / candidate).resolve()
        if resolved.is_file() and resolved.suffix.lower() in PROJECT_VIDEO_EXTENSIONS:
            if _is_media_format_variant(resolved) and preferred_workspace_video is not None:
                return preferred_workspace_video
            return resolved

    return preferred_workspace_video


def _is_media_format_variant(path: Path) -> bool:
    return bool(MEDIA_FORMAT_VARIANT_RE.search(path.stem))


def _preferred_video_candidate(candidates: list[Path]) -> Path | None:
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda path: (
            not _is_media_format_variant(path),
            path.suffix.lower() == ".mp4",
            path.stat().st_size,
            path.stat().st_mtime,
        ),
    )


def _preferred_workspace_video(workspace: Path) -> Path | None:
    input_dir = workspace / "input"
    if not input_dir.exists():
        return None
    candidates = [path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in PROJECT_VIDEO_EXTENSIONS]
    return _preferred_video_candidate(candidates)


def _workspace_path_for_project(project: Project, *, project_root: Path = PROJECT_ROOT) -> Path:
    if project.workspace_path:
        stored_path = Path(project.workspace_path)
        return stored_path.resolve() if stored_path.is_absolute() else (Path(project_root) / stored_path).resolve()
    return project_workspace_path(project.id, project_root=project_root)


def safe_relative_path(path: Path | str | None, *, project_root: Path = PROJECT_ROOT) -> str | None:
    if path in (None, ""):
        return None
    candidate = Path(path)
    try:
        return str(candidate.resolve().relative_to(Path(project_root).resolve())).replace("\\", "/")
    except ValueError:
        return str(candidate)


def create_project(
    *,
    source_url: str,
    title: str | None = None,
    auto_review: bool = True,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    validated_url = validate_source_url(source_url)
    init_database()
    with session_scope() as session:
        project = ProjectRepository(session).create(
            source_url=validated_url,
            title=str(title).strip() if title else None,
            status="created",
            current_stage="waiting",
            progress_percent=0.0,
            auto_review=bool(auto_review),
        )
        workspace = ensure_project_workspace(project.id, project_root=project_root)
        project.workspace_path = safe_relative_path(workspace, project_root=project_root)
        ProjectRepository(session).touch(project)
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
        latest_job = JobRepository(session).latest_for_project(project.id, "local_pipeline")
        raw_stage = project.current_stage or (latest_job.current_stage if latest_job else None) or "waiting"
        clip_count = project_repo.clip_count(project.id)
        stage, progress = _effective_stage_and_progress(project, project_repo=project_repo, clip_count=clip_count)
        if stage == "waiting" and raw_stage != "waiting":
            stage = raw_stage
            progress = float(project.progress_percent or 0.0)
        return {
            "project_id": project.id,
            "status": project.status,
            "stage": stage,
            "current_stage": stage,
            "progress_percent": progress,
            "message": STAGE_MESSAGES.get(stage, stage.replace("_", " ").title()),
            "error_message": project.error_message,
            "started_at": _iso(project.started_at),
            "updated_at": _iso(project.updated_at),
            "completed_at": _iso(project.completed_at),
            "clip_count": clip_count,
            "last_error": JobRepository(session).latest_failed_error(project.id),
            "job": _job_to_status_dict(latest_job) if latest_job is not None else None,
        }


def _job_to_status_dict(job) -> dict[str, Any]:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "stage": job.current_stage or job.stage,
        "progress_percent": float(job.progress or 0.0),
        "process_id": job.process_id,
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "exit_code": job.exit_code,
        "error_message": job.error_message,
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
