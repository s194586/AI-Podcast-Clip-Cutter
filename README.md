# Podcast Shorts Cutter

AI Podcast Clip Cutter is a local-first podcast automation toolkit for turning long podcast, interview, and talking-head videos into vertical short clips.

The core media processing workflow is deterministic and is assembled from reusable typed Python stages. A separate Clip Review Agent can send compact transcript context to Gemini for semantic temporal boundary review before a human renders final shorts.

The system does not claim viral prediction or fully automate editorial judgment. It proposes draft clips, shows why they were selected, lets the user adjust start/end boundaries, and renders final MP4 files only after review.

## Core Pipeline

```text
URL or local video
  -> download/reuse source media
  -> Faster-Whisper transcription
  -> speaker attribution / diarization
  -> podcast candidate scoring
  -> optional Gemini rerank/correction
  -> draft candidates
  -> optional Gemini transcript boundary review
  -> human review in web editor
  -> 9:16 render + burned subtitles
```

The media pipeline is not an agent. The fixed processing sequence remains deterministic:

```text
download media -> transcribe -> generate candidates -> score candidates -> prepare editor project -> render
```

The review agent is separate. Candidate generation finds and ranks possible clips. Gemini does not rank clips or inspect video frames; it only decides whether transcript-aligned start/end boundaries make the candidate coherent as a standalone short.

```mermaid
flowchart LR
  A[candidate generation] --> B[transcript context extraction]
  B --> C[Gemini semantic boundary review]
  C --> D[reviewed boundaries]
  D --> E[editor sliders]
  E --> F[optional user adjustment]
  F --> G[final render]
```

## Main Modules

```text
manager.py              Backwards-compatible thin CLI
apps/pipeline           Typed contexts, events, runner, and reusable stages
download_content.py     Downloads source media with yt-dlp
transcribe.py           Creates final_transcript.json with Faster-Whisper
content_classifier.py   Podcast-only compatibility profile writer
analyze_virals.py       Scores podcast windows and writes draft candidates
local_scoring.py        Podcast heuristics and local ranking
cutter.py               Renders 9:16 raw clips
subtitler.py            Burns subtitles into rendered clips
apps/api                FastAPI editor backend
apps/api/static         Browser review UI
apps/review_agent       Transcript boundary reviewer with Gemini and local_stub modes
orchestration/airflow   Optional Airflow DAG, task adapters, image, and operations
```

`analyze_virals.py` still keeps its historical filename for compatibility, but the active product direction is podcast-only.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

FFmpeg and FFprobe must be available in `PATH`.

Copy `.env.example` when you want a local environment template. The boundary reviewer uses:

```powershell
$env:TRANSCRIPTION_DEVICE = "auto"  # auto, cuda, or cpu
$env:TRANSCRIPTION_COMPUTE_TYPE = "auto"  # CUDA default float16, CPU default int8
$env:CLIP_REVIEW_MODE = "local_stub"  # or "gemini"
$env:GEMINI_API_KEY = "..."
$env:GEMINI_MODEL = "gemini-3.5-flash"
$env:CLIP_REVIEW_CONTEXT_SECONDS = "20.0"
$env:GEMINI_REQUEST_TIMEOUT_SECONDS = "300"
$env:GEMINI_BATCH_TIMEOUT_SECONDS = "1800"
```

`TRANSCRIPTION_DEVICE=auto` prefers CUDA when CTranslate2 reports CUDA devices. If CUDA execution fails because runtime libraries such as cuBLAS, cuDNN, the CUDA runtime, or the CUDA driver cannot be loaded, transcription logs a concise warning and retries once on CPU with `compute_type=int8`. `TRANSCRIPTION_DEVICE=cuda` is explicit and fails with an actionable message instead of silently falling back.

`GEMINI_API_KEY` is required only when `CLIP_REVIEW_MODE=gemini`. The app never logs or stores the key.

Each Gemini boundary-review attempt is bounded by `GEMINI_REQUEST_TIMEOUT_SECONDS` using the official SDK HTTP timeout and a killable process deadline. `GEMINI_BATCH_TIMEOUT_SECONDS` bounds the full project review. A single provider timeout becomes a saved `manual_review` result and later clips continue; invalid configuration, an exhausted batch deadline, explicit cancellation, or technical failure of every clip ends the stage without leaving the project running.

