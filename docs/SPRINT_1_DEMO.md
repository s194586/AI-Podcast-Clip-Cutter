# Sprint 1 manual-demo record

This document records the verified evidence as of 2026-07-27. It contains no
media filenames or paths, transcript content, private URLs, or tokenized URLs.

## Scenario A — real YouTube Most Replayed

Completed evidence:

- [x] Trusted YouTube Most Replayed provenance and 100 trusted points.
- [x] Production peak stage succeeded; `algorithm_version` was `2` and produced 16 peaks.
- [x] Production candidate stage succeeded and produced 12 neutral canonical candidates.
- [x] Candidate IDs were stable; adapter IDs exactly matched canonical candidate IDs.
- [x] `top_windows.json` matched the canonical candidate IDs.
- [x] Semantic scoring fields were absent.
- [x] Read-only artifact inspector returned `PASS` with exit code `0`.

The verified source was public video ID `UvaZ4uQOJvE` (duration 4659 seconds),
using extractor version `2026.03.17`. The analysis-only pipeline exited `0`.

To repeat this privacy-safe procedure with a deliberately selected public source:

```powershell
$demoWorkspace = Join-Path $env:TEMP "sprint1-real-demo"
New-Item -ItemType Directory -Force $demoWorkspace | Out-Null
.\.venv\Scripts\python.exe manager.py --url "<public YouTube URL with Most Replayed>" --workspace-dir $demoWorkspace --analysis-only --ai-mode local_only --subtitle-checker-mode local_only
.\.venv\Scripts\python.exe scripts\inspect_sprint1_artifacts.py --workspace $demoWorkspace --require-complete
```

For the final run, inspect only the artifact metadata: trusted provenance,
point/peak/candidate counts, `algorithm_version`, canonical IDs, and adapter-ID
equality. Do not copy media paths, media filenames, transcript content, or a
non-public URL into the evidence.

### Sparse-heatmap correction

The originally tested version-1 detector returned zero peaks because the median
midpoint gap was 46.6 seconds while `prominence_window_seconds` was 30 seconds;
there were nevertheless 31 raw strict local maxima. Version 2 keeps the
configured window. If one side has no in-window baseline samples, it uses the
nearest non-plateau point on that side only, independently for each side. This
is not a fixed-duration fallback.

## Scenario B — missing heatmap

Completed controlled manual evidence:

- [x] A separate temporary workspace was prepared from the completed Scenario A workspace.
- [x] `heatmap.json`, `heatmap_peaks.json`, `candidate_windows.json`, and `top_windows.json` were removed before the check.
- [x] `DetectHeatmapPeaksStage` stopped with `HeatmapUnavailableError` and error code `heatmap_unavailable`.
- [x] `stage_stopped`: `PASS`.
- [x] All four artifacts were absent after the check.
- [x] `no_synthetic_or_downstream_artifacts`: `PASS`.
- [x] `SCENARIO_B_MANUAL_EXIT=0`.

No synthetic heatmap or downstream peak/candidate/adapter artifact was created.
This controlled procedure used a copy of the completed Scenario A workspace; it
does not claim use of an unrelated public video without Most Replayed.

Automated contract evidence is also complete:

- [x] `tests.test_heatmap_contract.HeatmapContractTests.test_download_stage_propagates_heatmap_unavailable`
- [x] `tests.test_heatmap_contract.HeatmapContractTests.test_download_stage_invalidates_heatmap_when_metadata_has_no_heatmap`

These two tests ran successfully (`Ran 2 tests`, `OK`, exit code `0`). They
verify explicit `heatmap_unavailable` propagation and removal of stale heatmap
data when fresh metadata lacks Most Replayed; no synthetic or transcript-only
semantic fallback is created.
