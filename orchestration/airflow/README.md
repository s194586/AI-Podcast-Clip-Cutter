# Airflow Placeholder

Apache Airflow is not implemented, installed, or enabled as a product orchestrator in v0.6. The active application uses `LocalPipelineOrchestrator` and `python -m apps.pipeline.entrypoint`.

This directory preserves the earlier DAG prototype as future-integration scaffolding. `pipeline_tasks.py` now contains thin adapters over `apps.pipeline` stage services, so a future Airflow implementation can reuse the same context, artifacts, import behavior, review service, and error contracts without copying pipeline logic.

The placeholder is not part of the main requirements, release validation, FastAPI startup, or browser project flow. Rendering remains human-triggered. LangGraph is not implemented here or elsewhere in the v0.6 pipeline refactor.

See [../../docs/PIPELINE_SERVICES.md](../../docs/PIPELINE_SERVICES.md) for the supported architecture and future Airflow boundary.
