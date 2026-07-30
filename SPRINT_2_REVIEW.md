# Sprint 2 review closeout

## Outcome

Sprint 2 functionality and automated validation are complete. The live Gemini
success-decision acceptance criterion is **BLOCKED_EXTERNAL**, not passed.

| Area | Status | Evidence |
| --- | --- | --- |
| Semantic boundary-review implementation | Passed | Gemini-only product review path, typed segment-ID response, backend validation, human-editable boundaries |
| Automated review regression tests | Passed | Targeted review suite and full backend suite passed after the TLS and diagnostic patches |
| Windows spawned-worker TLS | Passed | Each `google.genai.Client` receives a process-local `truststore` system TLS context |
| Provider failure handling | Passed | Safe category, source type, optional cause/context type, HTTP status, and sanitized message reach `manual_review` |
| Live Gemini connection and failure path | Confirmed | Two isolated real review flows reached `gemini-3.5-flash` and returned controlled provider failures |
| Live success decision (2H) | **BLOCKED_EXTERNAL** | Neither live attempt returned `render_ready`, `adjust_boundaries`, or `reject` |

## Live 2H evidence

Two independent, isolated 2H acceptance attempts used the normal local backend
and React UI, a temporary SQLite database, `CLIP_REVIEW_MODE=gemini`, and one
normal review request each. Both reached `gemini-3.5-flash` and ended as
`manual_review` because the provider returned HTTP 500 high-demand errors.

Each attempt had `provider_attempt_count=1`: there was no retry, fallback,
heuristic scoring, or `local_stub`. This verifies the provider connection and
controlled technical-failure path, but it does **not** verify a live successful
editorial decision. No secrets, raw response bodies, or unsanitized exceptions
are recorded in this document.

## Review contract

- Gemini's editorial outcomes are `render_ready`, `adjust_boundaries`, and
  `reject`; `recommended_action` mirrors the decision.
- A technical provider failure becomes `manual_review` and does not overwrite
  existing or user-edited boundaries.
- Valid Gemini decisions use provenance `gemini_boundary_decision` and numeric
  score provenance `not_available`.
- `manual_review` uses provenance `manual_review` and numeric score provenance
  `not_available`.
- Legacy score, privacy, crop, and context fields are not used by the Gemini
  contract and remain SQL `NULL` unless historical data explicitly provides
  them.

## Known limitation and next step

The outstanding limitation is transient external availability of
`gemini-3.5-flash`. A future single fresh 2H attempt may confirm the live
success path when the provider is available. It should not change the model,
timeouts, retry policy, or introduce a fallback merely to obtain a passing
result.
