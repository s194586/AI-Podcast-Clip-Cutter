# Portfolio Overview

## 30-second recruiter summary

AI Podcast Clip Cutter is a portfolio-ready, end-to-end podcast clipping MVP.
It combines a deterministic media pipeline with FastAPI, React, SQLite,
semantic Gemini boundary review, per-clip LangGraph routing, and optional
Apache Airflow orchestration. The project emphasizes clear responsibility
boundaries, controlled failure behavior, reusable stages, and offline-testable
AI integration.

## Technical summary

The application creates isolated projects, transcribes source media, validates
the transcript, scores candidate windows locally, imports stable clips into
SQLite, and optionally reviews clip boundaries through Gemini. Gemini chooses
numbered transcript-segment pairs rather than arbitrary timestamps. LangGraph
routes the existing review through validation, one possible corrective attempt,
and controlled terminal outcomes. Local subprocess and Airflow modes reuse the
same `PipelineStageExecutor`; the state-aware React flow moves from Projects and
Processing through Review/Edit, human-triggered Render, and grouped Exports
without receiving secrets or internal paths. The renderer produces 1080x1920
outputs, combines stable face tracking with a blurred full-frame fallback, and
builds deterministic subtitle cues from word timestamps when available without
using an LLM to rewrite recognized speech.

## Main engineering challenges

- Separating reproducible candidate scoring from semantic model judgment.
- Constraining model output to backend-generated, duration-safe boundary pairs.
- Preserving prior reviewed and user-edited state across retries and failures.
- Sharing real stage logic between local subprocess and Airflow execution.
- Preventing nested provider and scheduler retries from creating retry storms.
- Keeping prompts, transcripts, credentials, and paths out of durable orchestration metadata.
- Maintaining useful project progress across process and container boundaries.
- Keeping a speaker visible when face detection is temporarily or persistently unavailable.
- Producing readable subtitle cues without changing the words recognized by Faster-Whisper.

## Decisions and trade-offs

- SQLite keeps the local MVP simple, while Airflow PostgreSQL remains scheduler-only.
- Local orchestration remains the default; Airflow adds visibility and scheduling at greater operational cost.
- LangGraph runs once per clip without a checkpointer, favoring isolation and data minimization.
- The model selects only from allowed pairs, reducing flexibility in exchange for authoritative safety.
- Subtitle formatting may adjust spacing, capitalization, punctuation, and cue layout, but not recognized words or meaning.
- Dynamic tracking holds the last stable crop only briefly before switching to a blurred full-frame composition.
- Rendering stays human-triggered, preserving editorial control.

See [Engineering Decisions](ENGINEERING_DECISIONS.md) for the ADR-style rationale.

## Strongest portfolio aspects

- A real AI boundary with mocked, deterministic offline tests.
- Explicit distinction between model judgment and backend authority.
- Shared domain services across CLI, local product flow, API, and Airflow.
- Controlled cancellation, timeout, provider-failure, and manual-review paths.
- Dockerized Airflow 3.3.0 integration with parse and real-smoke evidence.
- Recruiter-visible, state-aware React product flow backed by typed FastAPI contracts.
- Human-controlled 1080x1920 rendering with safe crop fallback, deterministic captions, and grouped export history.

## Implementation scope

The project author implemented the application architecture, orchestration
adapters, review workflow, persistence, API/UI integration, tests, Docker/Airflow
configuration, and documentation around third-party components. The repository
uses existing tools and models including Faster-Whisper, Gemini, LangGraph,
Airflow, React, and FFmpeg; it does not claim those dependencies were written
from scratch or that a custom language model was trained.

## Role relevance

For AI Engineer, ML Engineer, and backend-oriented roles, the project demonstrates:

- integrating an external model behind typed, testable provider contracts;
- designing deterministic preprocessing around probabilistic model behavior;
- validating and persisting structured model output safely;
- orchestrating long-running work across local and scheduled execution modes;
- building observable, human-in-the-loop failure and editing paths.

## CV bullets

- Built a FastAPI/React podcast clipping MVP with deterministic candidate scoring, SQLite project state, and human-controlled rendering.
- Integrated Gemini semantic boundary selection through a typed LangGraph workflow with authoritative validation, one bounded corrective retry, and offline provider-mocked tests.
- Implemented reusable pipeline stages shared by local subprocess and Apache Airflow 3.3.0 orchestration, validated through a 305-test Python suite (1 optional skip), 52 React tests, Docker/DAG checks, and controlled smoke tests.

## Interview talking points

1. Why the model selects an allowlisted pair instead of emitting timestamps.
2. How local and Airflow orchestrators share stage logic without duplicating business rules.
3. Why LangGraph is per clip and has no persistent checkpointer.
4. How retry ownership is divided between Airflow and the review workflow.
5. How cancellation and failed review preserve user-edited boundaries.

## Likely interview questions

### Why not ask Gemini to find and cut every viral moment?

Candidate discovery remains deterministic, reproducible, and inexpensive.
Gemini is used only where semantic judgment is most valuable: choosing complete
openings and endings from valid transcript boundaries. The system makes no
guarantee of virality.

### What does LangGraph add?

It makes the existing per-clip review states and conditional routes explicit:
context preparation, provider call, validation, one corrective route, apply,
manual review, provider failure, and cancellation. It does not introduce extra
agents or replace domain validation.

### What does Airflow own?

Airflow schedules the eight pipeline stages, records scheduler metadata, and
supports bounded deterministic-stage retries. It does not own semantic
decisions, graph-node execution, or automatic Gemini retry storms.

### How is model output made safe?

Gemini returns strict integer indexes. The backend maps them to real transcript
segments, verifies the pair allowlist, ordering, duration, authoritative ranges,
clip identity, and cancellation state before persistence.

### What would change for production deployment?

Likely work includes production serving, authentication/authorization, an
application database suited to horizontal scaling, object storage, formal
monitoring, deployment automation, browser E2E coverage, and an explicit quota
policy. Faster-Whisper lexical errors may remain, and the product has no built-in
subtitle text editor. None of those production extensions are claimed as
implemented in v1.1.0.

## Responsibility boundaries

| Component | Responsibility |
|---|---|
| Deterministic candidate generation | Reproducible local transcript scoring and candidate windows |
| Gemini | Semantic choice among allowed boundary pairs |
| LangGraph | Per-clip workflow routing and bounded corrective attempt |
| Airflow | Pipeline-stage scheduling and operational visibility |
| Backend validation | Final authority before reviewed state is persisted |
| Subtitle formatter | Deterministic cue layout and punctuation without rewriting recognized words |
| Renderer | Stable face tracking, bounded loss handling, and safe 1080x1920 composition |
| Human editor | Final edits, acceptance/rejection, and render trigger |
