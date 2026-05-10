# AI-Virtual-Cutter Benchmark Report

- Generated at: `2026-05-10T18:27:30.564840+00:00`
- Run id: `20260510_171633`
- AI mode: `local_only`
- Subtitle checker mode: `local_only`
- Legacy media assets in `input/`: `4`
- Benchmark corpus assets in `benchmarks/assets/`: `6`
- Auxiliary smoke assets: `2`

## Scope

- Legacy input asset: `input\EMERITOS BANDITOS BEZ MVP MAJORA W ESEA INTERMEDIATE!.f251.mp3`
- Legacy input asset: `input\EMERITOS BANDITOS BEZ MVP MAJORA W ESEA INTERMEDIATE!.f251.webm`
- Legacy input asset: `input\EMERITOS BANDITOS BEZ MVP MAJORA W ESEA INTERMEDIATE!.f399.mp4`
- Legacy input asset: `input\EMERITOS BANDITOS BEZ MVP MAJORA W ESEA INTERMEDIATE!.mp4`
- Benchmark corpus asset: `benchmarks\assets\putin_parade_commentary\input\source.mp3`
- Benchmark corpus asset: `benchmarks\assets\putin_parade_commentary\input\source.mp4`
- Benchmark corpus asset: `benchmarks\assets\roman_giertych_commentary\input\source.mp3`
- Benchmark corpus asset: `benchmarks\assets\roman_giertych_commentary\input\source.mp4`
- Benchmark corpus asset: `benchmarks\assets\ukraine_war_report\input\source.mp3`
- Benchmark corpus asset: `benchmarks\assets\ukraine_war_report\input\source.mp4`
- Auxiliary smoke asset (not used to claim universality): `tmp\smoke_120s.mp3`
- Auxiliary smoke asset (not used to claim universality): `tmp\smoke_5s.mp3`
- Configured benchmark cases: `4`
- Distinct expected content types tested: `gameplay, generic`
- This iteration expands the real benchmark corpus from `1` to `4` configured materials.
- The new additions broaden coverage for `generic` / commentary-like material, but they do not replace missing true `podcast` and `tutorial` benchmarks.
- Coverage gap: the corpus still does not include a true `podcast` and `tutorial` benchmark case, so universality is still not empirically proven.

## Classifier Results

| Material | Expected | Auto detected | Confidence | Correct | Reasons |
| --- | --- | --- | ---: | --- | --- |
| EMERITOS BANDITOS gameplay | `gameplay` | `gameplay` | 0.97 | yes | Visual motion is high across sampled frames.; Scene changes are frequent.; Speech contains many emotionally elevated segments. |
| Ukraine war report commentary | `generic` | `podcast` | 0.76 | no | High speech coverage across the material.; Multiple recurring speakers are present.; No single speaker dominates the full transcript. |
| Roman Giertych commentary essay | `generic` | `generic` | 0.53 | yes | Keyword evidence is weak, so a generic fallback stays safer. |
| Putin parade commentary | `generic` | `podcast` | 0.83 | no | High speech coverage across the material.; Utterances are relatively long and conversational.; The transcript contains many longer turns. |

## EMERITOS BANDITOS gameplay

- Expected content type: `gameplay`
- Expected speaker mode: `multi`
- Status: `completed`
- Description: Repository gameplay benchmark with facecam, team voice chat and fast game-state changes.
- Notes: Current gameplay material available in the repository. It is the only full long-form benchmark case at the moment.
- Transcript preparation: `reused_local_transcript`
- Heatmap source: `existing_heatmap`

### Transcript / Diarization

- Segments: `701`
- Speakers: `4`
- Speaker switches: `467`
- Dominant speaker ratio: `0.2825`
- Diarization status: `applied`
- Fallback used: `False`

### Subtitle Checker

- Mode: `local_only`
- Status: `warning`
- Score: `70.0`
- Issues: `0` errors, `38` warnings
- Top issue codes: `TOO_MANY_WORDS_FOR_DURATION` x35, `REPEATED_WORDS` x2, `DUPLICATED_ADJACENT_TEXT` x1

### Strategy Scenarios

