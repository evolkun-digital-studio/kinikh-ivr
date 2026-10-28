# Kinikh IVR — Admin Dashboard

React + TypeScript + Vite + Tailwind CSS admin dashboard for the Kinikh IVR
backend (`../app`). Talks to the existing FastAPI backend over HTTP — it does
not duplicate any backend logic, models, or auth.

## Run locally

```bash
cd dashboard
npm install
cp .env.example .env   # set VITE_API_BASE_URL if the backend isn't on :8000
npm run dev            # http://localhost:3000
```

The backend must be running separately (`uvicorn app.main:app --port 8000`
from the repo root) with `CORS_ORIGINS` including `http://localhost:3000`
(already the default in `.env.example`).

## Scripts

- `npm run dev` — Vite dev server with HMR.
- `npm run build` — type-checks (`tsc -b`) then produces a production build in `dist/`.
- `npm run preview` — serves the production build locally.
- `npm run typecheck` — type-check only, no emit.
- `npm run lint` — ESLint.

## Environment variables

Only `VITE_API_BASE_URL` is used. Vite inlines every `VITE_*` variable into
the client bundle at build time — **never** put API keys, database
credentials, or any other secret in a `VITE_*` variable or in this directory.
All secrets stay server-side in the FastAPI backend's `.env`.

## Structure

```
src/
  app/            App shell composition: providers, router
  components/     Design-system primitives (cards, tables, charts, badges,
                  modals, common) + layout/navigation
  pages/          One file per route
  services/       Typed API client + one module per backend resource
  hooks/          useAuth, useDebounce
  stores/         Zustand auth store (access/refresh tokens)
  types/          TypeScript types mirroring the backend's Pydantic schemas
  utils/          Formatting helpers
```

See `../docs/dashboard_architecture.md` for the full architecture writeup and
`../docs/dashboard_testing.md` for how this was tested.
