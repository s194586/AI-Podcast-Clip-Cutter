from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..db.models import Artifact, Clip, ClipEvaluation, Job, Project
from ..db.repositories import ArtifactRepository, ClipRepository, ProjectRepository
from .clips import (
    ClipValidationError,
    _load_clips_from_candidate_files,
    _normalize_project_clip,
    _normalize_window,
    _read_json,
    _relative_source,
    extract_windows,
)
from .project_state import DEFAULT_PROJECT_ID, PROJECT_ROOT, load_project_state, project_state_path


REAL_CANDIDATE_PATHS = (
    Path("top_windows.json"),
    Path("metadata") / "top_windows.json",
    Path("metadata") / "cutting_logic.json",
)
DEMO_CANDIDATE_PATH = Path("examples") / "top_windows.example.json"
IMPORT_RESET_COMMAND = "python -m apps.api.tools.import_local_project --reset"
MEDIA_FORMAT_VARIANT_RE = re.compile(r"\.f\d+$", re.IGNORECASE)


@dataclass(frozen=True)
class LocalImportSource:
    source_type: str
    source_path: str
    clip_count: int
    is_demo: bool = False


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


def _project_state_clip_count(*, project_root: Path = PROJECT_ROOT, project_id: str = DEFAULT_PROJECT_ID) -> int:
    path = project_state_path(project_id, project_root)
    if not path.exists():
        return 0
    state = load_project_state(project_id, project_root)
    state_clips = state.get("clips") if isinstance(state.get("clips"), list) else []
    return len([clip for clip in state_clips if isinstance(clip, dict)])


def _load_clips_from_candidate_path(project_root: Path, path: Path) -> tuple[list[dict[str, Any]], str]:
    payload = _read_json(path)
    windows = extract_windows(payload)
    source = _relative_source(path, project_root)
    clips = [_normalize_window(window, index, source) for index, window in enumerate(windows, start=1)]
    clips = [clip for clip in clips if clip["duration"] > 0]
    if not clips:
        raise ClipValidationError(f"{source}: no valid clips")
    return clips, source


def _candidate_source_for_path(project_root: Path, relative_path: Path, *, is_demo: bool) -> LocalImportSource | None:
    path = project_root / relative_path
    if not path.exists():
        return None
    clips, source = _load_clips_from_candidate_path(project_root, path)
    return LocalImportSource(
        source_type="candidate_file",
        source_path=source,
        clip_count=len(clips),
        is_demo=is_demo,
    )


def find_local_import_source(
    *,
    project_root: Path = PROJECT_ROOT,
    project_id: str = DEFAULT_PROJECT_ID,
    allow_demo: bool = False,
) -> LocalImportSource | None:
    state_clip_count = _project_state_clip_count(project_root=project_root, project_id=project_id)
    if state_clip_count > 0:
        return LocalImportSource(
            source_type="project_state",
            source_path=_relative_source(project_state_path(project_id, project_root), project_root),
            clip_count=state_clip_count,
            is_demo=False,
        )

    real_file_exists = False
    for relative_path in REAL_CANDIDATE_PATHS:
        path = project_root / relative_path
        if not path.exists():
            continue
        real_file_exists = True
        try:
            return _candidate_source_for_path(project_root, relative_path, is_demo=False)
        except ClipValidationError:
            continue

    if allow_demo or not real_file_exists:
        try:
            return _candidate_source_for_path(project_root, DEMO_CANDIDATE_PATH, is_demo=True)
        except ClipValidationError:
            return None
    return None


def import_candidate_file(session: Session, *, project_root: Path, source_path: str) -> Project | None:
    path = project_root / source_path
    try:
        clips, source = _load_clips_from_candidate_path(project_root, path)
    except ClipValidationError:
        return None

    project = ProjectRepository(session).create(
        source_url="",
        title="Local podcast project",
        status="ready" if clips else "draft",
        candidate_source_path=source,
    )
    ArtifactRepository(session).create(
        project_id=project.id,
        artifact_type="candidate_windows",
        path=source,
        filename=Path(source).name,
        media_type="application/json",
    )

    clip_repo = ClipRepository(session)
    for clip_payload in clips:
        clip_repo.create_from_dict(project.id, clip_payload)
    return project


def import_candidate_file_into_project(
    session: Session,
    *,
    project_id: int,
    project_root: Path,
    workspace_root: Path,
    source_path: str = "top_windows.json",
) -> Project | None:
    workspace_root = Path(workspace_root)
    source_file = workspace_root / source_path
    try:
        clips, clip_source = _load_clips_from_candidate_path(workspace_root, source_file)
    except ClipValidationError:
        return None

    project_repo = ProjectRepository(session)
    project = project_repo.get(int(project_id))
    if project is None:
        return None

    candidate_path = _safe_relative_to_root(source_file, project_root)
    transcript_path = workspace_root / "transcripts" / "final_transcript.json"
    source_video = _find_first_media(workspace_root / "input")
    project.candidate_source_path = candidate_path
    project.transcript_path = _safe_relative_to_root(transcript_path, project_root) if transcript_path.exists() else None
    project.source_video_path = _safe_relative_to_root(source_video, project_root) if source_video is not None else None
    project_repo.touch(project)

    _replace_artifacts_for_type(session, project_id=project.id, artifact_type="candidate_windows")
    _create_artifact_if_present(
        ArtifactRepository(session),
        project_id=project.id,
        artifact_type="candidate_windows",
        path=candidate_path,
        media_type="application/json",
    )
    if project.transcript_path:
        _replace_artifacts_for_type(session, project_id=project.id, artifact_type="transcript")
        _create_artifact_if_present(
            ArtifactRepository(session),
            project_id=project.id,
            artifact_type="transcript",
            path=project.transcript_path,
            media_type="application/json",
        )
    if project.source_video_path:
        _replace_artifacts_for_type(session, project_id=project.id, artifact_type="source_video")
        _create_artifact_if_present(
            ArtifactRepository(session),
            project_id=project.id,
            artifact_type="source_video",
            path=project.source_video_path,
            media_type="video/mp4",
        )

    clip_repo = ClipRepository(session)
    for clip_payload in clips:
        payload = dict(clip_payload)
        payload["source"] = clip_source
        existing = clip_repo.get_by_external_id(project.id, str(payload["id"]))
        if existing is None:
            clip_repo.create_from_dict(project.id, payload)
        else:
            _update_clip_from_payload(existing, payload)
            clip_repo.touch(existing)
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