| Scenario | Arg | Detected | Confidence | Override ok | Render success | Top-5 overlap note |
| --- | --- | --- | ---: | --- | --- | --- |
| auto | `auto` | `gameplay` | 0.97 | False | True | - |
| manual_gameplay | `gameplay` | `gameplay` | 1.00 | True | True | 5/5 overlap vs auto |
| compare_podcast | `podcast` | `podcast` | 1.00 | True | True | 3/5 overlap vs auto |
| compare_generic | `generic` | `generic` | 1.00 | True | True | 4/5 overlap vs auto |

### Pairwise Overlap

- `auto` vs `manual_gameplay`: `5/5` overlapping clips (`1.00`)
- `auto` vs `compare_podcast`: `3/5` overlapping clips (`0.60`)
- `auto` vs `compare_generic`: `4/5` overlapping clips (`0.80`)
- `manual_gameplay` vs `compare_podcast`: `3/5` overlapping clips (`0.60`)
- `manual_gameplay` vs `compare_generic`: `4/5` overlapping clips (`0.80`)
- `compare_podcast` vs `compare_generic`: `3/5` overlapping clips (`0.60`)

### Top Clips

#### auto

- `17:47.41 - 18:35.79` | score `94.23` | reasons: strong heatmap support, contains punchy or emotional language, has several short punchy lines
- `05:25.33 - 05:58.35` | score `92.97` | reasons: strong heatmap support, contains punchy or emotional language, has several short punchy lines
- `16:38.20 - 17:19.73` | score `92.73` | reasons: strong heatmap support, contains punchy or emotional language, contains high-importance transcript moments
- `02:17.46 - 02:52.19` | score `92.62` | reasons: strong heatmap support, contains punchy or emotional language, has several short punchy lines
- `04:14.05 - 04:59.95` | score `92.07` | reasons: strong heatmap support, contains punchy or emotional language, has several short punchy lines

#### manual_gameplay

- `17:47.41 - 18:35.79` | score `94.23` | reasons: strong heatmap support, contains punchy or emotional language, has several short punchy lines
- `05:25.33 - 05:58.35` | score `92.97` | reasons: strong heatmap support, contains punchy or emotional language, has several short punchy lines
- `16:38.20 - 17:19.73` | score `92.73` | reasons: strong heatmap support, contains punchy or emotional language, contains high-importance transcript moments
- `02:17.46 - 02:52.19` | score `92.62` | reasons: strong heatmap support, contains punchy or emotional language, has several short punchy lines
- `04:14.05 - 04:59.95` | score `92.07` | reasons: strong heatmap support, contains punchy or emotional language, has several short punchy lines

#### compare_podcast

- `17:47.41 - 18:35.79` | score `89.18` | reasons: strong heatmap support, good speech density for a short clip, has speaker dynamics or conversational turns
- `16:38.20 - 17:28.11` | score `88.26` | reasons: strong heatmap support, has speaker dynamics or conversational turns, good speech density for a short clip
- `04:58.65 - 05:58.35` | score `87.52` | reasons: strong heatmap support, good speech density for a short clip, has speaker dynamics or conversational turns
- `00:06.56 - 00:45.74` | score `85.53` | reasons: strong heatmap support, good speech density for a short clip, has speaker dynamics or conversational turns
- `07:54.74 - 08:25.84` | score `85.53` | reasons: strong heatmap support, good speech density for a short clip, has speaker dynamics or conversational turns

#### compare_generic

- `17:47.41 - 18:35.79` | score `89.45` | reasons: strong heatmap support, contains punchy or emotional language, good speech density for a short clip
- `16:38.20 - 17:19.73` | score `88.83` | reasons: strong heatmap support, contains punchy or emotional language, contains high-importance transcript moments
- `05:37.91 - 06:14.94` | score `88.78` | reasons: strong heatmap support, contains punchy or emotional language, good speech density for a short clip
- `04:14.05 - 04:59.95` | score `87.6` | reasons: strong heatmap support, contains punchy or emotional language, contains high-importance transcript moments
- `00:06.56 - 00:40.74` | score `86.68` | reasons: strong heatmap support, contains punchy or emotional language, good speech density for a short clip

### Rendering

- `auto`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`5`
- `manual_gameplay`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`5`
- `compare_podcast`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`7`
- `compare_generic`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`4`

### Findings

- Auto classification matched the expected type (gameplay) with confidence 0.97.
- Subtitle checker reported warnings (38), but no hard failure.
- Face-aware rendering completed, but actual face detections were sparse (93/2442 sampled checks), so this benchmark does not strongly validate facecam tracking quality.
- All rendered benchmark scenarios produced the requested subtitled clips.

