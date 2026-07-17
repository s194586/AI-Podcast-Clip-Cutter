from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_url: Mapped[str] = mapped_column(String(2048), default="")
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="draft", index=True)
    current_stage: Mapped[str] = mapped_column(String(128), default="waiting", index=True)
    progress_percent: Mapped[float] = mapped_column(Float, default=0.0)
    workspace_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_review: Mapped[bool] = mapped_column(Boolean, default=True)
    source_video_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    transcript_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    candidate_source_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    clips: Mapped[list[Clip]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="Clip.clip_index",
    )
    jobs: Mapped[list[Job]] = relationship(back_populates="project", cascade="all, delete-orphan")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="project", cascade="all, delete-orphan")
    evaluations: Mapped[list[ClipEvaluation]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ClipEvaluation.created_at.desc()",
    )


class Clip(Base):
    __tablename__ = "clips"
    __table_args__ = (UniqueConstraint("project_id", "external_id", name="uq_clips_project_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    clip_index: Mapped[int] = mapped_column(Integer, index=True)

    ai_start: Mapped[float] = mapped_column(Float)
    ai_end: Mapped[float] = mapped_column(Float)
    reviewed_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviewed_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    edited_start: Mapped[float] = mapped_column(Float)
    edited_end: Mapped[float] = mapped_column(Float)
    boundary_source: Mapped[str] = mapped_column(String(64), default="heuristic", index=True)
    min_start: Mapped[float] = mapped_column(Float)
    max_start: Mapped[float] = mapped_column(Float)
    min_end: Mapped[float] = mapped_column(Float)
    max_end: Mapped[float] = mapped_column(Float)

    status: Mapped[str] = mapped_column(String(64), default="draft", index=True)
    render_status: Mapped[str] = mapped_column(String(64), default="not_rendered", index=True)

    summary: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    selection_source: Mapped[str | None] = mapped_column(String(256), nullable=True)

    local_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    local_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selection_reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    local_features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    raw_outputs: Mapped[list[str]] = mapped_column(JSON, default=list)
    subtitled_outputs: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_render_output_dir: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    last_render_warnings: Mapped[list[str]] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    project: Mapped[Project] = relationship(back_populates="clips")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="clip")
    evaluations: Mapped[list[ClipEvaluation]] = relationship(
        back_populates="clip",
        cascade="all, delete-orphan",
        order_by="ClipEvaluation.created_at.desc()",
    )


class ClipEvaluation(Base):
    __tablename__ = "clip_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    clip_id: Mapped[int | None] = mapped_column(ForeignKey("clips.id", ondelete="SET NULL"), nullable=True, index=True)
    external_clip_id: Mapped[str] = mapped_column(String(128), index=True)

    provider: Mapped[str] = mapped_column(String(64), default="local_stub", index=True)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    decision: Mapped[str] = mapped_column(String(64), index=True)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    context_score: Mapped[float] = mapped_column(Float, default=0.0)
    hook_score: Mapped[float] = mapped_column(Float, default=0.0)
    payoff_score: Mapped[float] = mapped_column(Float, default=0.0)
    boundary_score: Mapped[float] = mapped_column(Float, default=0.0)
    privacy_risk: Mapped[str] = mapped_column(String(32), default="low", index=True)
    recommended_action: Mapped[str] = mapped_column(String(64), index=True)
    selected_start_segment_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    selected_end_segment_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    suggested_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggested_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviewed_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviewed_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_delta_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_delta_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning_summary: Mapped[str] = mapped_column(Text, default="")
    start_reason: Mapped[str] = mapped_column(Text, default="")
    end_reason: Mapped[str] = mapped_column(Text, default="")
    context_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    crop_advice: Mapped[str] = mapped_column(String(64), default="keep_current")
    needs_more_context: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    warnings_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    raw_result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    project: Mapped[Project] = relationship(back_populates="evaluations")
    clip: Mapped[Clip | None] = relationship(back_populates="evaluations")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(64), default="draft", index=True)
    stage: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_stage: Mapped[str | None] = mapped_column(String(128), nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    process_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    orchestrator_type: Mapped[str] = mapped_column(String(32), default="local", index=True)
    airflow_dag_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    airflow_dag_run_id: Mapped[str | None] = mapped_column(String(512), nullable=True, unique=True)
    airflow_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    airflow_task_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    airflow_try_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    airflow_max_tries: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    project: Mapped[Project] = relationship(back_populates="jobs")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    clip_id: Mapped[int | None] = mapped_column(ForeignKey("clips.id", ondelete="SET NULL"), nullable=True, index=True)
    artifact_type: Mapped[str] = mapped_column(String(128), index=True)
    path: Mapped[str] = mapped_column(String(2048))
    filename: Mapped[str] = mapped_column(String(512))
    media_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    project: Mapped[Project] = relationship(back_populates="artifacts")
    clip: Mapped[Clip | None] = relationship(back_populates="artifacts")
