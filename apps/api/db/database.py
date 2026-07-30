from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import MetaData, create_engine, event, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateTable


DEFAULT_DATABASE_URL = "sqlite:///data/podcast_cutter.db"

_LEGACY_NULLABLE_CLIP_EVALUATION_COLUMNS = frozenset(
    {
        "quality_score",
        "context_score",
        "hook_score",
        "payoff_score",
        "boundary_score",
        "privacy_risk",
        "crop_advice",
        "needs_more_context",
    }
)
_CLIP_EVALUATIONS_REBUILD_TABLE = "__clip_evaluations_nullable_legacy_fields"

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
    _ensure_sqlite_clip_evaluation_legacy_fields_nullable(engine)
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


def _ensure_sqlite_clip_evaluation_legacy_fields_nullable(engine: Engine) -> bool:
    """Make historical review fields nullable without altering their values.

    SQLite cannot drop a NOT NULL constraint in place.  We therefore rebuild
    only the affected table in one explicit transaction, retaining its rows,
    foreign keys, and explicitly-created indexes.  A database already using
    the nullable contract is left untouched.
    """
    from .models import Base

    if not engine.dialect.name.startswith("sqlite"):
        return False

    with engine.connect() as connection:
        # This is deliberately only a cheap preflight.  Every schema snapshot
        # used by a rebuild is fetched again after BEGIN IMMEDIATE below.
        column_rows = _sqlite_table_info(connection, "clip_evaluations")
        if not column_rows:
            return False
        nullable_by_name = {str(row["name"]): not bool(row["notnull"]) for row in column_rows}
        if all(nullable_by_name.get(column_name, False) for column_name in _LEGACY_NULLABLE_CLIP_EVALUATION_COLUMNS):
            return False

        connection.commit()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            # Re-read the contract while holding the write gate.  Another
            # process may have migrated or altered this table after preflight.
            column_rows = _sqlite_table_info(connection, "clip_evaluations")
            if not column_rows:
                connection.commit()
                return False
            nullable_by_name = {str(row["name"]): not bool(row["notnull"]) for row in column_rows}
            if all(nullable_by_name.get(column_name, False) for column_name in _LEGACY_NULLABLE_CLIP_EVALUATION_COLUMNS):
                connection.commit()
                return False

            expected_columns = tuple(Base.metadata.tables["clip_evaluations"].columns.keys())
            existing_columns = tuple(str(row["name"]) for row in column_rows)
            unknown_columns = sorted(set(existing_columns) - set(expected_columns))
            missing_columns = sorted(set(expected_columns) - set(existing_columns))
            if unknown_columns or missing_columns:
                raise RuntimeError(
                    "Cannot safely rebuild clip_evaluations with an unexpected column set "
                    f"(missing={missing_columns}, unknown={unknown_columns})."
                )

            existing_foreign_keys = _sqlite_foreign_keys(connection, "clip_evaluations")
            schema_objects = connection.exec_driver_sql(
                """
                SELECT type, name, sql
                FROM sqlite_master
                WHERE tbl_name = ?
                  AND type IN ('index', 'trigger')
                  AND sql IS NOT NULL
                ORDER BY type, name
                """,
                ("clip_evaluations",),
            ).all()

            temporary_metadata = MetaData()
            # The copied table keeps its original foreign-key targets.  Copy those
            # parent tables into the temporary metadata so SQLAlchemy can compile
            # the FK constraints without creating or modifying the parent tables.
            for parent_table_name in ("projects", "clips"):
                Base.metadata.tables[parent_table_name].to_metadata(temporary_metadata)
            temporary_table = Base.metadata.tables["clip_evaluations"].to_metadata(
                temporary_metadata,
                name=_CLIP_EVALUATIONS_REBUILD_TABLE,
            )
            create_table_sql = str(CreateTable(temporary_table).compile(dialect=engine.dialect))
            quoted_columns = ", ".join(f'"{column_name}"' for column_name in existing_columns)

            existing_tables = {
                str(row[0])
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).all()
            }
            if _CLIP_EVALUATIONS_REBUILD_TABLE in existing_tables:
                raise RuntimeError(
                    f"Refusing to reuse leftover migration table {_CLIP_EVALUATIONS_REBUILD_TABLE!r}."
                )

            connection.exec_driver_sql(create_table_sql)
            connection.exec_driver_sql(
                f'INSERT INTO "{_CLIP_EVALUATIONS_REBUILD_TABLE}" ({quoted_columns}) '
                f'SELECT {quoted_columns} FROM "clip_evaluations"'
            )
            connection.exec_driver_sql('DROP TABLE "clip_evaluations"')
            connection.exec_driver_sql(
                f'ALTER TABLE "{_CLIP_EVALUATIONS_REBUILD_TABLE}" RENAME TO "clip_evaluations"'
            )
            for _object_type, _object_name, object_sql in schema_objects:
                connection.exec_driver_sql(str(object_sql))

            if _sqlite_foreign_keys(connection, "clip_evaluations") != existing_foreign_keys:
                raise RuntimeError("clip_evaluations foreign key definitions changed during nullable-field migration.")
            foreign_key_errors = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
            if foreign_key_errors:
                raise RuntimeError(f"Foreign key check failed after clip_evaluations migration: {foreign_key_errors!r}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return True


def _sqlite_foreign_keys(connection, table_name: str) -> tuple[tuple[object, ...], ...]:
    # SQLite assigns FK ids from declaration order, which may legitimately
    # differ after rebuilding an equivalent table.  Compare the definition,
    # not that implementation-specific ordinal.
    return tuple(sorted(
        tuple(row)[1:]
        for row in connection.exec_driver_sql(f'PRAGMA foreign_key_list("{table_name}")').all()
    ))


def _sqlite_table_info(connection, table_name: str):
    return connection.exec_driver_sql(f'PRAGMA table_info("{table_name}")').mappings().all()


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
        "error_code": "ALTER TABLE jobs ADD COLUMN error_code VARCHAR(128)",
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
