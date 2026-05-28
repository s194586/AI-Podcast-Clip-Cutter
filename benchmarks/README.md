# Podcast Benchmarking

The benchmark corpus is now scoped to podcast/talking-head material only.

Active benchmark goals:

- verify podcast-only routing
- select clips with complete context, answer/thesis and payoff
- validate sentence-safe boundaries
- check speaker/crop stability
- check one stable subtitle style
- prepare clips for manual review

## Current Config

The default config is [cases.json](./cases.json). Every active case should use:

```json
{
  "expected_content_type": "podcast",
  "review_batch": "podcast_only_v1",
  "comparison_content_types": [],
  "include_generic_baseline": false
}
```

## Run

From the repository root:

```powershell
.\.venv\Scripts\python.exe tools\run_local_benchmark.py --review-batch podcast_only_v1
```

Useful options:

```powershell
.\.venv\Scripts\python.exe tools\run_local_benchmark.py --review-batch podcast_only_v1 --top 3
.\.venv\Scripts\python.exe benchmark.py --case podcast_j86_semantic_test --ai-mode local_only --subtitle-checker-mode local_only
```

## Outputs

- `benchmarks/results.json`
- `benchmarks/report.md`
- `benchmarks/human_review_template.csv`
- `benchmarks/review_dashboard.html`
- `benchmarks/runs/<timestamp>/...`

Open the dashboard:

```powershell
start benchmarks\review_dashboard.html
```

## Adding Media

Recommended layout:

```text
benchmarks/assets/<case_id>/
  input/
    source.mp4
    source.mp3
  metadata/
    source.info.json
    heatmap.json
  transcripts/
    final_transcript.json
```

`transcripts/final_transcript.json` is optional; if it is missing, the benchmark generates and caches a local transcript.

## Human Review

Fill `benchmarks/human_review_template.csv` or use the dashboard. Score:

- `human_relevance_score`
- `human_boundary_score`
- `human_crop_score`
- `notes`

Review the same podcast criteria for every clip: logical start, enough context, development, payoff, clean ending, subtitle sync/readability, stable crop and self-contained story.
