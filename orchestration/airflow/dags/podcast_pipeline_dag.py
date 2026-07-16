from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(os.environ.get("PODCAST_CUTTER_PROJECT_ROOT", Path(__file__).resolve().parents[3])).resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestration.airflow import pipeline_tasks

try:  # pragma: no cover - optional dependency path
    from airflow.decorators import dag, task
    from airflow.operators.python import get_current_context
except Exception as exc:  # pragma: no cover - default local test path
    AIRFLOW_AVAILABLE = False
    DAG_IMPORT_ERROR = exc
    podcast_pipeline = None
else:  # pragma: no cover - requires Airflow installation
    AIRFLOW_AVAILABLE = True
    DAG_IMPORT_ERROR = None

    def _execute_step(function: Callable[[dict[str, Any]], dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
        try:
            return function(config)
        except Exception as exc:
            project_id = config.get("project_id")
            if project_id is not None:
                pipeline_tasks.mark_project_failed(int(project_id), str(exc))
            raise

    @dag(
        dag_id="podcast_deterministic_pipeline",
        description="Prepare reviewed podcast clip candidates; rendering remains human-triggered.",
        start_date=datetime(2026, 1, 1),
        schedule=None,
        catchup=False,
        tags=["podcast-cutter", "deterministic-pipeline"],
    )
    def podcast_pipeline_dag():
        @task
        def validate_project_config_task() -> dict[str, Any]:
            context = get_current_context()
            dag_run = context.get("dag_run")
            return pipeline_tasks.validate_project_config(getattr(dag_run, "conf", None))

        @task
        def download_media_task(config: dict[str, Any]) -> dict[str, Any]:
            return _execute_step(pipeline_tasks.download_media, config)

        @task
        def transcribe_audio_task(config: dict[str, Any]) -> dict[str, Any]:
            return _execute_step(pipeline_tasks.transcribe_audio, config)

        @task
        def generate_candidates_task(config: dict[str, Any]) -> dict[str, Any]:
            return _execute_step(pipeline_tasks.generate_candidates, config)

        @task
        def import_candidates_to_sqlite_task(config: dict[str, Any]) -> dict[str, Any]:
            return _execute_step(pipeline_tasks.import_candidates_to_sqlite, config)

        @task
        def review_candidates_with_gemini_task(config: dict[str, Any]) -> dict[str, Any]:
            return _execute_step(pipeline_tasks.review_candidates_with_gemini, config)

        @task
        def mark_project_ready_task(config: dict[str, Any]) -> dict[str, Any]:
            return _execute_step(pipeline_tasks.mark_project_ready, config)

        config = validate_project_config_task()
        media = download_media_task(config)
        transcript = transcribe_audio_task(media)
        candidates = generate_candidates_task(transcript)
        imported = import_candidates_to_sqlite_task(candidates)
        reviewed = review_candidates_with_gemini_task(imported)
        mark_project_ready_task(reviewed)

    podcast_pipeline = podcast_pipeline_dag()