def _safe_relative_to_root(path: Path | None, project_root: Path) -> str | None:
    if path is None:
        return None
    try:
        return str(Path(path).resolve().relative_to(Path(project_root).resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _find_first_media(input_dir: Path) -> Path | None:
    if not input_dir.exists():
        return None
    video_matches: list[Path] = []
    for extension in (".mp4", ".mov", ".mkv", ".webm"):
        video_matches.extend(path for path in input_dir.glob(f"*{extension}") if path.is_file())
    if video_matches:
        return max(
            video_matches,
            key=lambda path: (
                not bool(MEDIA_FORMAT_VARIANT_RE.search(path.stem)),
                path.suffix.lower() == ".mp4",
                path.stat().st_size,
                path.stat().st_mtime,
            ),
        )
    for extension in (".m4a", ".mp3", ".wav"):
        matches = sorted(path for path in input_dir.glob(f"*{extension}") if path.is_file())
        if matches:
            return matches[0]
    return None


def _replace_artifacts_for_type(session: Session, *, project_id: int, artifact_type: str) -> None:
    session.execute(
        delete(Artifact).where(
            Artifact.project_id == int(project_id),
            Artifact.artifact_type == artifact_type,
        )
    )


def _update_clip_from_payload(clip: Clip, payload: dict[str, Any]) -> None:
    clip.clip_index = int(payload["index"])
    clip.ai_start = float(payload["ai_start"])
    clip.ai_end = float(payload["ai_end"])
    clip.reviewed_start = float(payload["reviewed_start"]) if payload.get("reviewed_start") is not None else None
    clip.reviewed_end = float(payload["reviewed_end"]) if payload.get("reviewed_end") is not None else None
    if str(clip.boundary_source or "heuristic") == "heuristic":
        clip.edited_start = float(payload["edited_start"])
        clip.edited_end = float(payload["edited_end"])
        clip.boundary_source = str(payload.get("boundary_source") or "heuristic")
    clip.min_start = float(payload["min_start"])
    clip.max_start = float(payload["max_start"])
    clip.min_end = float(payload["min_end"])
    clip.max_end = float(payload["max_end"])
    clip.summary = str(payload.get("summary") or "")
    clip.text = str(payload.get("text") or "")
    clip.source = payload.get("source")
    clip.candidate_id = str(payload["candidate_id"]) if payload.get("candidate_id") is not None else None
    clip.selection_source = payload.get("selection_source")
    clip.local_score = float(payload["local_score"]) if payload.get("local_score") is not None else None
    clip.local_rank = int(payload["local_rank"]) if payload.get("local_rank") is not None else None
    clip.selection_reasons = list(payload.get("selection_reasons") or [])
    clip.local_features = dict(payload.get("local_features") or {})


def clear_sqlite_project_rows(session: Session) -> None:
    for model in (ClipEvaluation, Artifact, Job, Clip, Project):
        session.execute(delete(model))


def import_selected_local_source(
    session: Session,
    *,
    project_root: Path = PROJECT_ROOT,
    project_id: str = DEFAULT_PROJECT_ID,
    allow_demo: bool = False,
) -> Project | None:
    source = find_local_import_source(project_root=project_root, project_id=project_id, allow_demo=allow_demo)
    if source is None:
        return None
    if source.source_type == "project_state":
        return import_project_state(session, project_root=project_root, project_id=project_id)
    return import_candidate_file(session, project_root=project_root, source_path=source.source_path)


def _looks_like_demo_clip(clip: Clip) -> bool:
    return (
        clip.external_id == "clip_001"
        and round(float(clip.ai_start), 2) == 100.0
        and round(float(clip.ai_end), 2) == 140.0
        and "clear podcast answer" in str(clip.summary or "").lower()
    )


def stale_demo_warning(session: Session, *, project_root: Path = PROJECT_ROOT) -> str | None:
    project_repo = ProjectRepository(session)
    project = project_repo.get_default()
    if project is None:
        return None
    clips = ClipRepository(session).list_for_project(project.id)
    if len(clips) != 1 or not _looks_like_demo_clip(clips[0]):
        return None
    source = find_local_import_source(project_root=project_root, allow_demo=False)
    if source is None or source.is_demo:
        return None
    return (
        "SQLite contains demo data while real candidate files exist. "
        f"Run: {IMPORT_RESET_COMMAND}"
    )


def bootstrap_legacy_state_if_needed(
    session: Session,
    *,
    project_root: Path = PROJECT_ROOT,
    project_id: str = DEFAULT_PROJECT_ID,
) -> Project | None:
    project_repo = ProjectRepository(session)
    if project_repo.count() > 0:
        return project_repo.get_default()

    return import_selected_local_source(session, project_root=project_root, project_id=project_id)
