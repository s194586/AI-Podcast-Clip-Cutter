# Engineering Decisions

These concise ADR-style entries describe the v1.0.0 portfolio MVP.

## 1. Deterministic candidate generation

**Context:** Whole-source model ranking would be expensive, difficult to
reproduce, and hard to test offline.

**Decision:** Generate and score candidate windows locally from transcript
features.

**Consequences:** Candidate selection is reproducible and testable, but quality
still depends on source audio, transcription, and scoring features.

## 2. Gemini selects semantic boundaries, not arbitrary timestamps

**Context:** A model is useful for semantic completeness but unsafe as the sole
authority for media timestamps.

**Decision:** Gemini selects numbered start/end options derived from transcript
segments.

**Consequences:** Semantic judgment remains model-driven while the valid action
space stays bounded.

## 3. `allowed_boundary_pairs`

**Context:** Individually valid start/end options can form an invalid duration
or ordering when combined.

**Decision:** Precompute valid pairs that satisfy ordering, duration, and range constraints.

**Consequences:** The provider has less freedom but cannot legitimately select
a pair the editor cannot accept.

## 4. Authoritative backend validation

**Context:** Structured schemas validate shape, not all domain invariants.

**Decision:** Re-map indexes to real segments and revalidate pair membership,
timestamps, ranges, duration, clip identity, and cancellation before persistence.

**Consequences:** A model response is never treated as success solely because it parsed.

## 5. One corrective retry

**Context:** Structured/domain-invalid output may be repairable, while
uncontrolled retries increase latency and quota use.

**Decision:** Permit exactly one concise corrective attempt.

**Consequences:** Each clip makes at most two provider calls and then terminates safely.

## 6. No automatic HTTP 429 retry in v1.0

**Context:** Quota windows and provider `Retry-After` behavior are external and
may exceed a user request or Airflow task budget.

**Decision:** Classify HTTP 429 as a provider failure without automatic waiting.

**Consequences:** The user can retry explicitly; automated `Retry-After`
handling remains optional.

## 7. One LangGraph invocation per clip

**Context:** A batch-wide graph would couple unrelated clip outcomes and weaken
per-clip progress and failure isolation.

**Decision:** Invoke a deterministic graph independently for each clip.

**Consequences:** One failed/manual clip does not prevent later clips from running.

## 8. No persistent LangGraph checkpointer

**Context:** The application database already owns business state, and graph
checkpointing could durably store transcript/prompt context.

**Decision:** Compile the graph without a persistent checkpointer.

**Consequences:** There is no second business-state database; graph execution
restarts per clip when explicitly retried.

## 9. Manual review terminates the graph

**Context:** Waiting for a human inside an Airflow task would occupy execution
capacity indefinitely.

**Decision:** End unresolved automatic review as `manual_review`.

**Consequences:** React and application SQLite handle later human action without
holding an orchestration task open.

## 10. Retry ownership is separated

**Context:** Nested Airflow and provider retries can multiply calls.

**Decision:** Airflow owns bounded deterministic-stage retries; LangGraph owns
the single corrective review retry.

**Consequences:** Retry budgets remain understandable and testable.

## 11. Airflow review retries are zero

**Context:** Re-running the complete review task can repeat successful model calls
for multiple clips after one provider failure.

**Decision:** Configure `review_boundaries` with zero Airflow retries.

**Consequences:** Airflow cannot create a Gemini retry storm; users retain explicit retry control.

## 12. Shared `PipelineStageExecutor`

**Context:** Separate local and Airflow implementations would drift.

**Decision:** Both modes call the same stage registry and executor.

**Consequences:** Domain behavior and lifecycle checks are tested once, while
orchestrators focus on process/scheduler concerns.

## 13. Separate application SQLite and Airflow PostgreSQL

**Context:** Product state and scheduler metadata have different ownership.

**Decision:** Keep projects, clips, jobs, artifacts, and reviews in SQLite;
reserve PostgreSQL for Airflow metadata.

**Consequences:** The local MVP remains simple, but SQLite is not presented as a
horizontally scaled production database.

## 14. FastAPI runs in Docker for Airflow mode

**Context:** The API must share the same application data mapping and stable
network access to Airflow.

**Decision:** Run FastAPI as `app-api` in the Compose stack.

**Consequences:** Container paths and networking are consistent; the host API
must be stopped before using the shared SQLite file.

## 15. Project-relative workspace paths

**Context:** Host and container absolute paths differ and can expose local details.

**Decision:** Persist and validate project-relative paths against fixed roots.

**Consequences:** Traversal/absolute paths are rejected and run configuration is portable.

## 16. Local mode remains available

**Context:** Airflow adds operational value but increases setup cost.

**Decision:** Keep `LocalPipelineOrchestrator` as the default.

**Consequences:** Contributors can use a simpler development path while the same
stages remain available under Airflow.

## 17. Content Packaging is excluded

**Context:** Titles, descriptions, hashtags, thumbnail text, and publishing
metadata would expand the product beyond clip preparation and review.

**Decision:** Exclude Content Packaging from the roadmap.

**Consequences:** The repository stays focused on a coherent, validated clipping workflow.

## 18. Stop at a portfolio-ready MVP

**Context:** Additional agents and unrelated features would increase surface
area without strengthening the core engineering story.

**Decision:** Treat v1.0.0 as complete for the portfolio scope.

**Consequences:** Deployment, browser E2E tests, demo assets, and automated HTTP
429 handling remain clearly optional rather than unfinished core functionality.
