# AI Podcast Clip Cutter Repository Map

This project is a local-first podcast clip cutter. The media pipeline proposes candidate clips, the FastAPI editor persists review state in SQLite, and the Gemini transcript boundary reviewer can refine candidate start/end boundaries before a human renders final shorts.

## `manager.py`

`manager.py` is a thin compatibility CLI for the deterministic preparation pipeline. It keeps the existing arguments and root-level behavior, creates a `PipelineContext`, invokes `PipelineRunner`, prints a human-readable summary, and returns the runner exit code. Stage business logic lives under `apps/pipeline`.

It also supports `--workspace-dir` for isolated runtime output and `--analysis-only` for stopping after candidate generation. Without those flags, the original root-level local workflow remains unchanged.

Common local run:

```powershell
.\.venv\Scripts\python.exe manager.py --url "https://www.youtube.com/watch?v=..." --content-type auto --ai-mode local_only --subtitle-checker-mode local_only
```

## Deterministic Pipeline

The core pipeline is not an agent. Its sequence is fixed:

```text
download/reuse media
-> transcribe with Faster-Whisper
-> optional local diarization
-> score podcast candidate windows
-> write top_windows.json and metadata/cutting_logic.json
-> cut raw clips
-> burn subtitles
```

Important root modules:

- `download_content.py`: downloads source media with `yt-dlp`.
- `transcribe.py`: writes `transcripts/final_transcript.json` using Faster-Whisper.
- `content_classifier.py`: writes a podcast-only compatibility profile.
- `analyze_virals.py`: historical filename; currently scores podcast clip windows.
- `local_scoring.py`: transcript-aware local candidate scoring.
- `cutter.py`: renders 9:16 raw clips.
- `subtitler.py`: burns subtitles into clips.
- `subtitler_checker.py` and `semantic_clip_director.py`: optional Gemini-assisted subtitle/context checks used by the legacy pipeline modes.

Transcription device selection is controlled by `TRANSCRIPTION_DEVICE`. The default `auto` mode prefers CUDA, then falls back once to CPU int8 for missing CUDA runtime libraries. Explicit `cpu` never initializes CUDA; explicit `cuda` does not fall back.

## `apps/pipeline`

- `context.py`: explicit project id, source URL, repository root, isolated workspace, review setting, and pipeline options.
- `config.py`: normalized safe options; it contains no API keys.
- `events.py`: versioned structured lifecycle markers and coarse product progress.
- `results.py` and `exceptions.py`: typed stage/run results and controlled failure categories.
- `runner.py`: ordered stage execution with dependency stop-on-failure behavior.
- `executor.py`: one-stage lifecycle/cancellation boundary shared by runners and Airflow.
- `registry.py`: canonical project stage names and constructors.
- `airflow_config.py`: strict versioned Airflow run-config validation and context reconstruction.
- `entrypoint.py`: dedicated project worker invoked with `python -m apps.pipeline.entrypoint`.
- `persistence.py`: project-state updates for direct entrypoint runs; job state remains orchestrator-owned.
- `stages/`: download, transcribe, transcript validation, candidate generation/import, graph-backed review, readiness, and legacy rendering wrappers.

## `apps/api`

`apps/api` is the FastAPI editor backend.

- `main.py`: route definitions and static frontend mounting.
- `db/`: SQLAlchemy database setup, models, and repositories.
- `services/`: project, clip, artifact, render, legacy import, and compatibility service layers.
- `orchestration/`: local subprocess and Airflow REST implementations, shared job state, and startup recovery/reconciliation.
- `tools/import_local_project.py`: CLI import helper for refreshing SQLite from local pipeline outputs.
- `static/`: legacy compatibility editor implemented with static HTML/CSS/JavaScript.

Compatibility endpoints still support the static fallback. Project-specific
endpoints support create/start/status/logs/cancel, project clip editing,
single/batch boundary review, and project-specific rendering.

## `apps/review_agent`

`apps/review_agent` contains the transcript boundary reviewer.

