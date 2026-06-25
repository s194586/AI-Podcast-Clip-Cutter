# Manager Notes

`manager.py` is the local CLI entrypoint for preparing podcast short candidates before human review in the browser editor.

## Local Run

```powershell
.\.venv\Scripts\python.exe manager.py --url "https://www.youtube.com/watch?v=..." --content-type auto --ai-mode local_only --subtitle-checker-mode local_only
```

`--content-type auto` is accepted for convenience. It routes to the same podcast/talking-head pipeline as `--content-type podcast`.

## Workflow Steps

1. Prepare local working folders.
2. Download or reuse source media in `input/`.
3. Transcribe with Faster-Whisper.
4. Run subtitle/timing checks.
5. Write a podcast content profile.
6. Score transcript-aware podcast candidate windows.
7. Optionally use Gemini for reranking or correction.
8. Write draft windows to `top_windows.json`.
9. Cut raw 9:16 clips with `cutter.py`.
10. Burn subtitles with `subtitler.py`.

## Editor Handoff

After the pipeline creates draft candidates, run:

```powershell
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --reload --port 8000
```

Open `http://127.0.0.1:8000`.

The editor imports candidates from `top_windows.json` when needed, then stores user edits and render outputs in:

```text
data/projects/local/project_state.json
```

## Generated Files

Do not commit local media or generated outputs:

- `.env`
- `.venv/`
- `input/`
- `cuts/`
- `metadata/`
- `transcripts/`
- `models/`
- `outputs/`
- `data/projects/`
- `top_windows.json`
