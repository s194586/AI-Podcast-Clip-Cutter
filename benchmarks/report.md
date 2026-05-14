# AI-Virtual-Cutter Benchmark Report

- Generated at: `2026-05-14T01:11:15.676505+00:00`
- Run id: `20260513_235701`
- AI mode: `local_only`
- Subtitle checker mode: `local_only`
- Legacy media assets in `input/`: `4`
- Benchmark corpus assets in `benchmarks/assets/`: `10`
- Auxiliary smoke assets: `2`

## Scope

- Legacy input asset: `input\EMERITOS BANDITOS BEZ MVP MAJORA W ESEA INTERMEDIATE!.f251.mp3`
- Legacy input asset: `input\EMERITOS BANDITOS BEZ MVP MAJORA W ESEA INTERMEDIATE!.f251.webm`
- Legacy input asset: `input\EMERITOS BANDITOS BEZ MVP MAJORA W ESEA INTERMEDIATE!.f399.mp4`
- Legacy input asset: `input\EMERITOS BANDITOS BEZ MVP MAJORA W ESEA INTERMEDIATE!.mp4`
- Benchmark corpus asset: `benchmarks\assets\canva_presentation_tutorial\input\source.mp3`
- Benchmark corpus asset: `benchmarks\assets\canva_presentation_tutorial\input\source.mp4`
- Benchmark corpus asset: `benchmarks\assets\magenta_team_podcast\input\source.mp3`
- Benchmark corpus asset: `benchmarks\assets\magenta_team_podcast\input\source.mp4`
- Benchmark corpus asset: `benchmarks\assets\putin_parade_commentary\input\source.mp3`
- Benchmark corpus asset: `benchmarks\assets\putin_parade_commentary\input\source.mp4`
- Benchmark corpus asset: `benchmarks\assets\roman_giertych_commentary\input\source.mp3`
- Benchmark corpus asset: `benchmarks\assets\roman_giertych_commentary\input\source.mp4`
- Benchmark corpus asset: `benchmarks\assets\ukraine_war_report\input\source.mp3`
- Benchmark corpus asset: `benchmarks\assets\ukraine_war_report\input\source.mp4`
- Auxiliary smoke asset (not used to claim universality): `tmp\smoke_120s.mp3`
- Auxiliary smoke asset (not used to claim universality): `tmp\smoke_5s.mp3`
- Configured benchmark cases: `6`
- Distinct expected content types tested: `commentary, gameplay, podcast, tutorial`
- This iteration expands the real benchmark corpus to `6` configured materials.

## Classifier Results

| Material | Expected | Auto detected | Confidence | Correct | Reasons |
| --- | --- | --- | ---: | --- | --- |
| EMERITOS BANDITOS gameplay | `gameplay` | `gameplay` | 0.97 | yes | Transcript contains gameplay-oriented vocabulary.; Visual motion and scene changes are highly dynamic.; Speech contains many emotionally elevated segments. |
| Ukraine war report commentary | `commentary` | `commentary` | 0.97 | yes | Speech coverage is high enough for narrator-led commentary.; Utterances are long enough to form explanatory monologue beats.; Transcript contains repeated public-affairs or commentary cues. |
| Roman Giertych commentary essay | `commentary` | `commentary` | 0.97 | yes | Speech coverage is high enough for narrator-led commentary.; Utterances are long enough to form explanatory monologue beats.; The transcript contains many longer explanatory turns. |
| Putin parade commentary | `commentary` | `commentary` | 0.97 | yes | Speech coverage is high enough for narrator-led commentary.; Utterances are long enough to form explanatory monologue beats.; The transcript contains many longer explanatory turns. |
| Magenta Team two-person podcast | `podcast` | `podcast` | 0.89 | yes | Speech coverage is high enough for a talk-led format.; Utterances are long enough to form complete spoken turns.; The transcript contains many explicit question turns. |
| Canva presentation tutorial | `tutorial` | `tutorial` | 0.97 | yes | Speech dominates the material, which fits guided instruction.; Utterances are long enough to carry explanations.; Transcript contains clear instructional vocabulary. |

## Key Observations

- Commentary-like cases routed to `podcast` in `0/3` cases.
- True commentary cases classified correctly as `commentary`: `3/3`.
- True podcast cases classified correctly as `podcast`: `1/1`.
- True tutorial cases classified correctly as `tutorial`: `1/1`.
- Expected single-speaker materials flagged as over-segmented by diarization: `0`.
- Expected multi-speaker materials flattened to a single speaker: `0`.

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
- Speaker switches: `444`
- Dominant speaker ratio: `0.3552`
- Diarization status: `applied`
- Fallback used: `False`
- Raw cluster count: `4`
- Final speaker count: `4`
- Single-speaker likelihood: `0.0`
- Multi-speaker evidence: `0.94`
- Clusters merged: `0`
- Tiny clusters removed: `0`
- Decision reason: `kept_multi_speaker_clusters`

### Subtitle Checker

- Mode: `local_only`
- Status: `warning`
- Score: `70.0`
- Issues: `0` errors, `38` warnings
- Top issue codes: `TOO_MANY_WORDS_FOR_DURATION` x35, `REPEATED_WORDS` x2, `DUPLICATED_ADJACENT_TEXT` x1

### Strategy Scenarios

| Scenario | Arg | Detected | Confidence | Layout | Render success | Top-5 overlap note |
| --- | --- | --- | ---: | --- | --- | --- |
| auto | `auto` | `gameplay` | 0.97 | `{'gameplay_priority_crop': 5}` | True | - |
| manual_gameplay | `gameplay` | `gameplay` | 1.00 | `{'gameplay_priority_crop': 5}` | True | 5/5 overlap vs auto |
| compare_podcast | `podcast` | `podcast` | 1.00 | `{'speaker_face_crop': 5}` | True | 3/5 overlap vs auto |
| compare_generic | `generic` | `generic` | 1.00 | `{'safe_center_crop': 5}` | True | 4/5 overlap vs auto |

### Pairwise Overlap

- `auto` vs `manual_gameplay`: `5/5` overlapping clips (`1.00`)
- `auto` vs `compare_podcast`: `3/5` overlapping clips (`0.60`)
- `auto` vs `compare_generic`: `4/5` overlapping clips (`0.80`)
- `manual_gameplay` vs `compare_podcast`: `3/5` overlapping clips (`0.60`)
- `manual_gameplay` vs `compare_generic`: `4/5` overlapping clips (`0.80`)
- `compare_podcast` vs `compare_generic`: `3/5` overlapping clips (`0.60`)