## Ukraine war report commentary

- Expected content type: `generic`
- Expected speaker mode: `single`
- Status: `completed`
- Source URL: https://www.youtube.com/watch?v=5hC0yPPFOYA
- Description: Single-host war report with map-based commentary and headline-driven analysis. Treated as generic/commentary-like for the current classifier taxonomy.
- Notes: Added as a generic/commentary benchmark. It is speech-heavy, but it is not a true podcast or tutorial.
- Transcript preparation: `generated_local_transcript_cpu_fallback_cached`
- Heatmap source: `existing_heatmap`

### Transcript / Diarization

- Segments: `1053`
- Speakers: `4`
- Speaker switches: `684`
- Dominant speaker ratio: `0.3514`
- Diarization status: `applied`
- Fallback used: `False`
- Diagnostic flags: `expected_single_speaker_but_detected_many`

### Subtitle Checker

- Mode: `local_only`
- Status: `warning`
- Score: `70.0`
- Issues: `0` errors, `10` warnings
- Top issue codes: `TOO_MANY_WORDS_FOR_DURATION` x8, `MODEL_ARTIFACT` x1, `EARLY_TRANSCRIPT_END` x1

### Strategy Scenarios

| Scenario | Arg | Detected | Confidence | Override ok | Render success | Top-5 overlap note |
| --- | --- | --- | ---: | --- | --- | --- |
| auto | `auto` | `podcast` | 0.76 | False | True | - |
| manual_generic | `generic` | `generic` | 1.00 | True | True | 4/5 overlap vs auto |
| compare_podcast | `podcast` | `podcast` | 1.00 | True | True | 5/5 overlap vs auto |

### Pairwise Overlap

- `auto` vs `manual_generic`: `4/5` overlapping clips (`0.80`)
- `auto` vs `compare_podcast`: `5/5` overlapping clips (`1.00`)
- `manual_generic` vs `compare_podcast`: `4/5` overlapping clips (`0.80`)

### Top Clips

#### auto

- `17:40.72 - 18:16.46` | score `84.96` | reasons: good speech density for a short clip, has speaker dynamics or conversational turns, starts with a stronger hook signal
- `11:56.88 - 12:27.68` | score `82.58` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, starts with a stronger hook signal
- `22:37.76 - 23:08.12` | score `80.8` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, starts with a stronger hook signal
- `10:12.83 - 10:48.78` | score `80.66` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, ends with a clearer payoff signal
- `13:06.56 - 13:44.74` | score `80.18` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, starts with a stronger hook signal

#### manual_generic

- `17:40.72 - 18:16.46` | score `82.33` | reasons: contains punchy or emotional language, good speech density for a short clip, starts with a stronger hook signal
- `11:56.88 - 12:27.68` | score `79.86` | reasons: contains punchy or emotional language, contains high-importance transcript moments, good speech density for a short clip
- `20:43.05 - 21:13.50` | score `78.0` | reasons: contains punchy or emotional language, starts with a stronger hook signal, contains high-importance transcript moments
- `10:12.83 - 10:48.78` | score `77.46` | reasons: contains punchy or emotional language, good speech density for a short clip, ends with a clearer payoff signal
- `22:39.10 - 23:23.72` | score `76.91` | reasons: contains punchy or emotional language, good speech density for a short clip, contains high-importance transcript moments

#### compare_podcast

- `17:40.72 - 18:16.46` | score `84.96` | reasons: good speech density for a short clip, has speaker dynamics or conversational turns, starts with a stronger hook signal
- `11:56.88 - 12:27.68` | score `82.58` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, starts with a stronger hook signal
- `22:37.76 - 23:08.12` | score `80.8` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, starts with a stronger hook signal
- `10:12.83 - 10:48.78` | score `80.66` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, ends with a clearer payoff signal
- `13:06.56 - 13:44.74` | score `80.18` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, starts with a stronger hook signal

### Rendering

- `auto`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`82`
- `manual_generic`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`69`
- `compare_podcast`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`82`

### Findings

- Auto classification missed the expected type: podcast vs generic.
- Subtitle checker reported warnings (10), but no hard failure.
- Transcript/diarization diagnostics raised flags: expected_single_speaker_but_detected_many
- All rendered benchmark scenarios produced the requested subtitled clips.

## Roman Giertych commentary essay

