# Architecture

Podcast Shorts Cutter is a local-first, human-in-the-loop editor for podcast and talking-head material.

The pipeline proposes draft clips from a long source video. The browser editor lets a user review candidates, adjust start/end times, accept or reject clips, and render final short-form MP4 files.

The main media pipeline is deterministic. It is not an agent. The agentic component is a separate Clip Review Agent that evaluates already-generated clip candidates and gives advisory recommendations to a human editor.

## Pipeline Modules

`manager.py` orchestrates the CLI workflow. It prepares folders, finds or downloads media, runs transcription, builds a podcast profile, scores candidate moments, cuts clips, and applies subtitles.

`transcribe.py` creates transcript JSON from source audio using Faster-Whisper. The transcript is the main input for candidate scoring, boundary checks, and subtitles.

`content_classifier.py` is now a podcast-only compatibility module. It writes `metadata/content_profile.json` so older pipeline calls still work, but it no longer routes to gameplay, tutorial, commentary, or generic strategies.

`analyze_virals.py` keeps its historical filename for compatibility. In the current product it generates and scores podcast candidate windows with local scoring and optional Gemini reranking.

`cutter.py` renders vertical 9:16 clips from the original input video.

`subtitler.py` burns subtitles into rendered clips.

## Pipeline Orchestration

The deterministic workflow can be orchestrated by Apache Airflow through `orchestration/airflow/dags/podcast_pipeline_dag.py`.

The DAG prepares reviewed candidate clips:

```text
validate project config
-> download media
-> transcribe audio
-> generate candidates
-> import candidates to SQLite
-> review top candidates
-> mark project ready
```

Rendering remains human-triggered in the editor. The Airflow DAG does not render every candidate automatically.

Airflow is optional. It is installed from `requirements-airflow.txt` and is not required for the FastAPI app or unit tests.

## Clip Review Agent

`apps/review_agent` contains the Clip Review Agent PoC.

The agent reviews one stored clip candidate at a time. It uses typed state and deterministic tools for:

- transcript context retrieval,
- candidate feature inspection,
- sensitive-pattern checks,
- boundary advice,
- crop advice metadata,
- final recommendation generation,
- saving `ClipEvaluation` rows in SQLite.

The review workflow is bounded:

```text
load_candidate
-> retrieve_context
-> evaluate_quality
-> route_context_decision
-> retrieve_more_context at most once
-> check_privacy
-> suggest_boundaries
-> suggest_crop
-> final_recommendation
-> save_evaluation
```

Default mode is `local_only`, which requires no API keys. `llm_optional` is available behind a service interface and falls back to deterministic evaluation if no provider client or key is available.

## Editor Backend

`apps/api` exposes the local editor backend with FastAPI.

- `GET /health` confirms the API is running.
- `GET /project` returns a compatibility manifest for the current default SQLite project.
- `GET /clips` loads clips for the default SQLite project.
- `PATCH /clips/{clip_id}` saves edited start/end times.
- `POST /clips/{clip_id}/accept` marks a clip as accepted.
- `POST /clips/{clip_id}/reject` marks a clip as rejected.
- `POST /render` validates adjusted bounds, calls `cutter.py`, runs `subtitler.py` when a transcript is available, updates the clip render status, and records output files as artifacts.
- `POST /projects` creates a project record without starting the pipeline.
- `GET /projects` lists projects newest first with clip counts.
- `GET /projects/{project_id}` returns project metadata.
- `GET /projects/{project_id}/clips` returns clips for one project.
- `GET /projects/{project_id}/status` returns project processing status, clip count, and latest failed job error.
- `POST /clips/{clip_id}/review` evaluates a clip in the default project.
- `GET /clips/{clip_id}/review` returns the latest saved evaluation for a clip.
- `POST /projects/{project_id}/clips/{clip_id}/review` evaluates a clip in a specific project.
- `GET /projects/{project_id}/clips/{clip_id}/review` returns the latest saved project-specific evaluation.

`apps/api/db` owns SQLAlchemy setup, models, and repository helpers.

`apps/api/services/project_service.py`, `clip_service.py`, `artifact_service.py`, and `legacy_import_service.py` keep routes thin and isolate persistence behavior.

`apps/api/services/project_state.py` remains as a legacy JSON compatibility helper.

`apps/api/services/clips.py` still normalizes draft windows into editor-ready clip records and validates trim ranges. Its public load/update/status/render persistence functions now delegate to SQLite-backed services.

`apps/api/services/render.py` locates local input media, prepares render folders, calls the existing render scripts, and returns output paths.

## Source Of Truth

SQLite is now the application source of truth.

The default database lives at:

```text
data/podcast_cutter.db
```

Set `PODCAST_CUTTER_DB_URL` to point at another database, for example a temporary SQLite file during tests.

The database stores:

- `projects`: source URL, title, status, and source/transcript/candidate paths.
- `clips`: stable editor IDs such as `clip_001`, AI boundaries, edited boundaries, validation bounds, accept/reject status, render status, scores, reasons, features, and latest render outputs.
- `clip_evaluations`: review agent decisions, quality/context/hook/payoff/boundary scores, privacy risk, recommended action, suggested boundaries, crop advice, reasons, warnings, and raw structured result metadata.
- `jobs`: Stage 2 preparation only. The schema exists, but there is no worker, queue, polling flow, or background job system.
- `artifacts`: metadata for generated local files such as source video, transcript, candidate windows, raw clips, and subtitled clips. Video bytes are not stored in SQLite.

`project_state.json` is a legacy compatibility import format. On startup the API creates tables and runs a safe bootstrap:

```text
1. If SQLite already contains projects, use SQLite.
2. Else import data/projects/local/project_state.json if present.
3. Else import candidate windows from top_windows.json, metadata/top_windows.json, metadata/cutting_logic.json, or examples/top_windows.example.json.
4. Else leave the database empty.
```

The bootstrap does not delete or rewrite the old JSON file. Once a project exists in SQLite, old JSON and candidate files are no longer re-imported automatically.

Compatibility endpoints resolve the default local project as the earliest SQLite project by database id. Project-specific endpoints should be used when callers need a particular project.

## Product Data Flow

```text
Podcast pipeline
  -> SQLite project state
  -> Clip Review Agent evaluation metadata
  -> FastAPI
  -> current browser editor
  -> rendered artifacts
```

## Production-Oriented AI Engineering Patterns

The project demonstrates:

- deterministic pipeline orchestration,
- tool-based clip evaluation,
- typed review state,
- SQLite persistence,
- testable FastAPI endpoints,
- optional LLM evaluation,
- human-in-the-loop review,
- Airflow DAG orchestration.

It does not claim that the whole application is autonomous or multi-agent. The editor remains the final decision point before rendering.

## Removed Multi-Content Routing

The active product no longer supports separate gameplay, tutorial, commentary, or generic strategies. Those old strategy/layout files have been removed; the registry resolves to podcast behavior only.
