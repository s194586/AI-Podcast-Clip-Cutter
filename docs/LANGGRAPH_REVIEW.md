# LangGraph Boundary Review

## Purpose

LangGraph is the internal workflow orchestrator for one existing semantic clip
boundary review. It is not a recommendation agent or a multi-agent simulation.
The runtime dependency is pinned to LangGraph 1.1.10, the newest tested line
compatible with the Airflow 3.3.0 Python 3.12 constraint set.
Gemini still makes the semantic choice from the existing numbered start/end
options and `allowed_boundary_pairs`. Existing backend validation remains
authoritative and local heuristics do not replace Gemini's semantic selection.

The public FastAPI and React contracts remain unchanged. Batch review invokes
one isolated graph per clip, so a controlled failure or `manual_review` outcome
does not block later clips.

## State graph

```text
START
  -> build_review_context
  -> invoke_reviewer
  -> validate_review
       -> apply_review -> END
       -> prepare_corrective_retry -> invoke_reviewer
       -> finalize_manual_review -> END
       -> finalize_provider_failure -> END
       -> finalize_cancelled -> END
```

`validate_review` has conditional edges for valid, retryable-invalid,
second-invalid, provider-failure, and cancellation outcomes. `apply_review`
checks cancellation again before persistence. `invoke_reviewer` is the only
provider-call node. The retry edge is guarded by `retry_used`, so one clip can
make no more than two provider calls: the initial call and one corrective call.

The corrective call is only for invalid structured output or authoritative
domain validation failure, such as an unknown option, a pair absent from
`allowed_boundary_pairs`, reversed boundaries, invalid duration, an
out-of-range boundary, or inconsistent segment mapping. Its feedback contains
only a concise error category and valid option indexes. It does not contain a
complete transcript, prompt, secret, raw provider body, or filesystem path.

Quota/rate-limit responses, timeouts, HTTP 499, provider availability failures,
invalid credentials, missing configuration, cancellation, and batch deadline
exhaustion do not take the corrective edge. Automatic HTTP 429
`Retry-After` handling is intentionally outside v0.8.

## State and persistence

The typed graph state contains routing metadata: project and clip identifiers,
review mode, attempt/retry counters, original and pre-existing boundary values,
allowed-pair count, selected indexes and mapped segment identifiers/timestamps,
safe validation/provider categories, cancellation state, terminal route, and
workflow timing/version metadata.

Transcript context, complete prompts, provider objects, credentials, raw HTTP
bodies, and filesystem paths remain ephemeral runtime context. The graph is
compiled without a persistent checkpointer. This avoids creating a second
business-state database and prevents transcript or prompt data from becoming
durable graph state.

The existing application database remains authoritative for clips, evaluations,
user edits, reviewed boundaries, project/job state, and audit metadata. A valid
result is persisted only after pair, segment, range, duration, clip, and
cancellation checks pass. Invalid/provider outcomes do not overwrite original
AI boundaries, user-edited boundaries, prior valid reviewed boundaries, or
rendered artifacts.

## Human review and cancellation

The graph does not pause an Airflow task for a person. An unresolved automatic
review terminates as `manual_review`; the user can then review or edit the clip
through the existing React UI, and the application database stores that action.

Cancellation is checked before context preparation, provider invocation,
validation/application, and final persistence. It follows the explicit
`finalize_cancelled` route and is translated to the existing controlled
cancellation behavior.

## Local and Airflow integration

`ReviewAgentService.review_clip` invokes the graph once for one clip.
`review_project_clips` retains its existing batch loop, deadline, progress
events, skip-completed behavior, and per-clip isolation. Local pipeline and
Airflow review stages both call this same service.

Airflow does not map graph nodes to tasks and does not put graph state,
transcripts, or prompts into XCom. The `review_boundaries` Airflow task retains
zero retries; the single corrective attempt belongs exclusively to the graph.
When `auto_review=false`, the shared review stage skips safely without invoking
the graph.

Content Packaging, publishing metadata, AI titles/descriptions, hashtags, and
thumbnail-text generation are not part of the roadmap. After v0.8, only
optional final repository/demo hardening, browser E2E testing,
deployment/production serving, and automatic Gemini HTTP 429 `Retry-After`
handling may remain.
