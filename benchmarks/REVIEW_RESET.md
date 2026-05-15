# Review Reset

Use this flow when you want a fresh benchmark/review pass that does not mix old clips and old review artifacts with a new cutter/layout iteration.

## Dry Run

Show what would be removed without deleting anything:

```powershell
.\.venv\Scripts\python.exe tools\clean_review_artifacts.py
```

## Apply Cleanup

Archive old human reviews and remove generated benchmark/review artifacts:

```powershell
.\.venv\Scripts\python.exe tools\clean_review_artifacts.py --archive-reviews --apply
```

Archived reviews are stored in `benchmarks/archive/` and should be treated as historical review data for previous runs.

## Optional Flags

Keep `benchmarks/results.json` and `benchmarks/report.md`:

```powershell
.\.venv\Scripts\python.exe tools\clean_review_artifacts.py --keep-results
```

Keep `benchmarks/review_dashboard.html`:

```powershell
.\.venv\Scripts\python.exe tools\clean_review_artifacts.py --keep-dashboard
```

## Regenerate Fresh Benchmark Artifacts

Run a new benchmark from scratch after cleanup:

```powershell
.\.venv\Scripts\python.exe benchmark.py --ai-mode local_only --subtitle-checker-mode local_only
```

## Regenerate Dashboard

Export a new dashboard from the fresh benchmark:

```powershell
.\.venv\Scripts\python.exe review_dashboard.py export-html --results benchmarks\results.json --output benchmarks\review_dashboard.html
start benchmarks\review_dashboard.html
```