### Top Clips

#### auto

- `18:03.49 - 18:35.79` | score `95.95` | reasons: strong heatmap support, contains punchy or emotional language, has several short punchy lines
- `05:37.91 - 06:14.94` | score `94.29` | reasons: strong heatmap support, contains punchy or emotional language, has several short punchy lines
- `16:38.20 - 17:19.73` | score `94.11` | reasons: strong heatmap support, contains punchy or emotional language, contains high-importance transcript moments
- `04:14.05 - 04:59.95` | score `92.7` | reasons: strong heatmap support, contains punchy or emotional language, has several short punchy lines
- `15:02.36 - 15:33.76` | score `92.36` | reasons: strong heatmap support, contains punchy or emotional language, has several short punchy lines

#### manual_gameplay

- `18:03.49 - 18:35.79` | score `95.95` | reasons: strong heatmap support, contains punchy or emotional language, has several short punchy lines
- `05:37.91 - 06:14.94` | score `94.29` | reasons: strong heatmap support, contains punchy or emotional language, has several short punchy lines
- `16:38.20 - 17:19.73` | score `94.11` | reasons: strong heatmap support, contains punchy or emotional language, contains high-importance transcript moments
- `04:14.05 - 04:59.95` | score `92.7` | reasons: strong heatmap support, contains punchy or emotional language, has several short punchy lines
- `15:02.36 - 15:33.76` | score `92.36` | reasons: strong heatmap support, contains punchy or emotional language, has several short punchy lines

#### compare_podcast

- `17:47.41 - 18:35.79` | score `93.18` | reasons: strong heatmap support, good speech density for a short clip, has speaker dynamics or conversational turns
- `16:38.20 - 17:28.11` | score `92.26` | reasons: strong heatmap support, has speaker dynamics or conversational turns, good speech density for a short clip
- `05:56.49 - 06:45.58` | score `91.24` | reasons: strong heatmap support, has speaker dynamics or conversational turns, good speech density for a short clip
- `04:14.05 - 04:59.95` | score `90.02` | reasons: strong heatmap support, has speaker dynamics or conversational turns, good speech density for a short clip
- `07:54.74 - 08:25.84` | score `89.53` | reasons: strong heatmap support, good speech density for a short clip, has speaker dynamics or conversational turns

#### compare_generic

- `17:47.41 - 18:35.79` | score `91.95` | reasons: strong heatmap support, contains punchy or emotional language, good speech density for a short clip
- `16:38.20 - 17:19.73` | score `91.33` | reasons: strong heatmap support, contains punchy or emotional language, contains high-importance transcript moments
- `05:37.91 - 06:14.94` | score `90.53` | reasons: strong heatmap support, contains punchy or emotional language, good speech density for a short clip
- `04:14.05 - 04:59.95` | score `89.35` | reasons: strong heatmap support, contains punchy or emotional language, contains high-importance transcript moments
- `00:06.56 - 00:45.74` | score `88.42` | reasons: strong heatmap support, contains punchy or emotional language, good speech density for a short clip

### Rendering

- `auto`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`10`, layout_modes=`{'gameplay_priority_crop': 5}`, crop_modes=`{'gameplay_balanced': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`
- `manual_gameplay`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`10`, layout_modes=`{'gameplay_priority_crop': 5}`, crop_modes=`{'gameplay_balanced': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`
- `compare_podcast`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`42`, layout_modes=`{'speaker_face_crop': 5}`, crop_modes=`{'speaker_focus': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`
- `compare_generic`: render_success=`True`, face_tracking_success=`0`, center_fallback=`0`, zoom_samples=`0`, layout_modes=`{'safe_center_crop': 5}`, crop_modes=`{'balanced': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`

### Layout / 9:16 Rendering

- `auto`: layout=`{'gameplay_priority_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'gameplay': 5}`, tracking_modes=`{'dynamic_face_tracking': 5}`
- `manual_gameplay`: layout=`{'gameplay_priority_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'gameplay': 5}`, tracking_modes=`{'dynamic_face_tracking': 5}`
- `compare_podcast`: layout=`{'speaker_face_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'speaker_face': 5}`, tracking_modes=`{'dynamic_face_tracking': 5}`
- `compare_generic`: layout=`{'safe_center_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'center': 5}`, tracking_modes=`{'safe_center_crop': 5}`

### Findings

- Auto classification matched the expected type (gameplay) with confidence 0.97.
- Subtitle checker reported warnings (38), but no hard failure.
- Face-aware rendering completed, but actual face detections were sparse (45/2258 sampled checks), so this benchmark does not strongly validate facecam tracking quality.
- All rendered benchmark scenarios produced the requested subtitled clips.

## Ukraine war report commentary

- Expected content type: `commentary`
- Expected speaker mode: `single`
- Status: `completed`
- Source URL: https://www.youtube.com/watch?v=5hC0yPPFOYA
- Description: Single-host war report with map-based commentary and headline-driven analysis. Previously tracked as generic/commentary-like before the dedicated commentary class was introduced.
- Notes: Added as a commentary benchmark. It is speech-heavy, but it is not a true podcast or tutorial.
- Transcript preparation: `reused_local_transcript`
- Heatmap source: `existing_heatmap`

### Transcript / Diarization

- Segments: `1053`
- Speakers: `1`
- Speaker switches: `0`
- Dominant speaker ratio: `1.0`
- Diarization status: `applied`
- Fallback used: `False`
- Raw cluster count: `4`
- Final speaker count: `1`
- Single-speaker likelihood: `0.7`
- Multi-speaker evidence: `0.46`
- Clusters merged: `1`
- Tiny clusters removed: `1`
- Decision reason: `collapsed_to_single_speaker_due_to_weak_multi_evidence`

### Subtitle Checker

- Mode: `local_only`
- Status: `warning`
- Score: `70.0`
- Issues: `0` errors, `10` warnings
- Top issue codes: `TOO_MANY_WORDS_FOR_DURATION` x8, `MODEL_ARTIFACT` x1, `EARLY_TRANSCRIPT_END` x1

### Strategy Scenarios

