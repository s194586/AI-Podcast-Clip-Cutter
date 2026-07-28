# Sprint 2 compact Gemini review request

Gemini receives review request contract version `3`. The request contains candidate
metadata and one chronological `segments` list. Every transcript segment ID and its
text appear exactly once; `relation` marks it as `before`, `candidate`, or `after`.
Optional `speaker` is included only when available.

Each segment carries boolean `start_eligible` and `end_eligible` fields. Gemini
receives no boundary-option indexes, boundary options, or allowed pairs. Candidate
metadata identifies the current aligned start and end only by segment ID.

`allowed_boundary_pairs` and boundary options remain backend-only compatibility
data. The backend resolves Gemini's selected IDs to canonical transcript segments
for timestamps, derives persistence-compatible indexes from the corresponding
boundary options, and rejects any inconsistent internal mapping explicitly.
