# External Models Plan

## Current local pipeline limitations

The local-first stack delivered meaningful improvements in:
- 9:16 layout and gameplay/tutorial readability
- duplicate reduction
- deterministic subtitle speaker colors
- safer boundary padding

However, the latest human review still shows that the main bottlenecks are now upstream:
- faster-whisper transcripts still contain language mistakes
- heuristic diarization is unstable and often over-segments speakers
- local candidate selection has weak story understanding
- clips still start without context or end without payoff
- advertisement / intro / setup segments can still leak into outputs
- local transcript-driven selection cannot reliably tell whether a short tells a complete story

## Target architecture

```text
Input video / YouTube URL
  -> provider transcription + diarization
  -> provider semantic video/audio analysis
  -> story-complete clip candidates
  -> local cutter/render 9:16
  -> dashboard/review
```

The long-term goal is to move semantic understanding and subtitle quality away from heuristic patches and toward purpose-built external models or APIs, while keeping the local cutter/render stage as the final assembly step.

## Provider candidates

### Transcription + diarization
- AssemblyAI
  Strong speech-to-text, diarization, summaries, and speech understanding features.
- Deepgram
  Fast transcription, diarization alternatives, and a potentially cheaper/scalable path.
- Speechmatics / Google STT
  Reasonable alternatives depending on language quality, diarization stability, and cost.

### Video/audio semantic analysis
- Gemini Video Understanding
  Target direction for timestamp-aware semantic clip analysis:
  - hook/context/payoff reasoning
  - advertisement / intro rejection
  - clip story completeness
  - suggested candidate boundaries with rationale

### Product-quality baseline references
- OpusClip
- Vizard

These are not required integrations. They are useful as product baselines when evaluating whether our benchmark outputs are actually competitive.

## Evaluation plan

For every new provider integration, compare it against the local baseline on:

### Transcript quality
- word error rate or a lightweight qualitative proxy
- language quality
- punctuation and casing
- subtitle readability

### Diarization quality
- detected speaker count vs expected reality
- speaker label stability
- effective speaker count after postprocessing
- whether the same speaker keeps a consistent identity in subtitles

### Story completeness
- hook score
- context completeness
- payoff clarity
- whether the clip tells a self-contained story
- whether the clip feels like an intro / ad / setup instead of a short

### Output quality
- does the selected clip need less manual boundary fixing?
- are subtitles more readable?
- does human review show fewer notes about context, boring setup, no payoff, and language mistakes?

## Migration plan

### Phase 1
- keep local cutter/render
- keep auto-only + dedup review flow
- integrate provider adapters behind clean interfaces

### Phase 2
- plug in provider transcription + diarization
- compare transcript and speaker stability against local baseline

### Phase 3
- plug in Gemini Video Understanding or equivalent semantic provider
- use timestamp-aware candidate generation and rejection instead of patching local heuristics

### Phase 4
- rebuild benchmark corpus on fresh films
- evaluate the new architecture against archived local-only results