| Scenario | Arg | Detected | Confidence | Layout | Render success | Top-5 overlap note |
| --- | --- | --- | ---: | --- | --- | --- |
| auto | `auto` | `commentary` | 0.97 | `{'stable_subject_crop': 5}` | True | - |
| manual_commentary | `commentary` | `commentary` | 1.00 | `{'stable_subject_crop': 5}` | True | 5/5 overlap vs auto |
| compare_podcast | `podcast` | `podcast` | 1.00 | `{'speaker_face_crop': 5}` | True | 5/5 overlap vs auto |
| compare_generic | `generic` | `generic` | 1.00 | `{'safe_center_crop': 5}` | True | 4/5 overlap vs auto |

### Pairwise Overlap

- `auto` vs `manual_commentary`: `5/5` overlapping clips (`1.00`)
- `auto` vs `compare_podcast`: `5/5` overlapping clips (`1.00`)
- `auto` vs `compare_generic`: `4/5` overlapping clips (`0.80`)
- `manual_commentary` vs `compare_podcast`: `5/5` overlapping clips (`1.00`)
- `manual_commentary` vs `compare_generic`: `4/5` overlapping clips (`0.80`)
- `compare_podcast` vs `compare_generic`: `4/5` overlapping clips (`0.80`)

### Top Clips

#### auto

- `17:40.72 - 18:16.46` | score `83.0` | reasons: good speech density for a short clip, contains high-importance transcript moments, stays relatively clear despite overlap risk
- `11:42.74 - 12:25.98` | score `79.55` | reasons: contains high-importance transcript moments, good speech density for a short clip, starts with a stronger hook signal
- `10:12.83 - 10:48.78` | score `78.92` | reasons: good speech density for a short clip, contains high-importance transcript moments, stays relatively clear despite overlap risk
- `22:37.76 - 23:08.12` | score `78.64` | reasons: good speech density for a short clip, contains high-importance transcript moments, starts with a stronger hook signal
- `13:06.56 - 13:43.52` | score `78.19` | reasons: good speech density for a short clip, contains high-importance transcript moments, starts with a stronger hook signal

#### manual_commentary

- `17:40.72 - 18:16.46` | score `83.0` | reasons: good speech density for a short clip, contains high-importance transcript moments, stays relatively clear despite overlap risk
- `11:42.74 - 12:25.98` | score `79.55` | reasons: contains high-importance transcript moments, good speech density for a short clip, starts with a stronger hook signal
- `10:12.83 - 10:48.78` | score `78.92` | reasons: good speech density for a short clip, contains high-importance transcript moments, stays relatively clear despite overlap risk
- `22:37.76 - 23:08.12` | score `78.64` | reasons: good speech density for a short clip, contains high-importance transcript moments, starts with a stronger hook signal
- `13:06.56 - 13:43.52` | score `78.19` | reasons: good speech density for a short clip, contains high-importance transcript moments, starts with a stronger hook signal

#### compare_podcast

- `17:40.72 - 18:16.46` | score `74.96` | reasons: good speech density for a short clip, starts with a stronger hook signal, stays relatively clear despite overlap risk
- `11:42.74 - 12:25.98` | score `71.41` | reasons: good speech density for a short clip, starts with a stronger hook signal, stays relatively clear despite overlap risk
- `22:37.76 - 23:08.12` | score `70.8` | reasons: good speech density for a short clip, starts with a stronger hook signal, stays relatively clear despite overlap risk
- `10:12.83 - 10:48.78` | score `70.66` | reasons: good speech density for a short clip, ends with a clearer payoff signal, stays relatively clear despite overlap risk
- `13:06.56 - 13:44.74` | score `70.18` | reasons: good speech density for a short clip, starts with a stronger hook signal, stays relatively clear despite overlap risk

#### compare_generic

- `17:40.72 - 18:16.46` | score `79.83` | reasons: contains punchy or emotional language, good speech density for a short clip, starts with a stronger hook signal
- `11:42.74 - 12:25.98` | score `76.24` | reasons: contains punchy or emotional language, contains high-importance transcript moments, good speech density for a short clip
- `20:43.05 - 21:13.50` | score `75.5` | reasons: contains punchy or emotional language, starts with a stronger hook signal, contains high-importance transcript moments
- `10:12.83 - 10:48.78` | score `74.96` | reasons: contains punchy or emotional language, good speech density for a short clip, ends with a clearer payoff signal
- `22:37.76 - 23:08.12` | score `74.22` | reasons: contains punchy or emotional language, good speech density for a short clip, contains high-importance transcript moments

### Rendering

- `auto`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`467`, layout_modes=`{'stable_subject_crop': 5}`, crop_modes=`{'balanced': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`
- `manual_commentary`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`467`, layout_modes=`{'stable_subject_crop': 5}`, crop_modes=`{'balanced': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`
- `compare_podcast`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`206`, layout_modes=`{'speaker_face_crop': 5}`, crop_modes=`{'speaker_focus': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`
- `compare_generic`: render_success=`True`, face_tracking_success=`0`, center_fallback=`0`, zoom_samples=`0`, layout_modes=`{'safe_center_crop': 5}`, crop_modes=`{'balanced': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`

### Layout / 9:16 Rendering

- `auto`: layout=`{'stable_subject_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'subject': 5}`, tracking_modes=`{'dynamic_face_tracking': 5}`
- `manual_commentary`: layout=`{'stable_subject_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'subject': 5}`, tracking_modes=`{'dynamic_face_tracking': 5}`
- `compare_podcast`: layout=`{'speaker_face_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'speaker_face': 5}`, tracking_modes=`{'dynamic_face_tracking': 5}`
- `compare_generic`: layout=`{'safe_center_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'center': 5}`, tracking_modes=`{'safe_center_crop': 5}`

### Findings

- Auto classification matched the expected type (commentary) with confidence 0.97.
- Subtitle checker reported warnings (10), but no hard failure.
- All rendered benchmark scenarios produced the requested subtitled clips.

## Roman Giertych commentary essay

- Expected content type: `commentary`
- Expected speaker mode: `single`
- Status: `completed`
- Source URL: https://www.youtube.com/watch?v=FheyKl2x73A
- Description: Single-narrator political commentary / explainer essay with archival visuals. Previously tracked as generic/commentary-like before the dedicated commentary class was introduced.
- Notes: Added as a commentary benchmark. It resembles a narrated commentary video more than a dialogue-driven podcast.
- Transcript preparation: `reused_local_transcript`
- Heatmap source: `existing_heatmap`

### Transcript / Diarization

