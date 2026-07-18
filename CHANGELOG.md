# Changelog

Release summaries are based on the verified Git history.

## v1.0.1 — 2026-07-18

- Added secure optional custom CA trust for Docker and Airflow without disabling certificate verification.
- Migrated Gemini review to the pinned `google-genai==2.11.0` SDK.
- Added supported Interactions `output_text` and model-output content parsing.
- Improved provider compatibility, quota, timeout, output, and boundary failure classification.
- Made all-failed and mixed Gemini batch summaries report applied and attention-required counts truthfully.

## v1.0.0 — Portfolio-ready documentation and hardening

- Reorganized the repository entry point around the product problem, architecture, operation, validation, decisions, safety, and limitations.
- Added recruiter/demo guidance, portfolio talking points, engineering decisions, and a factual release history.
- Corrected documentation for the completed Airflow and LangGraph integrations.
- Added no application features or runtime dependency changes.

## v0.8-langgraph-boundary-review — 2026-07-17

- Added a typed per-clip LangGraph workflow for the existing semantic boundary review.
- Added explicit valid, corrective, manual-review, provider-failure, and cancellation routes.
- Enforced one initial provider call and at most one corrective call.
- Added mocked graph and disposable persistence/API/batch smoke tests.

## v0.7-airflow-orchestrator — 2026-07-17

- Added the Dockerized Apache Airflow 3.3.0 LocalExecutor backend.
- Added the real eight-task `podcast_clip_pipeline` DAG and FastAPI REST orchestration adapter.
- Reused the common pipeline stage executor and retained zero retries on the review task.
- Validated an isolated real Airflow smoke with automatic Gemini review disabled.

## v0.6-pipeline-services — 2026-07-17

- Extracted reusable typed pipeline context, configuration, stages, results, events, registry, and executor.
- Preserved the backwards-compatible manager CLI while enabling external orchestration.

## v0.5-react-ui — 2026-07-16

- Added the React/TypeScript product dashboard, project creation and processing views, clip editor, and exports view.
- Added typed FastAPI client integration and Vitest/Testing Library coverage.
