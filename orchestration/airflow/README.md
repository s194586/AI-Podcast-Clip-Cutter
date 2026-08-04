# Airflow Orchestration

Airflow is an optional orchestration mode. Local mode remains the default and
continues to use `LocalPipelineOrchestrator` plus `apps.pipeline.entrypoint`.
Airflow mode uses Apache Airflow 3.3.0, PostgreSQL metadata, LocalExecutor, a
separate DAG processor, and the Airflow 3 stable REST API.

The DAG is `podcast_clip_pipeline`. Its tasks are the actual pipeline stages:

```text
prepare_workspace -> download_source -> detect_heatmap_peaks -> transcribe
-> validate_transcript -> generate_candidates -> import_candidates
-> review_boundaries -> mark_ready
```

Tasks call `PipelineStageExecutor` and `PipelineStageRegistry`. They never call
`manager.py` or run a whole `PipelineRunner` inside one Airflow task. Rendering
is intentionally absent and remains a human-triggered editor action.

## Configure

Docker Desktop with Linux containers and Compose v2 is required. From the repo
root, create the ignored runtime env file:

```powershell
Copy-Item .\orchestration\airflow\airflow.env.example .\orchestration\airflow\.env.airflow
notepad .\orchestration\airflow\.env.airflow
New-Item -ItemType Directory -Force .\data | Out-Null
```

Replace all `change-me` values with separate random secrets. Keep the database
password URL-safe. The env file is excluded from Git and the Docker build. The
Simple Auth Manager password is written with mode `0600` to the
`airflow-secrets` volume and is not printed by the initializer.

Keep the normal `CLIP_REVIEW_MODE=gemini` setting. A missing `GEMINI_API_KEY`
does not block stack startup or projects with `auto_review=false`; semantic
review requires a valid key and never silently falls back. `local_stub` is
reserved for controlled automated tests such as `smoke_docker.ps1`.

### Pyannote Community-1

Accept the gated Community-1 terms on Hugging Face and create a read-only token
before running transcription with `DIARIZATION_MODE=pyannote`. Put the token in
the ignored `.env.airflow` file as `HF_TOKEN`; Compose passes it only to
`airflow-scheduler`, where LocalExecutor runs the task. The API server, init
container, and DAG processor do not receive it. An empty token still allows the
stack and DAG parser to start; the transcription task fails explicitly if
Pyannote is requested without access.

The scheduler mounts the named `huggingface-cache` volume at
`/opt/airflow/huggingface`. After the pinned Community-1 revision has been
downloaded once, `HF_HUB_OFFLINE=1` enforces cache-only operation. A cache miss
in offline mode is a transcription error. The image build installs CPU wheels
but never downloads model weights. Set `DIARIZATION_MODE=off` to bypass model
loading and speaker attribution entirely. This MVP does not support Pyannote on
CUDA.

Output labels are anonymous and provide neither speaker identity nor face
association. Exclusive diarization selects one primary speaker and does not
fully represent overlapping speech. Speaker-boundary splits change canonical
time ranges, so regenerating the transcript can change canonical segment IDs.

The opt-in real-model check is documented in the repository README. It requires
`RUN_PYANNOTE_INTEGRATION=1`, `PYANNOTE_TEST_AUDIO`, and `HF_TOKEN`, makes no
Gemini request, and is skipped by default. Until it is run, manual end-to-end
transcription is the remaining real-model gate.

### Optional local HTTPS-inspection root

If antivirus or a corporate proxy replaces public HTTPS certificates with a
root already trusted by Windows, export only that public root certificate as
PEM to this ignored, fixed location:

```text
orchestration/airflow/secrets/custom-ca/root-ca.pem
```

Then set `CUSTOM_CA_REQUIRED=true` in the ignored `.env.airflow` file. Compose
mounts that directory read-only. Container startup accepts only the fixed
`root-ca.pem` name, rejects symlinks, oversized files, invalid/expired
certificates, and certificates without `CA:TRUE`, then updates the Linux trust
store before dropping privileges to the Airflow user. Certificate contents are
not logged. yt-dlp is configured to use this system store with certificate
verification enabled. Do not copy the Windows certificate store or commit a
local root.

