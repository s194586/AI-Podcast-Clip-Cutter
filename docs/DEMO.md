# Demo Guide

This guide describes a safe two-to-three-minute portfolio demo. Record only
with disposable or intentionally prepared demo data. Do not expose API keys,
Airflow credentials, local usernames, absolute paths, complete prompts, or
complete transcripts.

No verified screenshots are currently tracked. Capture real images later; do
not add placeholder image links to the README.

## Recording sequence and narration

| Time | View | Suggested narration |
|---|---|---|
| 0:00-0:20 | React dashboard | “AI Podcast Clip Cutter turns a long podcast into editable short-form candidates through a modular, observable pipeline.” |
| 0:20-0:40 | New-project form | “A project records the source, review settings, and orchestrator selection. Local and Airflow modes share the same stage implementations.” |
| 0:40-1:00 | Processing view | “The UI reads persisted job and stage progress from FastAPI; heavy work stays outside the web request.” |
| 1:00-1:25 | Airflow DAG grid/graph | “Airflow schedules eight sequential pipeline tasks. The review task has zero Airflow retries to avoid provider retry storms.” |
| 1:25-1:55 | Clip editor | “Deterministic scoring proposes candidates. Gemini selects semantically complete boundaries only from allowed transcript pairs, and the backend validates them.” |
| 1:55-2:15 | Gemini suggestion and edited handles | “LangGraph routes valid, corrective, provider-failure, cancellation, and manual-review outcomes. User edits remain authoritative.” |
| 2:15-2:35 | Manual-review state | “An unresolved review terminates safely for a person instead of holding an Airflow task open.” |
| 2:35-2:55 | Exports view | “Rendering remains human-triggered. Show an export only if it is an existing, safe demo artifact.” |

## Before recording

- Use a disposable project and source you are authorized to show.
- Confirm the browser contains no personal bookmarks, account details, or unrelated tabs.
- Hide `.env`, `.env.airflow`, terminals containing credentials, and Airflow auth setup.
- Use neutral project titles and remove personal paths from visible logs.
- Confirm transcript excerpts and media are suitable for public display.
- Preload the exact pages needed; avoid waiting through a real transcription in the recording.
- Do not make a real Gemini call solely for the demo if a safe, previously prepared result is available.
- Never imply the project is cloud deployed or has browser E2E coverage.

## Screenshots worth capturing

| View | Recommended filename under `docs/assets/` |
|---|---|
| Dashboard with safe project cards | `dashboard.png` |
| Project creation form | `new-project.png` |
| Processing progress | `processing.png` |
| Airflow eight-task DAG | `airflow-dag.png` |
| Clip editor with reviewed boundaries | `clip-editor.png` |
| Manual-review fallback | `manual-review.png` |
| Exports page with a safe artifact | `exports.png` |

Before committing any image, inspect it for API keys, passwords, local usernames,
absolute Windows/container paths, source URLs that should remain private, and
personal identifiers.

## README placement

After real screenshots exist, place one dashboard/editor image after the README
“Demo and preview” section and link the remaining images from this guide. Keep
the architecture diagrams as Mermaid so they remain text-reviewable and render
directly on GitHub.