- `context.py`: builds compact transcript context and numbered boundary options around a clip.
- `providers.py`: local stub and Gemini provider adapters.
- `schemas.py`: typed review contracts and result models.
- `service.py`: loads clips, calls the selected provider, validates option indexes, applies safe reviewed boundaries, and persists evaluations.
- `tools.py`: transcript loading, privacy pattern checks, and evaluation persistence helpers.
- `graph/state.py`: sanitized typed orchestration state.
- `graph/runtime.py`: ephemeral callbacks and provider/context objects that are not checkpointed.
- `graph/nodes.py`: context, provider, validation, retry, apply, failure, and cancellation nodes.
- `graph/routing.py`: bounded conditional routes.
- `graph/workflow.py`: compiled per-clip LangGraph workflow without a persistent checkpointer.

The active provider contract uses strict integer option indexes. Gemini returns `render_ready`, `adjust_boundaries`, or `reject`; backend-created `manual_review` is reserved for technical/provider validation failures.

## SQLite

SQLite is the application source of truth after import. The default database is:

```text
data/podcast_cutter.db
```

It stores projects, clips, jobs, artifacts, and clip evaluations. Project rows include local flow status, stage, progress, workspace path, auto-review setting, error, start, and completion timestamps. Job rows include process id, log path, stage, exit code, and errors.

Clip rows preserve the boundary lifecycle:

```text
ai_start/ai_end -> reviewed_start/reviewed_end -> edited_start/edited_end
```

Rendering uses `edited_start` and `edited_end`.

## Static Compatibility Frontend

The fallback editor lives in `apps/api/static`. It supports a minimal Project
Flow panel plus loading clips, previewing source video, manual slider
correction, accept/reject, single/batch review, and final render actions.

## `apps/web`

`apps/web` contains the React Product UI v0.5.

- `src/api/`: typed FastAPI client split by health, projects, clips, review, render, and errors.
- `src/components/`: app shell, status badges, progress display, and common state blocks.
- `src/pages/`: dashboard, new project, processing overview, clip editor, and exports routes.
- `src/test/`: Vitest setup and route-level test helpers.

The portfolio UI runs with Vite and proxies local backend calls to
`http://127.0.0.1:8010`. It is developed and validated separately from
FastAPI; `apps/api/static` remains the compatibility fallback.

## `orchestration/airflow`

This is the optional Dockerized Airflow integration. Local mode remains default.

- `dags/podcast_pipeline_dag.py`: real sequential `podcast_clip_pipeline` DAG.
- `pipeline_tasks.py`: thin one-stage adapter over the common executor/registry.
- `Dockerfile`: pinned Airflow 3.3.0 Python 3.12 application image with FFmpeg.
- `init_airflow.py`: secret-safe Simple Auth password initialization and DB migration.
- `README.md`: exact configuration, start/stop/reset, and offline validation procedures.

Airflow uses PostgreSQL for scheduler metadata and shared application SQLite for
project/editor state. Its review stage calls the same implemented
LangGraph-backed `ReviewAgentService` as local mode; graph state is not placed
in XCom.

## Runtime Directories

These directories are local runtime state and should not be committed:

- `.venv/`
- `.tools/`
- `input/`
- `cuts/`
- `metadata/`
- `transcripts/`
- `models/`
- `outputs/`
- `data/projects/`
- `data/projects/{project_id}/workspace/`
- Airflow logs/config/database files
- frontend `node_modules/` and `dist/`
- Python `__pycache__/` and temporary test/smoke directories

SQLite files under `data/` are runtime state unless a test explicitly creates a temporary database outside the repo.

## Generated Artifacts

Generated files include media downloads, transcripts, candidate JSON, cut clips, subtitle files, render outputs, benchmark outputs, local databases, and Airflow runtime files. They are ignored by `.gitignore`.

The committed demo candidate file is `examples/top_windows.example.json`.

## Tests

Tests live in `tests/` and use `unittest`. They cover deterministic stages,
project/API behavior, provider boundaries, LangGraph routes, local/Airflow
orchestration, timeouts/cancellation, and disposable release-smoke persistence.

Run the local validation gate:

```powershell
.\scripts\run_tests.ps1
```

Or directly:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```
