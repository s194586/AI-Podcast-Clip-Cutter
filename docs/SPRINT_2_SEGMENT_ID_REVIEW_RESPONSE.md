# Sprint 2 Task 2C: segment-ID review response

Gemini boundary-review responses use contract version 2. Every response contains
`review_response_contract_version: 2`, one `start_segment_id`, one
`end_segment_id`, the decision, reasoning fields, and warnings. The segment IDs
must be non-empty IDs from the supplied chronological transcript window.

Gemini does not return timestamps or option indexes. The compact request is version
3 and includes each transcript segment and its text once. It exposes eligibility
only through `start_eligible` and `end_eligible`; candidate metadata includes the
current aligned start and end segment IDs.

The backend resolves provider-selected IDs to canonical transcript segments. It
uses their timestamps exclusively, then derives compatible internal option indexes
from validated boundary options before running allowed-boundary-pair, ordering,
range, and duration validation.
Allowed pairs remain backend-only and are never sent to Gemini.

For `reject`, Gemini returns the current aligned segment IDs; the backend may
ignore those bounds when applying the rejection. A malformed response receives
one corrective retry that lists allowed segment IDs but no transcript text.

New raw results record `review_response_contract_version: 2`, selected IDs, and
backend-derived option indexes for transitional persistence/API compatibility.
Existing option-index raw results remain readable. Removing indexes entirely
from request and backend internals is deferred to Task 2D.
