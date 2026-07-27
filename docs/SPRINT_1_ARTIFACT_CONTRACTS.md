# Sprint 1 artifact contracts

All paths are workspace-relative. Times are finite seconds from the start of
the source video. Replay-interest values are in `0..1` and are not semantic
quality scores.

```text
trusted YouTube Most Replayed
-> metadata/heatmap.json
-> DetectHeatmapPeaksStage
-> metadata/heatmap_peaks.json
-> GenerateCandidatesStage
-> metadata/candidate_windows.json (canonical)
-> top_windows.json (compatibility adapter)
```

Only trusted YouTube Most Replayed may produce `metadata/heatmap.json` in
production. Missing or malformed data raises `HeatmapUnavailableError` and the
pipeline reports `heatmap_unavailable`; there is no synthetic or transcript-only
fallback. Producers use atomic writes. The Sprint 1 inspector is read-only.

## `metadata/source_media.json`

`schema_version: 1`; source is `"youtube"`. It binds the non-empty video ID,
source URL, and workspace-relative media path. It is provenance metadata, not a
candidate-selection input.

## `metadata/heatmap.json`

`schema_version: 1`; source is `"youtube_most_replayed"`; `synthetic: false`.
It requires a non-empty video ID, `extractor: "youtube"`, non-empty extractor
version, positive duration, and ordered non-empty points. Each point has
`start_time`, `end_time`, and `value`; intervals are valid and in duration, and
values are in `0..1`.

## `metadata/heatmap_peaks.json`

`schema_version: 1`; source is `"youtube_most_replayed"`; algorithm is
`"time_weighted_local_prominence"`; current `algorithm_version: 2`.

The producer projects exactly these detector parameters:

```text
smoothing_radius_seconds
prominence_window_seconds
min_prominence
min_distance_seconds
max_peaks
```

No derived/effective window, configuration, or artifact fields are added.
Smoothing remains time-weighted; plateau handling, minimum-prominence
filtering, temporal NMS, deterministic ranking, and `max_peaks` remain in
effect.

Local prominence collects baseline samples within the configured
`prominence_window_seconds`. If a peak side has no in-window samples, the
detector independently uses the nearest non-plateau point on that side. Thus an
internal local maximum is not discarded solely because a sparse source has a
sampling gap wider than the configured window. This fallback does not alter the
configured window or hardcode a duration threshold.

Required peak-document fields are `schema_version`, `source`, `algorithm`,
`algorithm_version`, `video_id`, `duration_seconds`, `parameters`, and `peaks`.
Each peak has unique positive `rank`, `peak_time`, `start_time`, `end_time`,
`raw_value`, `smoothed_value`, and `prominence`. An empty `peaks` list is valid.

## `metadata/candidate_windows.json` — canonical

`schema_version: 2`; source is `"youtube_most_replayed"`; generator is
`"peak_centered_candidate_windows"`; `generator_version: 1`.

It is the single canonical Sprint 1 candidate artifact. It contains stable,
unique `cand_v1_` IDs generated from the versioned video/generator/window
identity, technically constrained windows, and only source replay-interest
data. It has no local semantic, hook, emotion, payoff, viral, confidence, or
other semantic scoring fields.

Its exact parameter projection is:

```text
min_duration_seconds
target_duration_seconds
max_duration_seconds
max_overlap_ratio
max_candidates
```

## `top_windows.json` — compatibility adapter

`schema_version: 2`; source is
`"candidate_windows_compatibility_adapter"`; canonical artifact is
`"metadata/candidate_windows.json"`. It is an adapter, never a second source
of truth. Each adapter ID equals its canonical candidate ID, and the adapter
is an exact projection of canonical candidate fields. It permits no additional
semantic, legacy, or arbitrary root/item fields, and projects the same
candidate-ID set without semantic local scoring.