- Segments: `439`
- Speakers: `1`
- Speaker switches: `0`
- Dominant speaker ratio: `1.0`
- Diarization status: `applied`
- Fallback used: `False`
- Raw cluster count: `4`
- Final speaker count: `1`
- Single-speaker likelihood: `0.42`
- Multi-speaker evidence: `0.64`
- Clusters merged: `0`
- Tiny clusters removed: `0`
- Decision reason: `collapsed_to_single_speaker_due_to_weak_multi_evidence`

### Subtitle Checker

- Mode: `local_only`
- Status: `warning`
- Score: `100.0`
- Issues: `0` errors, `0` warnings

### Strategy Scenarios

| Scenario | Arg | Detected | Confidence | Layout | Render success | Top-5 overlap note |
| --- | --- | --- | ---: | --- | --- | --- |
| auto | `auto` | `commentary` | 0.97 | `{'stable_subject_crop': 5}` | True | - |
| manual_commentary | `commentary` | `commentary` | 1.00 | `{'stable_subject_crop': 5}` | True | 5/5 overlap vs auto |
| compare_podcast | `podcast` | `podcast` | 1.00 | `{'speaker_face_crop': 5}` | True | 4/5 overlap vs auto |
| compare_generic | `generic` | `generic` | 1.00 | `{'safe_center_crop': 5}` | True | 4/5 overlap vs auto |

### Pairwise Overlap

- `auto` vs `manual_commentary`: `5/5` overlapping clips (`1.00`)
- `auto` vs `compare_podcast`: `4/5` overlapping clips (`0.80`)
- `auto` vs `compare_generic`: `4/5` overlapping clips (`0.80`)
- `manual_commentary` vs `compare_podcast`: `4/5` overlapping clips (`0.80`)
- `manual_commentary` vs `compare_generic`: `4/5` overlapping clips (`0.80`)
- `compare_podcast` vs `compare_generic`: `3/5` overlapping clips (`0.60`)

### Top Clips

#### auto

- `19:54.36 - 20:31.18` | score `80.4` | reasons: good speech density for a short clip, starts with a stronger hook signal, contains high-importance transcript moments
- `19:09.66 - 19:41.82` | score `78.9` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, starts with a stronger hook signal
- `00:53.90 - 01:26.86` | score `74.27` | reasons: stays relatively clear despite overlap risk, starts with a stronger hook signal, good speech density for a short clip
- `11:14.48 - 11:53.92` | score `72.38` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, contains high-importance transcript moments
- `04:13.30 - 04:54.70` | score `71.83` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, contains high-importance transcript moments

#### manual_commentary

- `19:54.36 - 20:31.18` | score `80.4` | reasons: good speech density for a short clip, starts with a stronger hook signal, contains high-importance transcript moments
- `19:09.66 - 19:41.82` | score `78.9` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, starts with a stronger hook signal
- `00:53.90 - 01:26.86` | score `74.27` | reasons: stays relatively clear despite overlap risk, starts with a stronger hook signal, good speech density for a short clip
- `11:14.48 - 11:53.92` | score `72.38` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, contains high-importance transcript moments
- `04:13.30 - 04:54.70` | score `71.83` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, contains high-importance transcript moments

#### compare_podcast

- `19:09.66 - 20:01.48` | score `72.71` | reasons: good speech density for a short clip, starts with a stronger hook signal, ends with a clearer payoff signal
- `00:53.90 - 01:26.86` | score `67.1` | reasons: starts with a stronger hook signal, good speech density for a short clip, stays relatively clear despite overlap risk
- `11:14.48 - 11:53.92` | score `64.25` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, ends with a clearer payoff signal
- `04:13.30 - 04:54.70` | score `64.23` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, ends with a clearer payoff signal
- `13:39.04 - 14:30.56` | score `62.62` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, ends with a clearer payoff signal

#### compare_generic

- `19:54.36 - 20:31.18` | score `76.24` | reasons: contains punchy or emotional language, good speech density for a short clip, starts with a stronger hook signal
- `19:09.66 - 19:41.82` | score `75.54` | reasons: contains punchy or emotional language, good speech density for a short clip, starts with a stronger hook signal
- `00:53.90 - 01:26.86` | score `70.49` | reasons: contains punchy or emotional language, starts with a stronger hook signal, good speech density for a short clip
- `11:14.48 - 11:53.92` | score `69.56` | reasons: contains punchy or emotional language, good speech density for a short clip, contains high-importance transcript moments
- `12:23.12 - 12:58.34` | score `68.98` | reasons: contains punchy or emotional language, good speech density for a short clip, contains high-importance transcript moments

### Rendering

- `auto`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`0`, layout_modes=`{'stable_subject_crop': 5}`, crop_modes=`{'balanced': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`
- `manual_commentary`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`0`, layout_modes=`{'stable_subject_crop': 5}`, crop_modes=`{'balanced': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`
- `compare_podcast`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`0`, layout_modes=`{'speaker_face_crop': 5}`, crop_modes=`{'speaker_focus': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`
- `compare_generic`: render_success=`True`, face_tracking_success=`0`, center_fallback=`0`, zoom_samples=`0`, layout_modes=`{'safe_center_crop': 5}`, crop_modes=`{'balanced': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`

### Layout / 9:16 Rendering

- `auto`: layout=`{'stable_subject_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'subject': 5}`, tracking_modes=`{'dynamic_face_tracking': 5}`
- `manual_commentary`: layout=`{'stable_subject_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'subject': 5}`, tracking_modes=`{'dynamic_face_tracking': 5}`
- `compare_podcast`: layout=`{'speaker_face_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'speaker_face': 5}`, tracking_modes=`{'dynamic_face_tracking': 5}`
- `compare_generic`: layout=`{'safe_center_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'center': 5}`, tracking_modes=`{'safe_center_crop': 5}`

### Findings

- Auto classification matched the expected type (commentary) with confidence 0.97.
- Subtitle checker reported warnings (0), but no hard failure.
- All rendered benchmark scenarios produced the requested subtitled clips.

## Putin parade commentary

- Expected content type: `commentary`
- Expected speaker mode: `single`
- Status: `completed`
- Source URL: https://www.youtube.com/watch?v=7t9yv4d318U
- Description: Single-host current-events commentary about Russia's parade and Ukraine. Previously tracked as generic/commentary-like before the dedicated commentary class was introduced.
- Notes: Added as a commentary benchmark. Useful for testing news-like monologue material against the dedicated commentary route.
- Transcript preparation: `reused_local_transcript`
- Heatmap source: `existing_heatmap`