The canonical build does not require this optional file and never copies it
into an image. If HTTPS inspection also affects image-build downloads, configure
Docker Desktop to trust the proxy/root before building. No `--trusted-host` or
certificate bypass is used.

## Start

Stop any host FastAPI process using the same SQLite file before Airflow mode.
The repository-level Compose file is the canonical demo stack and also starts
the containerized React/Nginx frontend. Build, initialize, start, and wait for
healthchecks with one command:

```powershell
docker compose --env-file .\orchestration\airflow\.env.airflow up --build --detach --wait
docker compose --env-file .\orchestration\airflow\.env.airflow ps
```

Open the React UI at `http://127.0.0.1:5173`, FastAPI at
`http://127.0.0.1:8010`, and Airflow at `http://127.0.0.1:8080`. FastAPI
submits and reconciles DAG runs; do not trigger the DAG manually without its
versioned application run configuration.

## Stop And Reset

Normal stop preserves PostgreSQL metadata, Airflow logs/secrets, application
SQLite, project workspaces, and media:

```powershell
docker compose --env-file .\orchestration\airflow\.env.airflow down
```

To reset only Airflow infrastructure metadata and generated Airflow credentials,
first stop the stack, then remove the three named Airflow volumes explicitly.
This does not remove `data/`:

```powershell
docker compose --env-file .\orchestration\airflow\.env.airflow down
docker volume rm ai-podcast-clip-cutter_airflow-postgres ai-podcast-clip-cutter_airflow-logs ai-podcast-clip-cutter_airflow-secrets
```

Never add `-v` to routine `docker compose down` commands. Never delete `data/`
as part of an Airflow reset.

## Offline Infrastructure Check

Use an empty ignored data directory to validate infrastructure without opening
the real application database or any project workspace:

```powershell
New-Item -ItemType Directory -Force .\.airflow-validation-data | Out-Null
$env:APP_DATA_HOST_PATH = ".\.airflow-validation-data"
docker compose --env-file .\orchestration\airflow\.env.airflow config --quiet
docker compose --env-file .\orchestration\airflow\.env.airflow up airflow-init
docker compose --env-file .\orchestration\airflow\.env.airflow up -d postgres airflow-api-server airflow-scheduler airflow-dag-processor app-api
docker compose --env-file .\orchestration\airflow\.env.airflow exec airflow-scheduler airflow dags list
docker compose --env-file .\orchestration\airflow\.env.airflow exec airflow-scheduler airflow dags show podcast_clip_pipeline
docker compose --env-file .\orchestration\airflow\.env.airflow down
Remove-Item -LiteralPath .\.airflow-validation-data -Recurse -Force
Remove-Item Env:APP_DATA_HOST_PATH
```

These commands parse and inspect the DAG only. They do not trigger a DAG run,
download media, transcribe, call Gemini, or render clips.

## Retry And Cancellation

Deterministic/reusable stages have bounded Airflow retries. The review task has
zero Airflow retries so provider quota errors such as HTTP 429 cannot produce a
retry storm. An explicit project retry creates a new application job and DAG
run while idempotent stages reuse valid artifacts and preserve reviewed/user
boundaries.

Cancellation is persisted in application SQLite first. Tasks cooperatively
check that flag; FastAPI also asks Airflow to fail active task instances and the
DAG run. Airflow cancellation is best effort and may not kill external child
work instantly, but terminal guards prevent a late task update from changing a
cancelled project to ready.

SQLite uses WAL, a 30-second busy timeout, and foreign keys. It is appropriate
for this single-project-at-a-time local deployment, not a horizontally scaled
API. Moving application persistence to PostgreSQL would be part of an optional
production deployment.

The shared `ReviewAgentService` uses the implemented per-clip LangGraph
boundary-review workflow. Graph state is not passed through XCom, and
`review_boundaries` retains zero Airflow retries. When `auto_review=false`, the
stage bypasses the graph and provider safely.
