from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, inspect, text
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
    connect_args = {"check_same_thread": False} if url.drivername.startswith("sqlite") else {}
    _engine = create_engine(resolved_url, connect_args=connect_args, future=True)
    _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)
    _configured_url = resolved_url
    return _engine


def get_engine() -> Engine:
    return configure_database()


def init_database() -> None:
    from .models import Base

    engine = get_engine()
    Base.metadata.create_all(engine)
    _ensure_sqlite_clip_boundary_columns(engine)
    _ensure_sqlite_clip_evaluation_columns(engine)


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
