# New Case Workflow

This repository is currently optimized for a clean local-first benchmark workflow:

1. add a new local case
2. run a local-only benchmark
3. export the review dashboard
4. do manual review

External providers and semantic prototypes remain optional / experimental. They are not required for the default flow.

## 1. Add a new local benchmark case

Example:

```powershell
.\.venv\Scripts\python.exe tools\add_benchmark_case.py --case-id my_gameplay_01 --video-path D:\Videos\gameplay.mp4 --content-type gameplay --review-batch local_v1 --notes "new local test"
```

This command:
- creates `benchmarks/assets/<case_id>/`
- creates `input/`, `transcripts/`, and `metadata/`
- copies the local video into `benchmarks/assets/<case_id>/input/source.mp4` or the matching extension
- updates `benchmarks/cases.json`

Useful flags:
- `--source-url` if you want to keep the original source reference
- `--force` if you really want to overwrite an existing case definition

## 2. Run a local-only benchmark

Direct command:

```powershell
.\.venv\Scripts\python.exe benchmark.py --review-batch local_v1 --ai-mode local_only --subtitle-checker-mode local_only
```

Convenience helper:

```powershell
.\.venv\Scripts\python.exe tools\run_local_benchmark.py --review-batch local_v1
```

Optional filters:

```powershell
.\.venv\Scripts\python.exe tools\run_local_benchmark.py --review-batch local_v1 --top 3 --case my_gameplay_01
```

## 3. Generate the dashboard

If you ran `tools\run_local_benchmark.py`, the dashboard is exported automatically.

Manual command:

```powershell
.\.venv\Scripts\python.exe review_dashboard.py export-html --results benchmarks\results.json --output benchmarks\review_dashboard.html
```

Open it:

```powershell
start benchmarks\review_dashboard.html
```

## 4. Review clips

Use the dashboard and/or:
- fill `benchmarks/human_review_template.csv`
- append reviews into `benchmarks/human_reviews.jsonl`

Recommended review focus:
- context completeness
- ending/payoff
- subtitle readability
- speaker stability
- whether the clip tells a self-contained story

## 5. What not to commit

Do not commit generated artifacts:
- `benchmarks/assets/`
- `benchmarks/runs/`
- `benchmarks/results.json`
- `benchmarks/report.md`
- `benchmarks/review_dashboard.html`
- `benchmarks/human_reviews.jsonl`
- `benchmarks/human_review_template.csv`
- `temp/`
- `outputs/`

These are intentionally ignored so the repo stays clean between benchmark batches.
