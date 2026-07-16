from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .orchestration import (
    ProjectAlreadyRunningError,
    ProjectOrchestratorConfigurationError,
    ProjectOrchestratorNotFoundError,
    get_pipeline_orchestrator,
    recover_orphaned_jobs,
)
from .services.clips import (
    ClipValidationError,
    find_clip,
    load_clips,
    set_clip_status,
    update_clip_bounds,
)
from .services.project_service import (
    ProjectNotFoundError,
    ProjectValidationError,
    compatibility_project_response,
    create_project,
    get_project,
    get_project_clips,
    get_project_source_video_path,
    get_project_status,
    get_project_workspace_root,
    initialize_application_state,
    list_projects,
)
from .services.export_service import ExportAccessError, ExportNotFoundError, get_project_export_file, list_project_exports
from .services.project_state import PROJECT_ROOT
from .services.render import RenderError, locate_input_video, render_adjusted_clip
from apps.review_agent.config import ReviewConfigError, load_review_config, safe_review_config_summary
from apps.review_agent.service import ClipReviewConfigurationError, ClipReviewError, ClipReviewNotFoundError, ReviewAgentService


STATIC_DIR = Path(__file__).resolve().parent / "static"
logger = logging.getLogger(__name__)


def api_project_root() -> Path:
    return Path(os.environ.get("PODCAST_CUTTER_PROJECT_ROOT", str(PROJECT_ROOT))).resolve()


def _log_review_configuration(project_root: Path) -> None:
    try:
        config = load_review_config(project_root=project_root, require_api_key=False)
    except ReviewConfigError as exc:
        logger.warning("Clip review provider configuration is invalid: %s", exc)
        return

    logger.info(
        "Clip review provider configured: provider=%s model=%s mode_source=%s gemini_api_key_configured=%s",
        config.provider,
        config.model,
        config.mode_source,
        "yes" if config.api_key_configured else "no",
    )
    for warning in config.warnings:
        logger.warning("Clip review provider configuration warning: %s", warning)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    project_root = api_project_root()
    initialize_application_state(project_root=project_root)
    recover_orphaned_jobs(project_root=project_root)
    _log_review_configuration(project_root)
    yield


