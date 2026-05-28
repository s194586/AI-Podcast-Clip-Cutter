# New Podcast Case Workflow

The benchmark is now podcast-only. Add only podcast, interview, conversation or talking-head materials.

## 1. Add A Case

Use a local source file:

```powershell
.\.venv\Scripts\python.exe tools\add_benchmark_case.py --case-id my_podcast_case --video-path D:\Videos\podcast.mp4 --content-type podcast --review-batch podcast_only_v1 --notes "new podcast/talking-head test"
```

This creates:

- `benchmarks/assets/<case_id>/input/`
- `benchmarks/assets/<case_id>/transcripts/`
- `benchmarks/assets/<case_id>/metadata/`
- a `podcast` entry in `benchmarks/cases.json`

Useful flags:

- `--source-url` keeps the original source reference
- `--force` overwrites an existing case definition

## 2. YouTube Assets

`tools\add_benchmark_case.py` expects local files. For YouTube, download one reasonable-quality MP4 and MP3 into:

```text
benchmarks/assets/<case_id>/input/source.mp4
benchmarks/assets/<case_id>/input/source.mp3
```

Store metadata and heatmap here:

```text
benchmarks/assets/<case_id>/metadata/source.info.json
benchmarks/assets/<case_id>/metadata/heatmap.json
```

If YouTube does not provide a heatmap, a placeholder heatmap is acceptable. The benchmark report will flag that limitation.

## 3. Run Local Benchmark

```powershell
.\.venv\Scripts\python.exe tools\run_local_benchmark.py --review-batch podcast_only_v1
```

The helper runs local-only selection/subtitle checks by default and exports the dashboard automatically.

## 4. Open Dashboard

```powershell
start benchmarks\review_dashboard.html
```

## 5. Manual Review Focus

For each clip, check:

- logical start
- enough context before the answer or thesis
- clear development and payoff
- no sentence cut in the middle
- subtitle sync
- readable subtitle line breaks
- stable crop and speaker continuity
- whether the clip tells a short self-contained story

## 6. Do Not Commit Generated Artifacts

Do not commit:

- `benchmarks/assets/`
- `benchmarks/runs/`
- `benchmarks/results.json`
- `benchmarks/report.md`
- `benchmarks/review_dashboard.html`
- `benchmarks/human_reviews.jsonl`
- `benchmarks/human_review_template.csv`
- `temp/`
- `outputs/`
