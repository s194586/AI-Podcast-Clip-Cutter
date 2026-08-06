# AI Podcast Clip Cutter Web

React editor component for the local portfolio MVP. Product scope and public
status live in the [root README](../../README.md).

```powershell
npm install
npm run dev
```

Run FastAPI separately on `http://127.0.0.1:8010`. The Vite dev proxy forwards project, clip, review, render, source-video, health, and export routes to that backend.

Only `VITE_API_BASE_URL` belongs in frontend env files. Do not add backend secrets or Gemini credentials to `VITE_*` variables.

Run `npm test`, `npm run lint`, and `npm run build` from this directory.