Gemini free-tier quota or rate limits may return HTTP 429. This is an external review-provider limitation, not a pipeline-correctness failure: the app records a technical `manual_review` result, does not claim Gemini success or silently use `local_stub`, and lets you retry review later.

## Run The Local Pipeline

```powershell
python manager.py --url "https://www.youtube.com/watch?v=..." --content-type auto --ai-mode local_only --subtitle-checker-mode local_only
```

The automatic analysis still writes draft windows to:

```text
top_windows.json
metadata/cutting_logic.json
```

After the editor imports those candidates, the working source of truth becomes:

```text
data/podcast_cutter.db
```

SQLite stores projects, clips, jobs, clip evaluations, and generated artifact metadata. It preserves edited start/end times, accept/reject state, render status, scores, selection reasons, review recommendations, and output paths.

`data/projects/local/project_state.json` is now a legacy compatibility import format only. If the database is empty, the editor can import that file once. After SQLite contains project data, SQLite wins and the JSON file is not rewritten by the editor.

## Refresh Local SQLite After Running Pipeline

If the pipeline generated new `project_state.json` or `top_windows.json` files but the editor still shows stale demo clips, refresh the local SQLite database:

```powershell
python -m apps.api.tools.import_local_project --reset
```

This command only replaces SQLite project/clip/artifact/evaluation rows. It does not delete local media, transcripts, cuts, metadata, or `data/projects/local/project_state.json`.

## Run The Editor

### Legacy FastAPI Static UI

```powershell
python -m uvicorn apps.api.main:app --reload --port 8010
```

Open:

```text
http://127.0.0.1:8010
```

The legacy editor in `apps/api/static` remains the FastAPI fallback UI.

### React Product UI v0.5

The new React frontend lives in `apps/web` and runs separately through Vite during validation:

```powershell
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --reload --port 8010
.\scripts\dev_web.ps1
```

Or:

```powershell
.\scripts\dev_full_stack.ps1
```

The React app provides:

- load draft podcast candidates,
- create a project from a YouTube URL,
- start the local project pipeline,
- show coarse stage/progress and a safe technical log tail,
- preview the source video,
- adjust start and end,
- accept or reject clips,
- review all clips with AI transcript-boundary review,
- render final short clips,
- persist review state in SQLite.

See [docs/REACT_FRONTEND.md](docs/REACT_FRONTEND.md).

## Project Flow V1

The normal local workflow can now be driven from FastAPI:

```text
POST /projects
-> POST /projects/{project_id}/start
-> LocalPipelineOrchestrator starts apps.pipeline.entrypoint
-> PipelineRunner processes data/projects/{project_id}/workspace/
-> reusable stages import candidates and optionally review boundaries
-> project status becomes ready
-> existing editor opens that project's clips
```

`manager.py` remains available for backwards-compatible CLI use, including root-level defaults, `--workspace-dir`, skip flags, transcription options, and `--analysis-only`. The product worker no longer invokes it. Instead, the local orchestrator runs `python -m apps.pipeline.entrypoint`, which uses the same `PipelineRunner` and stage services in an isolated project workspace.

See [docs/PROJECT_FLOW.md](docs/PROJECT_FLOW.md) and [docs/PIPELINE_SERVICES.md](docs/PIPELINE_SERVICES.md).

## Optional Airflow Mode

Local orchestration remains the default. Start the included Docker Compose stack
when durable DAG-run visibility and per-stage retries are useful. The stack pins
Airflow 3.3.0, PostgreSQL 16.14, LocalExecutor, the API server, scheduler, and DAG
processor. It does not include Redis, Celery workers, or a triggerer because the
DAG uses ordinary Python tasks and intentionally limits active work.

```powershell
Copy-Item .\orchestration\airflow\airflow.env.example .\orchestration\airflow\.env.airflow
docker compose --env-file .\orchestration\airflow\.env.airflow build
docker compose --env-file .\orchestration\airflow\.env.airflow up airflow-init
docker compose --env-file .\orchestration\airflow\.env.airflow up -d
```