### Transcript / Diarization

- Segments: `229`
- Speakers: `1`
- Speaker switches: `0`
- Dominant speaker ratio: `1.0`
- Diarization status: `applied`
- Fallback used: `False`
- Raw cluster count: `4`
- Final speaker count: `1`
- Single-speaker likelihood: `0.7`
- Multi-speaker evidence: `0.46`
- Clusters merged: `1`
- Tiny clusters removed: `0`
- Decision reason: `collapsed_to_single_speaker_due_to_weak_multi_evidence`

### Subtitle Checker

- Mode: `local_only`
- Status: `warning`
- Score: `100.0`
- Issues: `0` errors, `0` warnings

### Strategy Scenarios

| Scenario | Arg | Detected | Confidence | Layout | Render success | Top-5 overlap note |
| --- | --- | --- | ---: | --- | --- | --- |
| auto | `auto` | `commentary` | 0.97 | `{'stable_subject_crop': 5}` | True | - |
| manual_commentary | `commentary` | `commentary` | 1.00 | `{'stable_subject_crop': 5}` | True | 5/5 overlap vs auto |
| compare_podcast | `podcast` | `podcast` | 1.00 | `{'speaker_face_crop': 5}` | True | 3/5 overlap vs auto |
| compare_generic | `generic` | `generic` | 1.00 | `{'safe_center_crop': 5}` | True | 3/5 overlap vs auto |

### Pairwise Overlap

- `auto` vs `manual_commentary`: `5/5` overlapping clips (`1.00`)
- `auto` vs `compare_podcast`: `3/5` overlapping clips (`0.60`)
- `auto` vs `compare_generic`: `3/5` overlapping clips (`0.60`)
- `manual_commentary` vs `compare_podcast`: `3/5` overlapping clips (`0.60`)
- `manual_commentary` vs `compare_generic`: `3/5` overlapping clips (`0.60`)
- `compare_podcast` vs `compare_generic`: `4/5` overlapping clips (`0.80`)

### Top Clips

#### auto

- `06:57.53 - 07:47.13` | score `73.9` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, starts with a stronger hook signal
- `00:55.26 - 01:35.33` | score `73.17` | reasons: good speech density for a short clip, contains high-importance transcript moments, stays relatively clear despite overlap risk
- `12:09.81 - 12:46.25` | score `70.26` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, contains high-importance transcript moments
- `10:21.07 - 10:57.49` | score `68.04` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, starts with a stronger hook signal
- `06:22.29 - 06:57.13` | score `67.26` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, contains high-importance transcript moments

#### manual_commentary

- `06:57.53 - 07:47.13` | score `73.9` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, starts with a stronger hook signal
- `00:55.26 - 01:35.33` | score `73.17` | reasons: good speech density for a short clip, contains high-importance transcript moments, stays relatively clear despite overlap risk
- `12:09.81 - 12:46.25` | score `70.26` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, contains high-importance transcript moments
- `10:21.07 - 10:57.49` | score `68.04` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, starts with a stronger hook signal
- `06:22.29 - 06:57.13` | score `67.26` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, contains high-importance transcript moments

#### compare_podcast

- `06:57.53 - 07:47.13` | score `67.18` | reasons: good speech density for a short clip, starts with a stronger hook signal, stays relatively clear despite overlap risk
- `00:55.26 - 01:35.33` | score `64.12` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, ends with a clearer payoff signal
- `12:09.81 - 12:46.25` | score `62.02` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, ends with a clearer payoff signal
- `09:57.49 - 10:39.61` | score `59.46` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, ends with a clearer payoff signal
- `13:30.69 - 14:11.31` | score `58.05` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, ends with a clearer payoff signal

#### compare_generic

- `00:55.26 - 01:35.33` | score `70.98` | reasons: contains punchy or emotional language, contains high-importance transcript moments, good speech density for a short clip
- `06:57.53 - 07:47.13` | score `70.62` | reasons: contains punchy or emotional language, good speech density for a short clip, starts with a stronger hook signal
- `12:09.81 - 12:46.25` | score `67.89` | reasons: contains punchy or emotional language, good speech density for a short clip, contains high-importance transcript moments
- `09:57.49 - 10:27.19` | score `67.31` | reasons: contains punchy or emotional language, good speech density for a short clip, contains high-importance transcript moments
- `00:16.22 - 00:47.74` | score `66.25` | reasons: contains punchy or emotional language, good speech density for a short clip, contains high-importance transcript moments

### Rendering

- `auto`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`303`, layout_modes=`{'stable_subject_crop': 5}`, crop_modes=`{'balanced': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`
- `manual_commentary`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`303`, layout_modes=`{'stable_subject_crop': 5}`, crop_modes=`{'balanced': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`
- `compare_podcast`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`107`, layout_modes=`{'speaker_face_crop': 5}`, crop_modes=`{'speaker_focus': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`
- `compare_generic`: render_success=`True`, face_tracking_success=`0`, center_fallback=`0`, zoom_samples=`0`, layout_modes=`{'safe_center_crop': 5}`, crop_modes=`{'balanced': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`

### Layout / 9:16 Rendering

- `auto`: layout=`{'stable_subject_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'subject': 5}`, tracking_modes=`{'dynamic_face_tracking': 5}`
- `manual_commentary`: layout=`{'stable_subject_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'subject': 5}`, tracking_modes=`{'dynamic_face_tracking': 5}`
- `compare_podcast`: layout=`{'speaker_face_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'speaker_face': 5}`, tracking_modes=`{'dynamic_face_tracking': 5}`
- `compare_generic`: layout=`{'safe_center_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'center': 5}`, tracking_modes=`{'safe_center_crop': 5}`

### Findings

- Auto classification matched the expected type (commentary) with confidence 0.97.
- Subtitle checker reported warnings (0), but no hard failure.
- All rendered benchmark scenarios produced the requested subtitled clips.

## Magenta Team two-person podcast

- Expected content type: `podcast`
- Expected speaker mode: `multi`
- Status: `completed`
- Source URL: https://www.youtube.com/watch?v=9Qa-szUnNaY
- Description: Short branded two-person conversation episode from Magenta Team. Suitable as a real multi-speaker podcast benchmark within the current taxonomy.
- Notes: Added to validate whether the classifier and diarization behave better on a true conversation than on commentary-like monologues.
- Transcript preparation: `reused_local_transcript`
- Heatmap source: `existing_heatmap`

