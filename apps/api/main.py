from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .services.clips import (
    ClipValidationError,
    find_clip,
    load_clips,
    set_clip_status,
    update_clip_bounds,
)
from .services.project_service import (
    ProjectNotFoundError,
    compatibility_project_response,
    create_project,
    get_project,
    get_project_clips,
    get_project_status,
    initialize_application_state,
    list_projects,
)
from .services.project_state import PROJECT_ROOT
from .services.render import RenderError, locate_input_video, render_adjusted_clip
from apps.review_agent.service import ClipReviewError, ClipReviewNotFoundError, ReviewAgentService


STATIC_DIR = Path(__file__).resolve().parent / "static"


def api_project_root() -> Path:
    return Path(os.environ.get("PODCAST_CUTTER_PROJECT_ROOT", str(PROJECT_ROOT))).resolve()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_application_state(project_root=api_project_root())
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


class ClipReviewPayload(BaseModel):
    project_id: int | None = None


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/project")
def project() -> dict:
    try:
        load_clips(project_root=api_project_root())
    except ClipValidationError:
        pass
    return compatibility_project_response(project_root=api_project_root())


@app.post("/projects")
def create_project_endpoint(payload: ProjectCreatePayload) -> dict[str, Any]:
    return {"project": create_project(source_url=payload.source_url, title=payload.title)}


@app.get("/projects")
def list_projects_endpoint() -> dict[str, Any]:
    return {"projects": list_projects()}


@app.get("/projects/{project_id}")
def get_project_endpoint(project_id: int) -> dict[str, Any]:
    try:
        return {"project": get_project(project_id)}
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/projects/{project_id}/clips")
def get_project_clips_endpoint(project_id: int) -> dict[str, Any]:
    try:
        return {"clips": get_project_clips(project_id)}
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/projects/{project_id}/status")
def get_project_status_endpoint(project_id: int) -> dict[str, Any]:
    try:
        return get_project_status(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/projects/{project_id}/clips/{clip_id}/review")
def review_project_clip(project_id: int, clip_id: str) -> dict[str, Any]:
    try:
        return ReviewAgentService(project_root=api_project_root()).review_clip(clip_id=clip_id, project_id=project_id)
    except ClipReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ClipReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/projects/{project_id}/clips/{clip_id}/review")
def get_project_clip_review(project_id: int, clip_id: str) -> dict[str, Any]:
    try:
        return ReviewAgentService(project_root=api_project_root()).get_latest_review(clip_id=clip_id, project_id=project_id)
    except ClipReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
        return ReviewAgentService(project_root=api_project_root()).review_clip(clip_id=clip_id, project_id=project_id)
    except ClipReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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
