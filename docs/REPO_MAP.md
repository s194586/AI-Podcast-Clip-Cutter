# Repository Map

This project is a local-first podcast clip cutter. The media pipeline proposes candidate clips, the FastAPI editor persists review state in SQLite, and the Gemini transcript boundary reviewer can refine candidate start/end boundaries before a human renders final shorts.

## `manager.py`

`manager.py` is the local CLI orchestrator for the deterministic preparation pipeline. It creates runtime folders, downloads or reuses source media, runs local transcription, writes a podcast content profile, generates candidate windows, cuts raw vertical clips, and burns subtitles.

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

## `apps/api`

`apps/api` is the FastAPI editor backend.

- `main.py`: route definitions and static frontend mounting.
- `db/`: SQLAlchemy database setup, models, and repositories.
- `services/`: project, clip, artifact, render, legacy import, and compatibility service layers.
- `orchestration/`: local project pipeline abstraction, stage parser, job recovery, and subprocess-backed `LocalPipelineOrchestrator`.
- `tools/import_local_project.py`: CLI import helper for refreshing SQLite from local pipeline outputs.
- `static/`: current browser editor implemented with static HTML/CSS/JavaScript.

The compatibility endpoints still power the static editor. Project-specific endpoints now support create/start/status/logs/cancel, project clip editing, single/batch Gemini review, and project-specific render.

## `apps/review_agent`

`apps/review_agent` contains the transcript boundary reviewer.

- `context.py`: builds compact transcript context and numbered boundary options around a clip.
- `providers.py`: local stub and Gemini provider adapters.
- `schemas.py`: typed review contracts and result models.
- `service.py`: loads clips, calls the selected provider, validates option indexes, applies safe reviewed boundaries, and persists evaluations.
- `tools.py`: transcript loading, privacy pattern checks, and evaluation persistence helpers.

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

## Current Static Frontend

The current editor lives in `apps/api/static`. It is intentionally not redesigned yet. It supports a minimal Project Flow panel plus loading clips, previewing source video, manual slider correction, accept/reject, single/batch AI review, and final render actions.

## `apps/web`

`apps/web` contains the React Product UI v0.5.

- `src/api/`: typed FastAPI client split by health, projects, clips, review, render, and errors.
- `src/components/`: app shell, status badges, progress display, and common state blocks.
- `src/pages/`: dashboard, new project, processing overview, clip editor, and exports routes.
- `src/test/`: Vitest setup and route-level test helpers.

The app runs with Vite and proxies local backend calls to `http://127.0.0.1:8010`. It is not mounted by FastAPI yet; `apps/api/static` remains the fallback UI.

## `orchestration/airflow`

Airflow is optional and isolated from the main requirements.

- `dags/podcast_pipeline_dag.py`: DAG wiring.
- `pipeline_tasks.py`: Python task helpers for download, transcription, candidate generation, SQLite import, and Gemini batch review.
- `README.md`: Airflow-specific setup and run notes.

Install Airflow only when needed with `requirements-airflow.txt`.

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

SQLite files under `data/` are runtime state unless a test explicitly creates a temporary database outside the repo.

## Generated Artifacts

Generated files include media downloads, transcripts, candidate JSON, cut clips, subtitle files, render outputs, benchmark outputs, local databases, and Airflow runtime files. They are ignored by `.gitignore`.

The committed demo candidate file is `examples/top_windows.example.json`.

## Tests

Tests live in `tests/` and use `unittest`.

Run the local validation gate:

```powershell
.\scripts\run_tests.ps1
```

Or directly:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```