### Transcript / Diarization

- Segments: `388`
- Speakers: `2`
- Speaker switches: `99`
- Dominant speaker ratio: `0.5593`
- Diarization status: `applied`
- Fallback used: `False`
- Raw cluster count: `4`
- Final speaker count: `2`
- Single-speaker likelihood: `0.08`
- Multi-speaker evidence: `0.86`
- Clusters merged: `2`
- Tiny clusters removed: `0`
- Decision reason: `kept_multi_speaker_clusters`

### Subtitle Checker

- Mode: `local_only`
- Status: `warning`
- Score: `88.0`
- Issues: `0` errors, `3` warnings
- Top issue codes: `TOO_MANY_WORDS_FOR_DURATION` x2, `DUPLICATED_ADJACENT_TEXT` x1

### Strategy Scenarios

| Scenario | Arg | Detected | Confidence | Layout | Render success | Top-5 overlap note |
| --- | --- | --- | ---: | --- | --- | --- |
| auto | `auto` | `podcast` | 0.89 | `{'speaker_face_crop': 5}` | True | - |
| manual_podcast | `podcast` | `podcast` | 1.00 | `{'speaker_face_crop': 5}` | True | 5/5 overlap vs auto |
| compare_generic | `generic` | `generic` | 1.00 | `{'safe_center_crop': 5}` | True | 4/5 overlap vs auto |

### Pairwise Overlap

- `auto` vs `manual_podcast`: `5/5` overlapping clips (`1.00`)
- `auto` vs `compare_generic`: `4/5` overlapping clips (`0.80`)
- `manual_podcast` vs `compare_generic`: `4/5` overlapping clips (`0.80`)

### Top Clips

#### auto

- `02:10.86 - 02:46.86` | score `85.58` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, starts with a stronger hook signal
- `07:49.37 - 08:26.37` | score `85.07` | reasons: has speaker dynamics or conversational turns, starts with a stronger hook signal, good speech density for a short clip
- `02:56.38 - 03:40.56` | score `83.01` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, starts with a stronger hook signal
- `00:03.08 - 00:44.42` | score `81.48` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, ends with a clearer payoff signal
- `04:26.28 - 05:09.34` | score `81.39` | reasons: has speaker dynamics or conversational turns, ends with a clearer payoff signal, good speech density for a short clip

#### manual_podcast

- `02:10.86 - 02:46.86` | score `85.58` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, starts with a stronger hook signal
- `07:49.37 - 08:26.37` | score `85.07` | reasons: has speaker dynamics or conversational turns, starts with a stronger hook signal, good speech density for a short clip
- `02:56.38 - 03:40.56` | score `83.01` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, starts with a stronger hook signal
- `00:03.08 - 00:44.42` | score `81.48` | reasons: has speaker dynamics or conversational turns, good speech density for a short clip, ends with a clearer payoff signal
- `04:26.28 - 05:09.34` | score `81.39` | reasons: has speaker dynamics or conversational turns, ends with a clearer payoff signal, good speech density for a short clip

#### compare_generic

- `07:49.37 - 08:26.37` | score `81.84` | reasons: contains punchy or emotional language, contains high-importance transcript moments, starts with a stronger hook signal
- `02:10.86 - 02:46.86` | score `80.5` | reasons: contains punchy or emotional language, good speech density for a short clip, starts with a stronger hook signal
- `04:26.28 - 05:09.34` | score `78.79` | reasons: contains punchy or emotional language, contains high-importance transcript moments, ends with a clearer payoff signal
- `00:42.70 - 01:27.20` | score `78.5` | reasons: contains punchy or emotional language, starts with a stronger hook signal, ends with a clearer payoff signal
- `02:56.38 - 03:40.56` | score `78.15` | reasons: contains punchy or emotional language, good speech density for a short clip, starts with a stronger hook signal

### Rendering

- `auto`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`126`, layout_modes=`{'speaker_face_crop': 5}`, crop_modes=`{'speaker_focus': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`
- `manual_podcast`: render_success=`True`, face_tracking_success=`5`, center_fallback=`0`, zoom_samples=`126`, layout_modes=`{'speaker_face_crop': 5}`, crop_modes=`{'speaker_focus': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`
- `compare_generic`: render_success=`True`, face_tracking_success=`0`, center_fallback=`0`, zoom_samples=`0`, layout_modes=`{'safe_center_crop': 5}`, crop_modes=`{'balanced': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`

### Layout / 9:16 Rendering

- `auto`: layout=`{'speaker_face_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'speaker_face': 5}`, tracking_modes=`{'dynamic_face_tracking': 5}`
- `manual_podcast`: layout=`{'speaker_face_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'speaker_face': 5}`, tracking_modes=`{'dynamic_face_tracking': 5}`
- `compare_generic`: layout=`{'safe_center_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'center': 5}`, tracking_modes=`{'safe_center_crop': 5}`

### Findings

- Auto classification matched the expected type (podcast) with confidence 0.8881.
- Subtitle checker reported warnings (3), but no hard failure.
- All rendered benchmark scenarios produced the requested subtitled clips.

## Canva presentation tutorial

- Expected content type: `tutorial`
- Expected speaker mode: `single`
- Status: `completed`
- Source URL: https://www.youtube.com/watch?v=t9ge2mWHoxU
- Description: Single-presenter Canva screencast on making engaging presentations. Suitable as a tutorial benchmark with screen-focused visuals and instructional speech.
- Notes: Added to test the tutorial path before tuning classifier or strategy weights.
- Transcript preparation: `reused_local_transcript`
- Heatmap source: `existing_heatmap`

### Transcript / Diarization

- Segments: `826`
- Speakers: `1`
- Speaker switches: `0`
- Dominant speaker ratio: `1.0`
- Diarization status: `applied`
- Fallback used: `False`
- Raw cluster count: `4`
- Final speaker count: `1`
- Single-speaker likelihood: `0.7`
- Multi-speaker evidence: `0.46`
- Clusters merged: `0`
- Tiny clusters removed: `0`
- Decision reason: `collapsed_to_single_speaker_due_to_weak_multi_evidence`

### Subtitle Checker

- Mode: `local_only`
- Status: `warning`
- Score: `96.0`
- Issues: `0` errors, `1` warnings
- Top issue codes: `MODEL_ARTIFACT` x1

### Strategy Scenarios

