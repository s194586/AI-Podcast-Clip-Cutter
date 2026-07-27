# Sprint 1 evidence

Date: 2026-07-27

This record is sanitized: it contains a public video ID only, no source URL,
media filename or path, transcript content, private URL, or tokenized URL.

## Scenario A — real data

- Public video ID: `UvaZ4uQOJvE`
- Duration: 4659 seconds
- Extractor version: `2026.03.17`
- Trusted YouTube Most Replayed points: 100
- Pipeline exit: `0`
- Peak detector: `time_weighted_local_prominence`, `algorithm_version: 2`
- Detected peaks: 16
- Generated candidates: 12
- Production peak stage: success
- Production candidate stage: success
- Stable candidate IDs: true
- Adapter IDs match canonical candidate IDs: true
- Semantic scoring fields: absent
- Final read-only artifact inspector: `PASS`, exit `0`

The version-1 detector was not accepted as final evidence. It generated zero
peaks because its 30-second prominence window had no samples on either side of
sparse points (median midpoint gap: 46.6 seconds), although 31 raw strict local
maxima existed. Algorithm version 2 keeps the configured prominence window and,
only for an empty side, independently uses the nearest non-plateau point on
that side. No fixed-duration workaround, derived/effective window field, or new
configuration/artifact field was introduced.

## Scenario B — automated contract evidence

Passed exactly:

- `tests.test_heatmap_contract.HeatmapContractTests.test_download_stage_propagates_heatmap_unavailable`
- `tests.test_heatmap_contract.HeatmapContractTests.test_download_stage_invalidates_heatmap_when_metadata_has_no_heatmap`

Result: `Ran 2 tests`, `OK`, exit code `0`. The contract evidence establishes
explicit missing-heatmap propagation, stale-heatmap invalidation, and no
synthetic or transcript-only semantic fallback.

## Scenario B — real-workspace manual evidence

Completed: `PASS`, `SCENARIO_B_MANUAL_EXIT=0`.

A separate temporary workspace was prepared from the completed Scenario A
workspace. Before the controlled check, `heatmap.json`, `heatmap_peaks.json`,
`candidate_windows.json`, and `top_windows.json` were removed. The controlled
`DetectHeatmapPeaksStage` run stopped with `HeatmapUnavailableError` and
reported error code `heatmap_unavailable`.

- `stage_stopped`: `PASS`
- `heatmap.json present`: `false`
- `heatmap_peaks.json present`: `false`
- `candidate_windows.json present`: `false`
- `top_windows.json present`: `false`
- `no_synthetic_or_downstream_artifacts`: `PASS`

This records that no synthetic heatmap or downstream peak/candidate/adapter
artifact was created. It removes the previous manual-Scenario-B blocker.

## Full suite

Command:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Result: `Ran 404 tests in 14.036s`, `OK`, exit code `0`.
