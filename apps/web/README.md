# Podcast Shorts Cutter Web

React Product UI v0.5 for the local FastAPI project flow.

```powershell
npm install
npm run dev
```

Run FastAPI separately on `http://127.0.0.1:8010`. The Vite dev proxy forwards project, clip, review, render, source-video, health, and export routes to that backend.

Only `VITE_API_BASE_URL` belongs in frontend env files. Do not add backend secrets or Gemini credentials to `VITE_*` variables.

See `../../docs/REACT_FRONTEND.md` for routes, tests, build, and legacy frontend fallback details.
