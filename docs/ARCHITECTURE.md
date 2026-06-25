# Architecture

Podcast Shorts Cutter is a local-first, human-in-the-loop editor for podcast and talking-head material.

The pipeline proposes draft clips from a long source video. The browser editor lets a user review candidates, adjust start/end times, accept or reject clips, and render final short-form MP4 files.

## Pipeline Modules

`manager.py` orchestrates the CLI workflow. It prepares folders, finds or downloads media, runs transcription, builds a podcast profile, scores candidate moments, cuts clips, and applies subtitles.

`transcribe.py` creates transcript JSON from source audio using Faster-Whisper. The transcript is the main input for candidate scoring, boundary checks, and subtitles.

`content_classifier.py` is now a podcast-only compatibility module. It writes `metadata/content_profile.json` so older pipeline calls still work, but it no longer routes to gameplay, tutorial, commentary, or generic strategies.

`analyze_virals.py` keeps its historical filename for compatibility. In the current product it generates and scores podcast candidate windows with local scoring and optional Gemini reranking.

`cutter.py` renders vertical 9:16 clips from the original input video.

`subtitler.py` burns subtitles into rendered clips.

## Editor Backend

`apps/api` exposes the local editor backend with FastAPI.

- `GET /health` confirms the API is running.
- `GET /project` returns the current local project manifest.
- `GET /clips` loads clips from `data/projects/local/project_state.json`, bootstrapping from `top_windows.json` when the manifest does not exist yet.
- `PATCH /clips/{clip_id}` saves edited start/end times.
- `POST /clips/{clip_id}/accept` marks a clip as accepted.
- `POST /clips/{clip_id}/reject` marks a clip as rejected.
- `POST /render` validates adjusted bounds, calls `cutter.py`, runs `subtitler.py` when a transcript is available, and records render outputs in the manifest.

`apps/api/services/project_state.py` owns the project manifest.

`apps/api/services/clips.py` normalizes draft windows into editor-ready clip records and validates trim ranges.

`apps/api/services/render.py` locates local input media, prepares render folders, calls the existing render scripts, and returns output paths.

## Source Of Truth

`top_windows.json` is an import artifact from automatic analysis.

The editor state is stored here:

```text
data/projects/local/project_state.json
```

It stores:

- source/artifact paths,
- original candidate times,
- edited start/end times,
- accept/reject state,
- render status,
- raw and subtitled output paths.

## Removed Multi-Content Routing

The active product no longer supports separate gameplay, tutorial, commentary, or generic strategies. Those old strategy/layout files have been removed; the registry resolves to podcast behavior only.
