# Podcast Shorts Cutter

Local-first human-in-the-loop editor for turning long podcast, interview, and talking-head videos into vertical short clips.

The system does not try to fully automate editorial judgment. It proposes draft clips, shows the user why they were selected, lets the user adjust start/end boundaries, and renders final MP4 files only after review.

## Core Pipeline

```text
URL or local video
  -> download/reuse source media
  -> Faster-Whisper transcription
  -> speaker attribution / diarization
  -> podcast candidate scoring
  -> optional Gemini rerank/correction
  -> draft candidates
  -> human review in web editor
  -> 9:16 render + burned subtitles
```

## Main Modules

```text
manager.py              Local CLI orchestrator
download_content.py     Downloads source media with yt-dlp
transcribe.py           Creates final_transcript.json with Faster-Whisper
content_classifier.py   Podcast-only compatibility profile writer
analyze_virals.py       Scores podcast windows and writes draft candidates
local_scoring.py        Podcast heuristics and local ranking
cutter.py               Renders 9:16 raw clips
subtitler.py            Burns subtitles into rendered clips
apps/api                FastAPI editor backend
apps/api/static         Browser review UI
```

`analyze_virals.py` still keeps its historical filename for compatibility, but the active product direction is podcast-only.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

FFmpeg and FFprobe must be available in `PATH`.

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
data/projects/local/project_state.json
```

That manifest stores edited start/end times, accept/reject state, render status, and output paths.

## Run The Editor

```powershell
python -m uvicorn apps.api.main:app --reload --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

The editor can:

- load draft podcast candidates,
- preview the source video,
- adjust start and end,
- accept or reject clips,
- render final short clips,
- persist review state in `project_state.json`.

## Product Direction

```text
AI suggests -> human edits -> app renders
```

This is now a podcast shorts cutter, not a general gameplay/tutorial/commentary viral cutter.
