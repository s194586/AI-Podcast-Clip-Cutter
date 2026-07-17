from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker


DEFAULT_DATABASE_URL = "sqlite:///data/podcast_cutter.db"

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_configured_url: str | None = None


def get_database_url() -> str:
    return os.environ.get("PODCAST_CUTTER_DB_URL", DEFAULT_DATABASE_URL)


def _sqlite_database_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return None
    if not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def configure_database(database_url: str | None = None) -> Engine:
    global _configured_url, _engine, _session_factory

    resolved_url = database_url or get_database_url()
    if _engine is not None and _configured_url == resolved_url:
        return _engine

    if _engine is not None:
        _engine.dispose()

    database_path = _sqlite_database_path(resolved_url)
    if database_path is not None:
        database_path.parent.mkdir(parents=True, exist_ok=True)

    url = make_url(resolved_url)
    connect_args = (
        {"check_same_thread": False, "timeout": 30}
        if url.drivername.startswith("sqlite")
        else {}
    )
    _engine = create_engine(resolved_url, connect_args=connect_args, future=True)
    if url.drivername.startswith("sqlite"):
        event.listen(_engine, "connect", _configure_sqlite_connection)
    _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)
    _configured_url = resolved_url
    return _engine


def get_engine() -> Engine:
    return configure_database()


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


def init_database() -> None:
    from .models import Base

    engine = get_engine()
    Base.metadata.create_all(engine)
    _ensure_sqlite_project_flow_columns(engine)
    _ensure_sqlite_clip_boundary_columns(engine)
    _ensure_sqlite_clip_evaluation_columns(engine)
    _ensure_sqlite_job_flow_columns(engine)


def _ensure_sqlite_project_flow_columns(engine: Engine) -> None:
    if not engine.dialect.name.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "projects" not in inspector.get_table_names():
        return
    existing_columns = {column["name"] for column in inspector.get_columns("projects")}
    column_sql = {
        "current_stage": "ALTER TABLE projects ADD COLUMN current_stage VARCHAR(128) DEFAULT 'waiting'",
        "progress_percent": "ALTER TABLE projects ADD COLUMN progress_percent FLOAT DEFAULT 0.0",
        "workspace_path": "ALTER TABLE projects ADD COLUMN workspace_path VARCHAR(2048)",
        "error_message": "ALTER TABLE projects ADD COLUMN error_message TEXT",
        "auto_review": "ALTER TABLE projects ADD COLUMN auto_review BOOLEAN DEFAULT 1",
        "started_at": "ALTER TABLE projects ADD COLUMN started_at DATETIME",
        "completed_at": "ALTER TABLE projects ADD COLUMN completed_at DATETIME",
    }
    missing = [name for name in column_sql if name not in existing_columns]
    if not missing:
        return
    with engine.begin() as connection:
        for column_name in missing:
            connection.execute(text(column_sql[column_name]))


def _ensure_sqlite_clip_boundary_columns(engine: Engine) -> None:
    if not engine.dialect.name.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "clips" not in inspector.get_table_names():
        return
    existing_columns = {column["name"] for column in inspector.get_columns("clips")}
    column_sql = {
        "reviewed_start": "ALTER TABLE clips ADD COLUMN reviewed_start FLOAT",
        "reviewed_end": "ALTER TABLE clips ADD COLUMN reviewed_end FLOAT",
        "boundary_source": "ALTER TABLE clips ADD COLUMN boundary_source VARCHAR(64) DEFAULT 'heuristic'",
    }
    missing = [name for name in column_sql if name not in existing_columns]
    if not missing:
        return
    with engine.begin() as connection:
        for column_name in missing:
            connection.execute(text(column_sql[column_name]))