| Scenario | Arg | Detected | Confidence | Layout | Render success | Top-5 overlap note |
| --- | --- | --- | ---: | --- | --- | --- |
| auto | `auto` | `tutorial` | 0.97 | `{'full_frame_blur_background': 5}` | True | - |
| manual_tutorial | `tutorial` | `tutorial` | 1.00 | `{'full_frame_blur_background': 5}` | True | 5/5 overlap vs auto |
| compare_generic | `generic` | `generic` | 1.00 | `{'safe_center_crop': 5}` | True | 3/5 overlap vs auto |

### Pairwise Overlap

- `auto` vs `manual_tutorial`: `5/5` overlapping clips (`1.00`)
- `auto` vs `compare_generic`: `3/5` overlapping clips (`0.60`)
- `manual_tutorial` vs `compare_generic`: `3/5` overlapping clips (`0.60`)

### Top Clips

#### auto

- `07:31.31 - 08:07.91` | score `67.14` | reasons: strong heatmap support, stays relatively clear despite overlap risk, good speech density for a short clip
- `25:46.46 - 26:22.20` | score `66.87` | reasons: strong heatmap support, stays relatively clear despite overlap risk, good speech density for a short clip
- `09:56.17 - 10:31.10` | score `66.6` | reasons: strong heatmap support, stays relatively clear despite overlap risk, good speech density for a short clip
- `17:25.96 - 18:01.94` | score `66.07` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, fits a strong short-form duration window
- `26:53.84 - 27:29.16` | score `64.65` | reasons: strong heatmap support, good speech density for a short clip, stays relatively clear despite overlap risk

#### manual_tutorial

- `07:31.31 - 08:07.91` | score `67.14` | reasons: strong heatmap support, stays relatively clear despite overlap risk, good speech density for a short clip
- `25:46.46 - 26:22.20` | score `66.87` | reasons: strong heatmap support, stays relatively clear despite overlap risk, good speech density for a short clip
- `09:56.17 - 10:31.10` | score `66.6` | reasons: strong heatmap support, stays relatively clear despite overlap risk, good speech density for a short clip
- `17:25.96 - 18:01.94` | score `66.07` | reasons: good speech density for a short clip, stays relatively clear despite overlap risk, fits a strong short-form duration window
- `26:53.84 - 27:29.16` | score `64.65` | reasons: strong heatmap support, good speech density for a short clip, stays relatively clear despite overlap risk

#### compare_generic

- `07:31.31 - 08:07.91` | score `70.81` | reasons: strong heatmap support, contains high-importance transcript moments, contains punchy or emotional language
- `09:05.65 - 09:42.82` | score `70.38` | reasons: strong heatmap support, contains high-importance transcript moments, good speech density for a short clip
- `09:56.17 - 10:31.10` | score `68.99` | reasons: strong heatmap support, contains punchy or emotional language, contains high-importance transcript moments
- `25:46.46 - 26:22.20` | score `68.86` | reasons: strong heatmap support, contains high-importance transcript moments, good speech density for a short clip
- `02:47.91 - 03:27.87` | score `63.61` | reasons: strong heatmap support, contains punchy or emotional language, good speech density for a short clip

### Rendering

- `auto`: render_success=`True`, face_tracking_success=`0`, center_fallback=`0`, zoom_samples=`0`, layout_modes=`{'full_frame_blur_background': 5}`, crop_modes=`{'content_preserving': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`
- `manual_tutorial`: render_success=`True`, face_tracking_success=`0`, center_fallback=`0`, zoom_samples=`0`, layout_modes=`{'full_frame_blur_background': 5}`, crop_modes=`{'content_preserving': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`
- `compare_generic`: render_success=`True`, face_tracking_success=`0`, center_fallback=`0`, zoom_samples=`0`, layout_modes=`{'safe_center_crop': 5}`, crop_modes=`{'balanced': 5}`, output=`1080x1920` aspect=`9:16` vertical_9_16=`True`

### Layout / 9:16 Rendering

- `auto`: layout=`{'full_frame_blur_background': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'screen': 5}`, tracking_modes=`{'full_frame_blur_background': 5}`
- `manual_tutorial`: layout=`{'full_frame_blur_background': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'screen': 5}`, tracking_modes=`{'full_frame_blur_background': 5}`
- `compare_generic`: layout=`{'safe_center_crop': 5}`, output=`1080x1920`, aspect=`9:16`, vertical_9_16=`True`, crop_priorities=`{'center': 5}`, tracking_modes=`{'safe_center_crop': 5}`

### Findings

- Auto classification matched the expected type (tutorial) with confidence 0.97.
- Subtitle checker reported warnings (1), but no hard failure.
- All rendered benchmark scenarios produced the requested subtitled clips.

## Layout / 9:16 Rendering

