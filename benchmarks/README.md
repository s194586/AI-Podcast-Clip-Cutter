# Benchmarking

The benchmark corpus has been reset for a new external-model-focused iteration.

Current state:
- `benchmarks/cases.json` is intentionally empty
- archived baseline definitions live in `benchmarks/old_cases.json.bak`
- a reusable example case lives in `benchmarks/cases.example.json`

`benchmark.py` still runs the local cutter/render benchmark flow, but the next corpus should be built from fresh media and evaluated against the new provider architecture described in `docs/EXTERNAL_MODELS_PLAN.md`.

## What it measures

- content classification accuracy and confidence
- strategy routing differences between `auto`, manual override and `generic`
- top-clip overlap and diversity
- transcript / diarization diagnostics
  includes `raw_cluster_count`, `final_speaker_count`, `single_speaker_likelihood`,
  `multi_speaker_evidence`, `clusters_merged`, `tiny_clusters_removed` and `decision_reason`
- subtitle checker summary
- rendering / face-tracking / zoom stats
- human review placeholders for final clip judgment

## Default config

The default config lives in [cases.json](./cases.json).

Run the benchmark from the repo root:

```powershell
.\.venv\Scripts\python.exe benchmark.py
```

Useful options:

```powershell
.\.venv\Scripts\python.exe benchmark.py --case emeritos_gameplay
.\.venv\Scripts\python.exe benchmark.py --skip-render
.\.venv\Scripts\python.exe benchmark.py --force-transcribe
```

If `faster-whisper` auto-detects CUDA but the machine does not have the required runtime, the benchmark now retries local transcription on CPU/int8 automatically.

Outputs:

- `benchmarks/report.md` - human-readable latest report
- `benchmarks/results.json` - machine-readable latest report
- `benchmarks/human_review_template.csv` - manual review sheet
- `benchmarks/runs/<timestamp>/...` - detailed per-run artifacts

## Adding a new benchmark material

Recommended layout:

```text
benchmarks/assets/<case_id>/
  input/
    source.mp4
    source.mp3            # optional if source.mp4 already has audio
  metadata/
    source.info.json      # optional, lets the benchmark reuse YouTube metadata
    heatmap.json          # optional explicit heatmap
  transcripts/
    final_transcript.json # optional existing local transcript cache
```

Then copy `benchmarks/cases.example.json` into `benchmarks/cases.json` and add your cases:

```json
{
  "id": "my_podcast_case",
  "label": "My Podcast Case",
  "expected_content_type": "podcast",
  "source_url": "https://www.youtube.com/watch?v=...",
  "description": "Short description of the material.",
  "expected_speaker_mode": "multi_speaker",
  "video": "benchmarks/assets/my_podcast_case/input/source.mp4",
  "audio": "benchmarks/assets/my_podcast_case/input/source.mp3",
  "info_json": "benchmarks/assets/my_podcast_case/metadata/source.info.json",
  "heatmap": "benchmarks/assets/my_podcast_case/metadata/heatmap.json",
  "transcript_source": "benchmarks/assets/my_podcast_case/transcripts/final_transcript.json",
  "comparison_content_types": ["tutorial"],
  "include_generic_baseline": true,
  "notes": "Short description of the material."
}
```

### Adding a YouTube case

The current project downloader logic is still `yt-dlp`-based. For benchmark assets, use the same style of download but keep the files inside `benchmarks/assets/<case_id>/input/`, then normalize the merged MP4 and extracted MP3 to `source.mp4` and `source.mp3`.

Example:

```powershell
& .\.tools\yt-dlp-standalone.exe `
  --no-check-certificates `
  --ffmpeg-location .\.tools\ffmpeg-8.1.1-essentials_build\bin `
  --format "bestvideo[height<=1080]+bestaudio/best" `
  --output "benchmarks/assets/<case_id>/input/%(title)s.%(ext)s" `
  --merge-output-format mp4 `
  --write-info-json `
  --keep-video `
  --continue `
  --no-playlist `
  -- "https://www.youtube.com/watch?v=..."
```

After download:

1. rename the merged MP4 to `input/source.mp4`
2. extract or rename audio to `input/source.mp3`
3. copy the `.info.json` into `metadata/source.info.json`
4. create `metadata/heatmap.json`
   if YouTube does not expose a heatmap, a placeholder is acceptable and the benchmark report will flag that limitation
5. optionally pre-generate `transcripts/final_transcript.json`; otherwise `benchmark.py` will generate and cache it on the first run

Important notes:

- `expected_content_type` must be one of: `podcast`, `gameplay`, `tutorial`, `commentary`, `generic`
- `expected_speaker_mode` should be `single`, `multi`, `single_speaker`, `multi_speaker` or `unknown`
- if `transcript_source` is omitted, the benchmark will generate a fresh local transcript through `transcribe.py`
- if neither `heatmap` nor `info_json` is available, the benchmark will generate a placeholder heatmap and flag that limitation in the report

## Human review

After the run, open `benchmarks/human_review_template.csv` and fill:

- `human_relevance_score` from 1 to 5
- `human_boundary_score` from 1 to 5
- `human_crop_score` from 1 to 5
- `notes`

This is the intended place for final subjective judgment, because raw heuristics cannot fully score clip usefulness.

## Clean-slate workflow

1. Put fresh benchmark media into `benchmarks/assets/<case_id>/input/source.mp4`
2. Add the case to `benchmarks/cases.json`
3. Run:

```powershell
.\.venv\Scripts\python.exe benchmark.py --ai-mode local_only --subtitle-checker-mode local_only
```

4. Export the review dashboard:

```powershell
.\.venv\Scripts\python.exe review_dashboard.py export-html --results benchmarks\results.json --output benchmarks\review_dashboard.html
start benchmarks\review_dashboard.html
```
