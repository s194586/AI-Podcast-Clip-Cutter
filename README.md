# AI Podcast Clip Cutter

AI Podcast Clip Cutter is a local-first MVP for turning long podcast, interview and talking-head videos into vertical short-form clips for TikTok, YouTube Shorts and Reels.

The active product scope is podcast-only. Gameplay, tutorial, commentary and generic strategies are kept only as legacy code where removing them would risk unrelated imports; they are not selected by the current pipeline, benchmark or default routing.

## MVP Target

- AI Podcast Clip Cutter
- Talking-head short generator
- Long spoken material: podcasts, interviews, conversations and solo talking-head videos
- Output: 9:16 clips with stable face-focused framing and one consistent subtitle style

## Pipeline

1. Prepare or download source media.
2. Transcribe locally with Faster-Whisper when no reusable transcript exists.
3. Run diarization as internal analysis.
4. Route the material as `podcast`.
5. Score candidate clips for context, answer/thesis, payoff, sentence-safe boundaries and speaker continuity.
6. Optionally use Gemini for experimental rerank/correction modes.
7. Cut vertical 9:16 clips.
8. Burn subtitles with one stable podcast style.

Technical modes such as `local_only`, `gemini_optional` and `gemini_enabled` still control whether AI/API calls are allowed. They do not change the podcast-only content scope.

## Key Files

```text
manager.py                 End-to-end workflow entrypoint
analyze_virals.py          Candidate generation, scoring and optional rerank
cutter.py                  Clip cutting and 9:16 rendering
subtitler.py               Stable podcast subtitles
content_classifier.py      Podcast-only routing gate
strategies/                Active registry selects the podcast strategy
layout/                    Active layout resolves to podcast face crop
benchmark.py               Podcast-only benchmark runner
benchmarks/cases.json      Podcast benchmark cases
benchmarks/review_dashboard.html  Latest review dashboard after a benchmark run
```

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

FFmpeg and FFprobe must be available in `PATH` or through the bundled tools used by the project scripts.

## Run Locally

```powershell
.\.venv\Scripts\python.exe manager.py --content-type auto --ai-mode local_only --subtitle-checker-mode local_only
```

`auto` is accepted for convenience, but the current MVP routes it to `podcast`.

## Benchmark

Run the podcast-only benchmark batch:

```powershell
.\.venv\Scripts\python.exe tools\run_local_benchmark.py --review-batch podcast_only_v1
```

Outputs:

- `benchmarks/results.json`
- `benchmarks/report.md`
- `benchmarks/human_review_template.csv`
- `benchmarks/review_dashboard.html`
- `benchmarks/runs/<timestamp>/...`

Open the dashboard:

```powershell
start benchmarks\review_dashboard.html
```

## Human Review Focus

For each clip, judge:

- whether it starts logically
- whether it has enough context before the answer or thesis
- whether it develops into a clear point
- whether the ending has payoff or closure
- whether no sentence is cut in the middle
- whether subtitles are synchronized and readable
- whether framing and speaker continuity are stable
- whether the clip tells a short self-contained story

## Notes

Generated media, benchmark runs, local model caches and secrets are intentionally excluded from version control. Do not commit `.env`, `.venv`, local source media or generated benchmark artifacts unless explicitly planned.