def _ensure_sqlite_clip_evaluation_columns(engine: Engine) -> None:
    if not engine.dialect.name.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "clip_evaluations" not in inspector.get_table_names():
        return
    existing_columns = {column["name"] for column in inspector.get_columns("clip_evaluations")}
    column_sql = {
        "provider": "ALTER TABLE clip_evaluations ADD COLUMN provider VARCHAR(64) DEFAULT 'local_stub'",
        "model": "ALTER TABLE clip_evaluations ADD COLUMN model VARCHAR(256)",
        "selected_start_segment_id": "ALTER TABLE clip_evaluations ADD COLUMN selected_start_segment_id VARCHAR(256)",
        "selected_end_segment_id": "ALTER TABLE clip_evaluations ADD COLUMN selected_end_segment_id VARCHAR(256)",
        "reviewed_start": "ALTER TABLE clip_evaluations ADD COLUMN reviewed_start FLOAT",
        "reviewed_end": "ALTER TABLE clip_evaluations ADD COLUMN reviewed_end FLOAT",
        "start_delta_seconds": "ALTER TABLE clip_evaluations ADD COLUMN start_delta_seconds FLOAT",
        "end_delta_seconds": "ALTER TABLE clip_evaluations ADD COLUMN end_delta_seconds FLOAT",
        "reasoning_summary": "ALTER TABLE clip_evaluations ADD COLUMN reasoning_summary TEXT DEFAULT ''",
        "start_reason": "ALTER TABLE clip_evaluations ADD COLUMN start_reason TEXT DEFAULT ''",
        "end_reason": "ALTER TABLE clip_evaluations ADD COLUMN end_reason TEXT DEFAULT ''",
        "context_seconds": "ALTER TABLE clip_evaluations ADD COLUMN context_seconds FLOAT",
    }
    missing = [name for name in column_sql if name not in existing_columns]
    if not missing:
        return
    with engine.begin() as connection:
        for column_name in missing:
            connection.execute(text(column_sql[column_name]))


def _ensure_sqlite_job_flow_columns(engine: Engine) -> None:
    if not engine.dialect.name.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "jobs" not in inspector.get_table_names():
        return
    existing_columns = {column["name"] for column in inspector.get_columns("jobs")}
    column_sql = {
        "current_stage": "ALTER TABLE jobs ADD COLUMN current_stage VARCHAR(128)",
        "process_id": "ALTER TABLE jobs ADD COLUMN process_id INTEGER",
        "log_path": "ALTER TABLE jobs ADD COLUMN log_path VARCHAR(2048)",
        "started_at": "ALTER TABLE jobs ADD COLUMN started_at DATETIME",
        "finished_at": "ALTER TABLE jobs ADD COLUMN finished_at DATETIME",
        "exit_code": "ALTER TABLE jobs ADD COLUMN exit_code INTEGER",
        "orchestrator_type": "ALTER TABLE jobs ADD COLUMN orchestrator_type VARCHAR(32) DEFAULT 'local'",
        "airflow_dag_id": "ALTER TABLE jobs ADD COLUMN airflow_dag_id VARCHAR(256)",
        "airflow_dag_run_id": "ALTER TABLE jobs ADD COLUMN airflow_dag_run_id VARCHAR(512)",
        "airflow_state": "ALTER TABLE jobs ADD COLUMN airflow_state VARCHAR(64)",
        "airflow_task_id": "ALTER TABLE jobs ADD COLUMN airflow_task_id VARCHAR(256)",
        "airflow_try_number": "ALTER TABLE jobs ADD COLUMN airflow_try_number INTEGER",
        "airflow_max_tries": "ALTER TABLE jobs ADD COLUMN airflow_max_tries INTEGER",
        "cancel_requested": "ALTER TABLE jobs ADD COLUMN cancel_requested BOOLEAN DEFAULT 0",
    }
    missing = [name for name in column_sql if name not in existing_columns]
    with engine.begin() as connection:
        for column_name in missing:
            connection.execute(text(column_sql[column_name]))
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_jobs_orchestrator_type "
                "ON jobs (orchestrator_type)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_jobs_airflow_dag_run_id "
                "ON jobs (airflow_dag_run_id) WHERE airflow_dag_run_id IS NOT NULL"
            )
        )


def get_session() -> Session:
    if _session_factory is None:
        configure_database()
    if _session_factory is None:
        raise RuntimeError("Database session factory was not configured.")
    return _session_factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def configured_database_url() -> str:
    return _configured_url or get_database_url()


def configured_database_path() -> Path | None:
    return _sqlite_database_path(configured_database_url())