See [orchestration/airflow/README.md](orchestration/airflow/README.md) before
starting, stopping, resetting, or running an isolated infrastructure check.

## Run Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Or run the local validation helper:

```powershell
.\scripts\run_tests.ps1
```

## Persistence

The default database URL is:

```text
sqlite:///data/podcast_cutter.db
```

Override it with:

```powershell
$env:PODCAST_CUTTER_DB_URL = "sqlite:///C:/path/to/podcast_cutter.db"
```

The current browser editor still uses the compatibility endpoints:

```text
GET /project
GET /clips
PATCH /clips/{clip_id}
POST /clips/{clip_id}/accept
POST /clips/{clip_id}/reject
POST /render
```

The first project-oriented API is also available:

```text
POST /projects
POST /projects/{project_id}/start
GET /projects
GET /projects/{project_id}
GET /projects/{project_id}/clips
GET /projects/{project_id}/status
GET /projects/{project_id}/logs
POST /projects/{project_id}/cancel
PATCH /projects/{project_id}/clips/{clip_id}
POST /projects/{project_id}/clips/{clip_id}/accept
POST /projects/{project_id}/clips/{clip_id}/reject
POST /clips/{clip_id}/review
GET /clips/{clip_id}/review
POST /projects/{project_id}/clips/{clip_id}/review
POST /projects/{project_id}/review-clips
POST /projects/{project_id}/render
GET /projects/{project_id}/exports
GET /projects/{project_id}/exports/{artifact_id}/download
```

Compatibility endpoints use the earliest SQLite project by database id as the default local project.

## Clip Review Agent

Default mode is an explicit offline stub and does not require API keys:

```powershell
$env:CLIP_REVIEW_MODE = "local_stub"
```

Real review mode uses the official Google Gen AI SDK:

```powershell
$env:CLIP_REVIEW_MODE = "gemini"
$env:GEMINI_API_KEY = "..."
```

Gemini receives only approximately `CLIP_REVIEW_CONTEXT_SECONDS` before the candidate, transcript segments overlapping the candidate, approximately the same amount after it, and numbered start/end boundary options. It returns one of three editorial decisions: `render_ready`, `adjust_boundaries`, or `reject`, plus required non-null integer option indexes. The backend maps those indexes to segment IDs and timestamps. Backend-created `manual_review` is reserved for technical or validation failure.

Safe `render_ready` and `adjust_boundaries` decisions store `reviewed_start`/`reviewed_end`, copy those values into `edited_start`/`edited_end`, and set `boundary_source="ai_review"`. Manual slider edits later change only `edited_start`/`edited_end` and set `boundary_source="user"`. Rendering always uses edited boundaries.

Gemini does not visually crop the video. Visual 9:16 rendering remains deterministic and uses `edited_start`/`edited_end` afterward.

The browser editor has a project-level **Review all with AI** button that calls `POST /projects/{project_id}/review-clips`, reloads clips, and shows the AI-reviewed boundaries on the existing handles.

See [docs/CLIP_REVIEW_AGENT.md](docs/CLIP_REVIEW_AGENT.md).

For a codebase overview, see [docs/REPO_MAP.md](docs/REPO_MAP.md). The planned frontend migration is captured in [docs/FRONTEND_REDESIGN_PLAN.md](docs/FRONTEND_REDESIGN_PLAN.md).

## Orchestration Direction

Airflow is an optional Dockerized product orchestrator. The DAG delegates each
task to the same registered stage services as local mode and never invokes
`manager.py` or wraps the complete `PipelineRunner` in one task. LangGraph is
still deferred; Gemini review remains a direct typed `ReviewAgentService` call.

## Product Direction

```text
AI suggests -> human edits -> app renders
```

This is now a podcast shorts cutter, not a general gameplay/tutorial/commentary viral cutter.

This project demonstrates production-oriented AI engineering patterns:

- deterministic pipeline orchestration,
- Gemini transcript boundary review,
- typed review state,
- SQLite persistence,
- testable FastAPI endpoints,
- optional LLM evaluation with local fallback,
- human-in-the-loop review,
- reusable pipeline stages suitable for future external orchestration.