app = FastAPI(title="Podcast Shorts Cutter", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class RenderPayload(BaseModel):
    clip_id: str
    start: float
    end: float


class ClipBoundsPayload(BaseModel):
    start: float
    end: float


class ProjectCreatePayload(BaseModel):
    source_url: str
    title: str | None = None
    auto_review: bool = True
    auto_start: bool = False


class ClipReviewPayload(BaseModel):
    project_id: int | None = None
    apply_safe_suggestions: bool = True


class ProjectReviewPayload(BaseModel):
    apply_safe_suggestions: bool = True


PROJECT_PUBLIC_PATH_FIELDS = {
    "workspace_path",
    "source_video_path",
    "transcript_path",
    "candidate_source_path",
}
CLIP_PUBLIC_PATH_FIELDS = {
    "raw_outputs",
    "subtitled_outputs",
    "last_render_output_dir",
}
RENDER_PUBLIC_PATH_FIELDS = {
    "output_dir",
    "raw_outputs",
    "subtitled_outputs",
    "windows_file",
    "cutting_log",
    "logs",
}


def _public_project(project: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in project.items() if key not in PROJECT_PUBLIC_PATH_FIELDS}


def _public_clip(clip: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in clip.items() if key not in CLIP_PUBLIC_PATH_FIELDS}


def _public_render_result(result: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in result.items() if key not in RENDER_PUBLIC_PATH_FIELDS}
    if isinstance(payload.get("clip"), dict):
        payload["clip"] = _public_clip(payload["clip"])
    return payload


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    review_config = safe_review_config_summary(project_root=api_project_root())
    return {
        "status": "ok",
        "clip_review_provider": review_config.get("provider"),
        "clip_review_model": review_config.get("model"),
        "clip_review_mode_source": review_config.get("mode_source"),
        "gemini_api_key_configured": review_config.get("gemini_api_key_configured"),
        "review_config": review_config,
    }


@app.get("/project")
def project() -> dict:
    try:
        load_clips(project_root=api_project_root())
    except ClipValidationError:
        pass
    return compatibility_project_response(project_root=api_project_root())


@app.post("/projects")
def create_project_endpoint(payload: ProjectCreatePayload) -> dict[str, Any]:
    try:
        project_root = api_project_root()
        project = create_project(
            source_url=payload.source_url,
            title=payload.title,
            auto_review=payload.auto_review,
            project_root=project_root,
        )
        response: dict[str, Any] = {"project": _public_project(project)}
        if payload.auto_start:
            response["job"] = get_pipeline_orchestrator(project_root=project_root).start_project(project["id"]).to_dict()
            response["status"] = get_project_status(project["id"])
        return response
    except ProjectValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProjectAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProjectOrchestratorConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/projects/{project_id}/start")
def start_project_endpoint(project_id: int) -> dict[str, Any]:
    try:
        project_root = api_project_root()
        job = get_pipeline_orchestrator(project_root=project_root).start_project(project_id)
        return {"job": job.to_dict(), "status": get_project_status(project_id)}
    except ProjectAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProjectOrchestratorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectOrchestratorConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/projects")
def list_projects_endpoint() -> dict[str, Any]:
    return {"projects": [_public_project(project) for project in list_projects()]}


@app.get("/projects/{project_id}")
def get_project_endpoint(project_id: int) -> dict[str, Any]:
    try:
        return {"project": _public_project(get_project(project_id))}
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/projects/{project_id}/clips")
def get_project_clips_endpoint(project_id: int) -> dict[str, Any]:
    try:
        return {"clips": [_public_clip(clip) for clip in get_project_clips(project_id)]}
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/projects/{project_id}/status")
def get_project_status_endpoint(project_id: int) -> dict[str, Any]:
    try:
        return get_project_status(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/projects/{project_id}/logs")
def get_project_logs_endpoint(project_id: int, tail: int = 200) -> dict[str, Any]:
    try:
        return get_pipeline_orchestrator(project_root=api_project_root()).read_project_log_tail(project_id, tail=tail)
    except ProjectOrchestratorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/projects/{project_id}/cancel")
def cancel_project_endpoint(project_id: int) -> dict[str, Any]:
    try:
        return get_pipeline_orchestrator(project_root=api_project_root()).cancel_project(project_id).to_dict()
    except ProjectOrchestratorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/projects/{project_id}/clips/{clip_id}")
def update_project_clip(project_id: int, clip_id: str, payload: ClipBoundsPayload) -> dict:
    try:
        return {
            "clip": _public_clip(
                update_clip_bounds(
                    clip_id,
                    payload.start,
                    payload.end,
                    project_id=project_id,
                    project_root=api_project_root(),
                )
            )
        }
    except ClipValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/projects/{project_id}/clips/{clip_id}/accept")
def accept_project_clip(project_id: int, clip_id: str) -> dict:
    try:
        return {
            "clip": _public_clip(
                set_clip_status(clip_id, "accepted", project_id=project_id, project_root=api_project_root())
            )
        }
    except ClipValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/projects/{project_id}/clips/{clip_id}/reject")
def reject_project_clip(project_id: int, clip_id: str) -> dict:
    try:
        return {
            "clip": _public_clip(
                set_clip_status(clip_id, "rejected", project_id=project_id, project_root=api_project_root())
            )
        }
    except ClipValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/projects/{project_id}/clips/{clip_id}/review")
def review_project_clip(project_id: int, clip_id: str) -> dict[str, Any]:
    try:
        return ReviewAgentService(project_root=api_project_root()).review_clip(clip_id=clip_id, project_id=project_id)
    except ClipReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ClipReviewConfigurationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ClipReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/projects/{project_id}/clips/{clip_id}/review")
def get_project_clip_review(project_id: int, clip_id: str) -> dict[str, Any]:
    try:
        return ReviewAgentService(project_root=api_project_root()).get_latest_review(clip_id=clip_id, project_id=project_id)
    except ClipReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/projects/{project_id}/review-clips")
def review_project_clips(project_id: int, payload: ProjectReviewPayload | None = None) -> dict[str, Any]:
    try:
        apply_safe_suggestions = True if payload is None else payload.apply_safe_suggestions
        return ReviewAgentService(project_root=api_project_root()).review_project_clips(
            project_id=project_id,
            apply_safe_suggestions=apply_safe_suggestions,
        )
    except ClipReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ClipReviewConfigurationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ClipReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/clips")
def clips() -> dict:
    try:
        project_root = api_project_root()
        source_video = locate_input_video(project_root)
        return {
            "clips": load_clips(project_root=project_root),
            "source_video_available": source_video is not None,
            "source_video_url": "/source-video",
        }
    except ClipValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/clips/{clip_id}")
def update_clip(clip_id: str, payload: ClipBoundsPayload) -> dict:
    try:
        return {"clip": update_clip_bounds(clip_id, payload.start, payload.end)}
    except ClipValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/clips/{clip_id}/accept")
def accept_clip(clip_id: str) -> dict:
    try:
        return {"clip": set_clip_status(clip_id, "accepted")}
    except ClipValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/clips/{clip_id}/reject")
def reject_clip(clip_id: str) -> dict:
    try:
        return {"clip": set_clip_status(clip_id, "rejected")}
    except ClipValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/clips/{clip_id}/review")
def review_clip(clip_id: str, payload: ClipReviewPayload | None = None) -> dict[str, Any]:
    try:
        project_id = payload.project_id if payload is not None else None
        apply_safe_suggestions = True if payload is None else payload.apply_safe_suggestions
        return ReviewAgentService(project_root=api_project_root()).review_clip(
            clip_id=clip_id,
            project_id=project_id,
            apply_safe_suggestions=apply_safe_suggestions,
        )
    except ClipReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ClipReviewConfigurationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ClipReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/clips/{clip_id}/review")
def get_clip_review(clip_id: str, project_id: int | None = None) -> dict[str, Any]:
    try:
        return ReviewAgentService(project_root=api_project_root()).get_latest_review(clip_id=clip_id, project_id=project_id)
    except ClipReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/source-video")
def source_video() -> FileResponse:
    video_path = locate_input_video(api_project_root())
    if video_path is None:
        raise HTTPException(
            status_code=404,
            detail="Missing source video. Put or download an mp4, mov, mkv, or webm file into input/.",
        )
    return FileResponse(video_path)


@app.get("/projects/{project_id}/source-video")
def project_source_video(project_id: int) -> FileResponse:
    try:
        workspace_root = get_project_workspace_root(project_id, project_root=api_project_root())
        persisted_video_path = get_project_source_video_path(project_id, project_root=api_project_root())
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    video_path = persisted_video_path or locate_input_video(workspace_root)
    if video_path is None:
        raise HTTPException(
            status_code=404,
            detail="Missing source video in this project's workspace input directory.",
        )
    return FileResponse(video_path)


@app.post("/render")
def render(payload: RenderPayload) -> dict:
    try:
        project_root = api_project_root()
        clip = find_clip(load_clips(project_root=project_root), payload.clip_id)
        return render_adjusted_clip(clip, payload.start, payload.end, project_root=project_root)
    except ClipValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RenderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_detail()) from exc


@app.post("/projects/{project_id}/render")
def render_project_clip(project_id: int, payload: RenderPayload) -> dict:
    try:
        project_root = api_project_root()
        workspace_root = get_project_workspace_root(project_id, project_root=project_root)
        source_video_path = get_project_source_video_path(project_id, project_root=project_root)
        clip = find_clip(load_clips(project_id=project_id, project_root=project_root), payload.clip_id)
        return _public_render_result(
            render_adjusted_clip(
                clip,
                payload.start,
                payload.end,
                project_root=project_root,
                runtime_root=workspace_root,
                project_id=project_id,
                source_video_path=source_video_path,
            )
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ClipValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RenderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_detail()) from exc


@app.get("/projects/{project_id}/exports")
def get_project_exports_endpoint(project_id: int) -> dict[str, Any]:
    try:
        return {"exports": list_project_exports(project_id, project_root=api_project_root())}
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExportAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/projects/{project_id}/exports/{artifact_id}/download")
def download_project_export_endpoint(project_id: int, artifact_id: int) -> FileResponse:
    try:
        file_path, filename, media_type = get_project_export_file(
            project_id,
            artifact_id,
            project_root=api_project_root(),
        )
        return FileResponse(file_path, media_type=media_type, filename=filename)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExportAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
