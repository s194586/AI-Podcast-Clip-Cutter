# Frontend Redesign Plan

This is a plan only. The current static frontend in `apps/api/static` should remain working until the new app reaches feature parity.

## Product UI v0.5 Status

`apps/web` now contains the first React Product UI implementation. It covers the project dashboard, new project form, processing overview, clip editor, and exports page against project-scoped FastAPI endpoints. The legacy static frontend remains unchanged and is still the FastAPI fallback.

Production integration is intentionally deferred until manual validation is complete. The React app runs through Vite and uses the development proxy documented in [REACT_FRONTEND.md](REACT_FRONTEND.md).

## Proposed Stack

- `apps/web`
- React
- TypeScript
- Vite
- Tailwind CSS
- optional `shadcn/ui` for accessible primitives
- FastAPI remains the backend in `apps/api`

The frontend should call the existing project-specific FastAPI endpoints first, adding new endpoints only where the product workflow genuinely needs them.

## Product Areas

### Project Dashboard

Show local projects with title/source, processing status, clip counts, review progress, render progress, and latest failure state. Keep operational density high; this is a workbench, not a marketing page.

### New Project Wizard

Guide users through source URL or local media selection, content mode defaults, transcription settings, AI mode, and output expectations. Advanced options should be available but not visually dominant.

### Processing Progress Stepper

Represent deterministic stages clearly:

```text
download -> transcribe -> score candidates -> import SQLite -> AI review -> ready
```

Technical logs should be hidden behind a details drawer so normal users see stage state first and raw logs only when needed.

### Clip Editor

Keep the current core workflow:

- source video preview
- candidate list
- transcript-aware boundary display
- start/end sliders
- edited duration validation
- accept/reject controls
- render action
- rendered output status

The editor should make AI-reviewed boundaries visible without hiding manual corrections. `ai_*`, `reviewed_*`, and `edited_*` state should remain conceptually distinct.

### AI Review Panel

Expose single-clip and batch review states:

- provider/model
- decision
- selected option indexes and derived segment IDs when useful
- reasoning summary
- start/end reasons
- warnings
- retry/failure state

Default view should be editorial. Technical validation metadata can live in a collapsible details drawer.

### Exports And Downloads

Add a focused page for rendered clips with status, output paths, download links, and render metadata. Keep failed renders easy to inspect and retry.

## Responsive Layout

Desktop should prioritize efficient review: project/clip navigation, video, transcript context, and actions visible with minimal mode switching.

Mobile should remain usable for status checks and light review, but detailed trimming can prioritize tablet/desktop ergonomics.

## Migration Strategy

1. Keep `apps/api/static` as the default served editor.
2. Add `apps/web` behind a separate dev command and optional FastAPI static mount.
3. Build read-only project dashboard against existing APIs.
4. Add clip list and clip detail views.
5. Add manual boundary editing and render actions.
6. Add Gemini single and batch review controls.
7. Add exports/download page.
8. Run both frontends during parity testing. Product UI v0.5 is ready for this step.
9. Switch the default FastAPI mount only after the React app covers the current static editor workflows and has passed manual Project 3 validation.
10. Remove the static frontend in a later cleanup after a tagged checkpoint.

Do not change the backend boundary lifecycle to fit frontend state. The frontend should reflect the existing contract.
