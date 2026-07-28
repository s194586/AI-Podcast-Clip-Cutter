# Sprint 2 compact Gemini review request

Gemini receives review request contract version `2`. The request contains candidate
metadata and one chronological `segments` list. Every transcript segment ID and its
text appear exactly once; `relation` marks it as `before`, `candidate`, or `after`.
Optional `speaker` is included only when available.

Each segment carries nullable `start_option_index` and `end_option_index`. These
preserve the existing option-index response contract: Gemini still returns
`selected_start_option_index` and `selected_end_option_index` in Task 2B.

`allowed_boundary_pairs` remains internal backend validation data and is never sent
to Gemini. The backend continues to reject an otherwise known start/end combination
that is not an allowed pair. Segment-ID selections are intentionally deferred to
Task 2C.
