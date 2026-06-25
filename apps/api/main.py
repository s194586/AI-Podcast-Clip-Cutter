from __future__ import annotations

from pathlib import Path

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
from .services.project_state import load_project_state, project_state_path
from .services.render import RenderError, locate_input_video, render_adjusted_clip


STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Podcast Shorts Cutter")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class RenderPayload(BaseModel):
    clip_id: str
    start: float
    end: float


class ClipBoundsPayload(BaseModel):
    start: float
    end: float


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/project")
def project() -> dict:
    try:
        load_clips()
    except ClipValidationError:
        pass
    return {
        "project": load_project_state(),
        "project_state_path": str(project_state_path()),
    }


@app.get("/clips")
def clips() -> dict:
    try:
        source_video = locate_input_video()
        return {
            "clips": load_clips(),
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


@app.get("/source-video")
def source_video() -> FileResponse:
    video_path = locate_input_video()
    if video_path is None:
        raise HTTPException(
            status_code=404,
            detail="Missing source video. Put or download an mp4, mov, mkv, or webm file into input/.",
        )
    return FileResponse(video_path)


@app.post("/render")
def render(payload: RenderPayload) -> dict:
    try:
        clip = find_clip(load_clips(), payload.clip_id)
        return render_adjusted_clip(clip, payload.start, payload.end)
    except ClipValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RenderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_detail()) from exc