- Requested layout mode: `auto`
- `emeritos_gameplay/auto` -> layout `{'gameplay_priority_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `emeritos_gameplay/manual_gameplay` -> layout `{'gameplay_priority_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `emeritos_gameplay/compare_podcast` -> layout `{'speaker_face_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `emeritos_gameplay/compare_generic` -> layout `{'safe_center_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `ukraine_war_report/auto` -> layout `{'stable_subject_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `ukraine_war_report/manual_commentary` -> layout `{'stable_subject_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `ukraine_war_report/compare_podcast` -> layout `{'speaker_face_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `ukraine_war_report/compare_generic` -> layout `{'safe_center_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `roman_giertych_commentary/auto` -> layout `{'stable_subject_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `roman_giertych_commentary/manual_commentary` -> layout `{'stable_subject_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `roman_giertych_commentary/compare_podcast` -> layout `{'speaker_face_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `roman_giertych_commentary/compare_generic` -> layout `{'safe_center_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `putin_parade_commentary/auto` -> layout `{'stable_subject_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `putin_parade_commentary/manual_commentary` -> layout `{'stable_subject_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `putin_parade_commentary/compare_podcast` -> layout `{'speaker_face_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `putin_parade_commentary/compare_generic` -> layout `{'safe_center_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `magenta_team_podcast/auto` -> layout `{'speaker_face_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `magenta_team_podcast/manual_podcast` -> layout `{'speaker_face_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `magenta_team_podcast/compare_generic` -> layout `{'safe_center_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `canva_presentation_tutorial/auto` -> layout `{'full_frame_blur_background': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `canva_presentation_tutorial/manual_tutorial` -> layout `{'full_frame_blur_background': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`
- `canva_presentation_tutorial/compare_generic` -> layout `{'safe_center_crop': 5}`, output `1080x1920`, aspect `9:16`, vertical_9_16 `True`

## Human Review / Selection Quality

- Records with complete human scores: `5`
- Minimum recommended complete records before weight tuning: `10`
- Human-review signal is still small, so this iteration uses conservative rules and diagnostics rather than aggressive weight tuning.
- Auto records with complete human scores: `5`
- Current template complete records: `1` / `110`; archive complete records: `4` / `4`
- Average human scores: relevance `2.60`, boundary `2.80`, crop `3.80`

### Reviewed Averages

- Content type `gameplay` (`n=5`): relevance `2.60`, boundary `2.80`, crop `3.80`
- Scenario `auto` (`n=5`): relevance `2.60`, boundary `2.80`, crop `3.80`

### Note Issues

- `bad boundary`: `3`
- `buy menu`: `2`
- `weak payoff`: `2`
- `setup / waiting`: `2`
- `smoke / utility`: `2`

### Data-Backed Top Problems

- Scoring chooses setup / ad-like / weak-payoff clips too often. (`score=11.40`)
- Clip boundaries still start too early or end too weakly. (`score=5.20`)
- Crop / framing still hurts readability on reviewed clips. (`score=1.20`)

### High Score / Low Relevance

- `emeritos_gameplay/auto` clip `2` (05:25.33 - 05:58.35) | local `92.97` vs human relevance `2`
- `emeritos_gameplay/auto` clip `4` (02:17.46 - 02:52.19) | local `92.62` vs human relevance `1`
- `emeritos_gameplay/auto` clip `5` (04:14.05 - 04:59.95) | local `92.07` vs human relevance `2`

### Iteration Changes

- Human review preservation now also recovers complete scored rows from the previous `benchmarks/results.json`, so historical manual scores are archived even if the CSV archive file is missing.
- The report now marks small human-review samples explicitly and lists the next clips to review instead of presenting low-N tuning as statistically strong.
- Local scoring adds conservative penalties for ad/sponsor-like text, gameplay setup/waiting/smoke/utility, weak payoff, too much preamble and contextless talk-led clips.
- Local scoring adds small positive signals for gameplay action/payoff, tutorial instructions, podcast question-response shape and complete commentary thoughts.
- Boundary refinement now records `max_duration_clamped` and can trim a low-value gameplay opening when a later action/payoff remains inside the clip.

### Ranking Movement

- Not enough complete human review exists to claim a statistically strong ranking improvement. Use the next review pass to verify whether the new penalties demote setup/ad-like/weak-payoff clips.

### Next Human Review Targets

- `emeritos_gameplay/auto` clip `1` (18:03.49 - 18:35.79) | expected `gameplay` | local `95.95`
- `canva_presentation_tutorial/auto` clip `1` (07:31.31 - 08:07.91) | expected `tutorial` | local `67.14`
- `magenta_team_podcast/auto` clip `1` (02:10.86 - 02:46.86) | expected `podcast` | local `85.58`
- `ukraine_war_report/auto` clip `1` (17:40.72 - 18:16.46) | expected `commentary` | local `83.0`
- `putin_parade_commentary/auto` clip `1` (06:57.53 - 07:47.13) | expected `commentary` | local `73.9`
- `roman_giertych_commentary/auto` clip `1` (19:54.36 - 20:31.18) | expected `commentary` | local `80.4`
- `emeritos_gameplay/auto` clip `2` (05:37.91 - 06:14.94) | expected `gameplay` | local `94.29`
- `emeritos_gameplay/auto` clip `4` (04:14.05 - 04:59.95) | expected `gameplay` | local `92.7`
- `emeritos_gameplay/auto` clip `5` (15:02.36 - 15:33.76) | expected `gameplay` | local `92.36`
- `canva_presentation_tutorial/auto` clip `2` (25:46.46 - 26:22.20) | expected `tutorial` | local `66.87`
- `canva_presentation_tutorial/auto` clip `3` (09:56.17 - 10:31.10) | expected `tutorial` | local `66.6`
- `canva_presentation_tutorial/auto` clip `4` (17:25.96 - 18:01.94) | expected `tutorial` | local `66.07`

- Biggest remaining problem after this iteration: `scoring` (Scoring chooses setup / ad-like / weak-payoff clips too often.)

## Selection Quality Tuning

- Complete human-review records available: `5`
- Tuning basis: defensive heuristics plus the existing human-review notes; the scored sample is too small for aggressive weight tuning.
- Scoring changes: ad/sponsor, buy-menu, setup/waiting, smoke/utility, weak-payoff, long-preamble and contextless-fragment penalties; small boosts for gameplay action/payoff, tutorial instruction language, podcast dialogue shape and complete commentary thoughts.
- Boundary metadata: `original_start`, `original_end`, `refined_start`, `refined_end`, `boundary_adjustment_reason`, `sentence_boundary_used`, `speaker_turn_boundary_used`, `max_duration_clamped`.
- Boundary behavior: talk-led clips are aligned to transcript sentence/segment boundaries; gameplay clips can trim low-value setup while keeping a short pre-roll before action.
- Fresh human review should prioritize:
  - `emeritos_gameplay/auto` clip `1` (18:03.49 - 18:35.79) | expected `gameplay` | local `95.95`
  - `canva_presentation_tutorial/auto` clip `1` (07:31.31 - 08:07.91) | expected `tutorial` | local `67.14`
  - `magenta_team_podcast/auto` clip `1` (02:10.86 - 02:46.86) | expected `podcast` | local `85.58`
  - `ukraine_war_report/auto` clip `1` (17:40.72 - 18:16.46) | expected `commentary` | local `83.0`
  - `putin_parade_commentary/auto` clip `1` (06:57.53 - 07:47.13) | expected `commentary` | local `73.9`
  - `roman_giertych_commentary/auto` clip `1` (19:54.36 - 20:31.18) | expected `commentary` | local `80.4`
  - `emeritos_gameplay/auto` clip `2` (05:37.91 - 06:14.94) | expected `gameplay` | local `94.29`
  - `emeritos_gameplay/auto` clip `4` (04:14.05 - 04:59.95) | expected `gameplay` | local `92.7`

## Recommendation

- Next step: `more_human_review`
- Title: Score more benchmark clips before stronger tuning
- Why: Only 5 complete human-review records are available; at least 10 are recommended before larger scoring changes.
