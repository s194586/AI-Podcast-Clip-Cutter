# React Frontend

`apps/web` contains the Product UI v0.5 React application. It is separate from the legacy FastAPI static UI and is intended for manual validation before any production serving switch.

## Stack

- React
- TypeScript
- Vite
- Tailwind CSS
- React Router
- lucide-react
- Vitest
- React Testing Library

## Installation

```powershell
cd apps\web
npm install
```

Dependencies are local to `apps/web`. Do not install React, Vite, or Tailwind globally.

## Startup

Start FastAPI on port `8010`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --reload --port 8010
```

Start Vite:

```powershell
.\scripts\dev_web.ps1
```

Or from the frontend directory:

```powershell
cd apps\web
npm run dev
```

`scripts/dev_full_stack.ps1` prints both commands. Run it with `-OpenWindows` only when you want two visible PowerShell windows.

## Routes

- `/` - project dashboard
- `/projects/new` - new project form
- `/projects/:projectId` - project overview and processing page
- `/projects/:projectId/editor` - clip editor
- `/projects/:projectId/exports` - rendered clips and downloads

The app shell includes project navigation, a New Project action, backend status, and the configured AI reviewer indicator.

## API Proxy

Vite development proxy forwards backend routes to:

```text
http://127.0.0.1:8010
```

The proxy covers `/health`, `/project`, `/projects`, `/clips`, `/render`, and `/source-video`. Production CORS is not changed by the React app.

## Environment Variables

The only public frontend variable is:

```text
VITE_API_BASE_URL=http://127.0.0.1:8010
```

Use `apps/web/.env.example` as the frontend template. Do not put backend secrets, Gemini credentials, database URLs, local filesystem paths, or SQLite paths in any `VITE_*` variable.

## API Usage

The typed API client lives in `apps/web/src/api`.

- `client.ts` builds URLs from `VITE_API_BASE_URL`, parses JSON, supports blob responses, and accepts `AbortSignal`.
- `errors.ts` converts backend validation errors into controlled messages.
- `projects.ts`, `clips.ts`, `review.ts`, `render.ts`, and `health.ts` wrap project-scoped endpoints.
- All clip operations use `/projects/{project_id}/...` endpoints. The React app does not use global `GET /clips`.
- Browser code never calls Gemini directly. Review actions call the FastAPI review endpoints.

## Tests

```powershell
cd apps\web
npm run test -- --run
```

Tests mock FastAPI with `fetch` and do not call YouTube, Gemini, Whisper, or FFmpeg.

## Build

```powershell
cd apps\web
npm run lint
npm run build
```

Build output goes to `apps/web/dist/` and is ignored by Git.

## Legacy Frontend Fallback

`apps/api/static` remains the FastAPI-served fallback UI. It is still available from the FastAPI root route:

```text
http://127.0.0.1:8010/
```

The React app is run separately through Vite until production integration is manually validated.
