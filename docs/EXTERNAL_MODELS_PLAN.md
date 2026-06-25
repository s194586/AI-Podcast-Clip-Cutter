# External Models Notes

Podcast Shorts Cutter is a local-first MVP. External providers are optional upgrades, not requirements for the editor workflow.

## Current Local Limits

- Faster-Whisper transcripts can contain language mistakes.
- Heuristic speaker attribution can over-segment speakers.
- Local scoring can miss story completeness, setup, or payoff.
- Clip boundaries often need human adjustment.
- Crop and framing still benefit from manual review.

These limits are why the product is intentionally human-in-the-loop:

```text
AI proposes draft podcast clips -> user adjusts -> app renders
```

## Possible Provider Upgrades

### Transcription and Diarization

- AssemblyAI
- Deepgram
- Speechmatics
- Google Speech-to-Text

These could improve transcript quality, punctuation, casing, and speaker labels.

### Semantic Review

- Gemini or similar timestamp-aware models

This could improve hook/context/payoff reasoning, intro or ad rejection, and draft boundary suggestions.

## Evaluation Direction

Provider integrations should be evaluated by whether they reduce manual correction work:

- fewer boundary edits,
- clearer transcript excerpts,
- better subtitle readability,
- more complete short-form stories,
- fewer crop/framing surprises.

The local cutter/render stage should remain the final assembly step unless there is a strong reason to replace it.
