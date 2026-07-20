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
| 0:00-0:20 | Projects | “AI Podcast Clip Cutter turns long conversations into reviewable short-form projects and exposes the next valid action for each project state.” |
| 0:20-0:40 | New Project | “A project records the source, review setting, and local or Airflow orchestrator choice. Both modes reuse the same pipeline services.” |
| 0:40-1:00 | Processing | “The compact processing view polls persisted FastAPI state, shows the active stage, and changes its primary action when the project is ready.” |
| 1:00-1:35 | Review/Edit in the Editor | “Deterministic local scoring proposes candidates. Gemini reviews only semantic start and end boundaries through LangGraph, while backend validation remains authoritative.” |
| 1:35-2:00 | Boundary preview and state-aware action | “The user previews and edits exact boundaries, accepts or rejects the clip, and explicitly triggers rendering. AI review never rewrites the quoted transcript.” |
| 2:00-2:25 | Prepared 1080x1920 render | “Stable detections use smoothed face tracking. If detection is unavailable, the dynamic renderer moves to the full source on a blurred background. Captions are formatted deterministically from transcript timestamps.” |
| 2:25-2:55 | Exports | “The latest render is foregrounded, previous attempts stay collapsed as history, and Raw and With subtitles are grouped as variants of the same clip.” |

If an Airflow proof is useful for the audience, show the eight-task DAG only as
a brief optional cutaway. Do not interrupt the main product flow or start a new
DagRun solely for the recording.

## Before recording

- Use a disposable project and source you are authorized to show.
- Confirm the browser contains no personal bookmarks, account details, or unrelated tabs.
- Hide `.env`, `.env.airflow`, terminals containing credentials, and Airflow auth setup.
- Use neutral project titles and remove personal paths from visible logs.
- Confirm transcript excerpts and media are suitable for public display.
- Confirm the prepared export shows the intended face-tracking fallback and readable subtitles before recording.
- Confirm the Exports view selects the intended Raw or With subtitles variant and does not expose unsafe filenames.
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