- Expected content type: `generic`
- Expected speaker mode: `single`
- Status: `completed`
- Source URL: https://www.youtube.com/watch?v=FheyKl2x73A
- Description: Single-narrator political commentary / explainer essay with archival visuals. Treated as generic/commentary-like for the current taxonomy.
- Notes: Added as a generic/commentary benchmark. It resembles a narrated commentary video more than a dialogue-driven podcast.
- Transcript preparation: `generated_local_transcript_cpu_fallback_cached`
- Heatmap source: `existing_heatmap`

### Transcript / Diarization

- Segments: `439`
- Speakers: `4`
- Speaker switches: `288`
- Dominant speaker ratio: `0.2825`
- Diarization status: `applied`
- Fallback used: `False`
- Diagnostic flags: `expected_single_speaker_but_detected_many`

### Subtitle Checker

- Mode: `local_only`
- Status: `warning`
- Score: `100.0`
- Issues: `0` errors, `0` warnings

### Strategy Scenarios

| Scenario | Arg | Detected | Confidence | Override ok | Render success | Top-5 overlap note |
| --- | --- | --- | ---: | --- | --- | --- |
| auto | `auto` | `generic` | 0.53 | False | True | - |
| manual_generic | `generic` | `generic` | 1.00 | True | True | 5/5 overlap vs auto |
| compare_podcast | `podcast` | `podcast` | 1.00 | True | True | 3/5 overlap vs auto |

### Pairwise Overlap

- `auto` vs `manual_generic`: `5/5` overlapping clips (`1.00`)
- `auto` vs `compare_podcast`: `3/5` overlapping clips (`0.60`)
- `manual_generic` vs `compare_podcast`: `3/5` overlapping clips (`0.60`)

### Top Clips

#### auto

- `19:54.36 - 20:31.18` | score `78.74` | reasons: contains punchy or emotional language, good speech density for a short clip, starts with a stronger hook signal
- `19:09.66 - 19:41.82` | score `78.04` | reasons: contains punchy or emotional language, good speech density for a short clip, starts with a stronger hook signal
- `00:53.90 - 01:26.86` | score `72.99` | reasons: contains punchy or emotional language, starts with a stronger hook signal, good speech density for a short clip
- `11:14.48 - 11:53.92` | score `72.06` | reasons: contains punchy or emotional language, good speech density for a short clip, contains high-importance transcript moments
- `12:20.16 - 12:56.24` | score `71.5` | reasons: contains punchy or emotional language, good speech density for a short clip, contains high-importance transcript moments

#### manual_generic

- `19:54.36 - 20:31.18` | score `78.74` | reasons: contains punchy or emotional language, good speech density for a short clip, starts with a stronger hook signal
- `19:09.66 - 19:41.82` | score `78.04` | reasons: contains punchy or emotional language, good speech density for a short clip, starts with a stronger hook signal
- `00:53.90 - 01:26.86` | score `72.99` | reasons: contains punchy or emotional language, starts with a stronger hook signal, good speech density for a short clip
- `11:14.48 - 11:53.92` | score `72.06` | reasons: contains punchy or emotional language, good speech density for a short clip, contains high-importance transcript moments
- `12:20.16 - 12:56.24` | score `71.5` | reasons: contains punchy or emotional language, good speech density for a short clip, contains high-importance transcript moments

#### compare_podcast

- `19:09.66 - 20:01.48` | score `82.71` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, starts with a stronger hook signal
- `00:53.90 - 01:26.86` | score `77.1` | reasons: has speaker dynamics or conversational turns, starts with a stronger hook signal, good speech density for a short clip
- `11:14.48 - 11:53.92` | score `74.25` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, stays relatively clear despite overlap risk
- `04:13.30 - 04:54.70` | score `74.23` | reasons: good speech density for a short clip, has speaker dynamics or conversational turns, stays relatively clear despite overlap risk
- `13:39.04 - 14:30.56` | score `72.62` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, stays relatively clear despite overlap risk

### Rendering

- `auto`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`0`
- `manual_generic`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`0`
- `compare_podcast`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`0`

### Findings

- Auto classification matched the expected type (generic) with confidence 0.527.
- Subtitle checker reported warnings (0), but no hard failure.
- Transcript/diarization diagnostics raised flags: expected_single_speaker_but_detected_many
- All rendered benchmark scenarios produced the requested subtitled clips.

## Putin parade commentary

