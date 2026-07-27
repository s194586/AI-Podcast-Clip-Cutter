# Sprint 1 review

SPRINT: 1
STATUS: done

## Scope

Sprint 1 delivers trusted YouTube Most Replayed provenance, explicit missing
heatmap handling, deterministic replay-interest peak detection, neutral
candidate generation, stable candidate IDs, a canonical candidate artifact,
a compatibility adapter, and a read-only artifact inspector. Local semantic or
viral scoring does not select candidates.

## Real-data Scenario A

On 2026-07-27, a public source identified only as video ID `UvaZ4uQOJvE` was
processed successfully. Its duration was 4659 seconds; extractor version was
`2026.03.17`; the trusted Most Replayed heatmap had 100 points. The pipeline
exited `0`, the peak and candidate stages succeeded, and the final inspector
returned `PASS` with exit `0`.

The final version-2 detector produced 16 peaks and the neutral generator
produced 12 candidates. Candidate IDs were stable, `top_windows` IDs matched
canonical candidate IDs, and semantic scoring fields were absent.

During the manual run, version 1 exposed a production bug: its 30-second
prominence window had no baseline samples around sparse points with a median
midpoint gap of 46.6 seconds. It therefore emitted zero peaks despite 31 raw
strict local maxima. Version 2 retains the configured window and independently
uses the nearest non-plateau point only for an otherwise empty side. It adds no
hardcoded duration threshold or new artifact/configuration fields.

## Scenario B — missing heatmap

Automated contract evidence passed:

- `tests.test_heatmap_contract.HeatmapContractTests.test_download_stage_propagates_heatmap_unavailable`
- `tests.test_heatmap_contract.HeatmapContractTests.test_download_stage_invalidates_heatmap_when_metadata_has_no_heatmap`

Result: `Ran 2 tests`, `OK`, exit code `0`. This proves the automated contract:
missing Most Replayed propagates explicitly, invalidates stale heatmap data, and
does not create a synthetic or transcript-only fallback.

Controlled manual Scenario B evidence also passed: a separate temporary
workspace was prepared from the completed Scenario A workspace, then
`heatmap.json`, `heatmap_peaks.json`, `candidate_windows.json`, and
`top_windows.json` were removed before the check. `DetectHeatmapPeaksStage`
stopped with `HeatmapUnavailableError`, reported `heatmap_unavailable`, and
returned `SCENARIO_B_MANUAL_EXIT=0`. `stage_stopped` and
`no_synthetic_or_downstream_artifacts` both passed; all four artifacts remained
absent. No synthetic or downstream artifact was created.

## Definition of Done

Complete:

- [x] Scenario A real-data evidence: 100 trusted Most Replayed points,
  algorithm version 2, 16 peaks, 12 candidates, stable IDs, and inspector PASS.
- [x] Automated Scenario B contract evidence: 2 tests OK.
- [x] Controlled manual Scenario B evidence: explicit `heatmap_unavailable`;
  no synthetic or downstream artifacts.
- [x] Full suite: 404 tests OK.
- [x] `git diff --check` passed.

## Blockers

None.

## Verification

The finalization full-suite command passed: `Ran 404 tests in 14.036s`, `OK`,
exit code `0`. The exact command and result are recorded in
`docs/SPRINT_1_EVIDENCE.md`. With all Definition of Done items complete,
Sprint 2 may now begin.
