# Clip Review Agent

The Clip Review Agent is a small PoC for reviewing one generated podcast clip candidate at a time.

It is separate from the deterministic media pipeline. The pipeline downloads media, transcribes, generates candidates, scores them, prepares editor state, and renders only when a human asks it to. The agent does not orchestrate that pipeline.

## Purpose

The agent gives practical advice to the human editor:

- keep, reject, adjust boundaries, extend context, render ready, or manual review,
- whether more transcript context is needed,
- whether the start or end feels cut too tightly,
- whether the clip works as a standalone short,
- whether obvious private or sensitive patterns appear,
- whether a speaker-focused or wider crop is safer.

## Workflow

The workflow lives in `apps/review_agent/graph.py`.

```text
load_candidate
-> retrieve_context
-> evaluate_quality
-> route_context_decision
-> retrieve_more_context at most once
-> check_privacy
-> suggest_boundaries
-> suggest_crop
-> final_recommendation
-> save_evaluation
```

LangGraph is used when installed. If it is unavailable, the same bounded workflow runs through a local runner so tests and local-only development do not require optional graph dependencies to be installed first.

## Tools

The deterministic tools live in `apps/review_agent/tools.py`.

- `get_transcript_context` slices transcript segments around the clip.
- `get_candidate_features` returns stored score, rank, reasons, boundaries, and features.
- `check_sensitive_patterns` detects obvious email, phone, PESEL-like, credit-card-like, address-like, and sensitive keyword patterns.
- `suggest_boundaries` gives heuristic start/end advice.
- `suggest_crop_advice` returns advisory crop metadata only.
- `save_evaluation` persists a structured SQLite result.

The sensitive-pattern checker is advisory. It is not a legal compliance engine and should not overblock normal podcast conversation.

## Modes

Default mode:

```powershell
$env:CLIP_REVIEW_MODE = "local_only"
```

Optional mode:

```powershell
$env:CLIP_REVIEW_MODE = "llm_optional"
$env:OPENAI_API_KEY = "..."
$env:CLIP_REVIEW_MODEL = "gpt-4.1-mini"
```

If the optional provider client, API key, or model call is unavailable, the service falls back to deterministic local evaluation.

## API

```text
POST /clips/{clip_id}/review
GET  /clips/{clip_id}/review
POST /projects/{project_id}/clips/{clip_id}/review
GET  /projects/{project_id}/clips/{clip_id}/review
```

The POST endpoint loads the clip from SQLite, loads transcript context from the project transcript path or `transcripts/final_transcript.json`, runs the review workflow, saves a `ClipEvaluation`, and returns structured JSON.

## Persistence

Review results are saved in `clip_evaluations`.

Key fields include:

- decision,
- recommended action,
- quality/context/hook/payoff/boundary scores,
- privacy risk,
- suggested start/end,
- crop advice,
- reasons,
- warnings,
- raw structured metadata.

The latest saved evaluation is returned by the GET endpoints.

## Human In The Loop

The review agent produces advice, not final editorial truth. Human review remains required before rendering final shorts.