- Expected content type: `generic`
- Expected speaker mode: `single`
- Status: `completed`
- Source URL: https://www.youtube.com/watch?v=7t9yv4d318U
- Description: Single-host current-events commentary about Russia's parade and Ukraine. Treated as generic/commentary-like for the current taxonomy.
- Notes: Added as a generic/commentary benchmark. Useful for testing news-like monologue material without introducing a new taxonomy class yet.
- Transcript preparation: `generated_local_transcript_cpu_fallback_cached`
- Heatmap source: `existing_heatmap`

### Transcript / Diarization

- Segments: `229`
- Speakers: `4`
- Speaker switches: `148`
- Dominant speaker ratio: `0.31`
- Diarization status: `applied`
- Fallback used: `False`
- Diagnostic flags: `expected_single_speaker_but_detected_many`

### Subtitle Checker

- Mode: `local_only`
- Status: `warning`
- Score: `100.0`
- Issues: `0` errors, `0` warnings

### Strategy Scenarios

| Scenario | Arg | Detected | Confidence | Override ok | Render success | Top-5 overlap note |
| --- | --- | --- | ---: | --- | --- | --- |
| auto | `auto` | `podcast` | 0.83 | False | True | - |
| manual_generic | `generic` | `generic` | 1.00 | True | True | 4/5 overlap vs auto |
| compare_podcast | `podcast` | `podcast` | 1.00 | True | True | 5/5 overlap vs auto |

### Pairwise Overlap

- `auto` vs `manual_generic`: `4/5` overlapping clips (`0.80`)
- `auto` vs `compare_podcast`: `5/5` overlapping clips (`1.00`)
- `manual_generic` vs `compare_podcast`: `4/5` overlapping clips (`0.80`)

### Top Clips

#### auto

- `06:57.53 - 07:47.13` | score `78.98` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, starts with a stronger hook signal
- `10:08.63 - 10:39.61` | score `74.61` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, stays relatively clear despite overlap risk
- `00:55.26 - 01:34.25` | score `74.36` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, stays relatively clear despite overlap risk
- `12:31.65 - 13:06.09` | score `73.46` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, stays relatively clear despite overlap risk
- `14:34.63 - 15:19.99` | score `70.39` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, stays relatively clear despite overlap risk

#### manual_generic

- `06:57.53 - 07:47.13` | score `74.92` | reasons: contains punchy or emotional language, good speech density for a short clip, starts with a stronger hook signal
- `00:55.26 - 01:34.25` | score `73.73` | reasons: contains punchy or emotional language, contains high-importance transcript moments, good speech density for a short clip
- `10:08.63 - 10:39.61` | score `73.31` | reasons: contains punchy or emotional language, good speech density for a short clip, contains high-importance transcript moments
- `12:31.65 - 13:06.09` | score `71.15` | reasons: contains punchy or emotional language, good speech density for a short clip, contains high-importance transcript moments
- `03:28.09 - 04:02.47` | score `68.88` | reasons: contains punchy or emotional language, good speech density for a short clip, contains high-importance transcript moments

#### compare_podcast

- `06:57.53 - 07:47.13` | score `78.98` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, starts with a stronger hook signal
- `10:08.63 - 10:39.61` | score `74.61` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, stays relatively clear despite overlap risk
- `00:55.26 - 01:34.25` | score `74.36` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, stays relatively clear despite overlap risk
- `12:31.65 - 13:06.09` | score `73.46` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, stays relatively clear despite overlap risk
- `14:34.63 - 15:19.99` | score `70.39` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, stays relatively clear despite overlap risk

### Rendering

- `auto`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`11`
- `manual_generic`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`11`
- `compare_podcast`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`11`

### Findings

- Auto classification missed the expected type: podcast vs generic.
- Subtitle checker reported warnings (0), but no hard failure.
- Transcript/diarization diagnostics raised flags: expected_single_speaker_but_detected_many
- All rendered benchmark scenarios produced the requested subtitled clips.

## Human Review

- Fill in `benchmarks\human_review_template.csv` with `human_relevance_score`, `human_boundary_score`, `human_crop_score` and notes for each rendered clip.

## Recommendation

- Next step: `expand_benchmark_corpus`
- Title: Collect missing podcast and tutorial benchmark materials before tuning algorithms
- Why: The benchmark still lacks representative coverage for podcast, tutorial. The current corpus is useful for gameplay and generic/commentary-like material, but it still cannot validate whether the cutter is truly universal across all target types.
