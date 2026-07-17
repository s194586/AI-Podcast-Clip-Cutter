from __future__ import annotations

from datetime import datetime, timedelta, timezone

from orchestration.airflow.pipeline_tasks import execute_airflow_stage


DAG_ID = "podcast_clip_pipeline"
TASK_ORDER = (
    "prepare_workspace",
    "download_source",
    "transcribe",
    "validate_transcript",
    "generate_candidates",
    "import_candidates",
    "review_boundaries",
    "mark_ready",
)
TASK_RETRIES = {
    "prepare_workspace": 0,
    "download_source": 1,
    "transcribe": 1,
    "validate_transcript": 0,
    "generate_candidates": 1,
    "import_candidates": 2,
    "review_boundaries": 0,
    "mark_ready": 1,
}

try:  # pragma: no cover - Airflow is installed only in the container image
    from airflow.sdk import dag, get_current_context, task
except ImportError as exc:  # pragma: no cover - regular application test environment
    AIRFLOW_AVAILABLE = False
    DAG_IMPORT_ERROR = exc
    podcast_clip_pipeline = None
else:
    AIRFLOW_AVAILABLE = True
    DAG_IMPORT_ERROR = None

    def _run_task(stage_name: str) -> dict:
        context = get_current_context()
        dag_run = context["dag_run"]
        task_instance = context["task_instance"]
        retries = TASK_RETRIES[stage_name]
        return execute_airflow_stage(
            dict(dag_run.conf or {}),
            stage_name,
            try_number=int(task_instance.try_number),
            max_attempts=retries + 1,
        )


    @dag(
        dag_id=DAG_ID,
        description="Sequential podcast clip preparation using reusable pipeline stages.",
        schedule=None,
        start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        catchup=False,
        max_active_runs=1,
        max_active_tasks=1,
        tags=["podcast-cutter", "pipeline-services"],
    )
    def _podcast_clip_pipeline():
        @task(task_id="prepare_workspace", retries=TASK_RETRIES["prepare_workspace"])
        def prepare_workspace():
            return _run_task("prepare_workspace")

        @task(
            task_id="download_source",
            retries=TASK_RETRIES["download_source"],
            retry_delay=timedelta(seconds=30),
        )
        def download_source():
            return _run_task("download_source")

        @task(
            task_id="transcribe",
            retries=TASK_RETRIES["transcribe"],
            retry_delay=timedelta(minutes=2),
        )
        def transcribe():
            return _run_task("transcribe")

        @task(task_id="validate_transcript", retries=TASK_RETRIES["validate_transcript"])
        def validate_transcript():
            return _run_task("validate_transcript")

        @task(
            task_id="generate_candidates",
            retries=TASK_RETRIES["generate_candidates"],
            retry_delay=timedelta(seconds=30),
        )
        def generate_candidates():
            return _run_task("generate_candidates")

        @task(
            task_id="import_candidates",
            retries=TASK_RETRIES["import_candidates"],
            retry_delay=timedelta(seconds=15),
        )
        def import_candidates():
            return _run_task("import_candidates")

        @task(task_id="review_boundaries", retries=TASK_RETRIES["review_boundaries"])
        def review_boundaries():
            return _run_task("review_boundaries")

        @task(
            task_id="mark_ready",
            retries=TASK_RETRIES["mark_ready"],
            retry_delay=timedelta(seconds=10),
        )
        def mark_ready():
            return _run_task("mark_ready")

        stages = (
            prepare_workspace(),
            download_source(),
            transcribe(),
            validate_transcript(),
            generate_candidates(),
            import_candidates(),
            review_boundaries(),
            mark_ready(),
        )
        for upstream, downstream in zip(stages, stages[1:]):
            upstream >> downstream


    podcast_clip_pipeline = _podcast_clip_pipeline()


# Compatibility name for callers that imported the v0.6 placeholder symbol.
podcast_pipeline = podcast_clip_pipeline
