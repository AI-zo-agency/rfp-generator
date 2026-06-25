# zö agency — Frontend

Next.js dashboard for RFP intake, Go/No-Go review, proposal drafting, knowledge base, and pipeline tracking.

## Prerequisites

- Node.js 20+
- **FastAPI backend running** on port 8001 (see [root README](../README.md))

The frontend does not connect to Supabase directly. All data flows through the backend API.

## Setup

```bash
npm install
cp .env.example .env
```

Minimum `frontend/.env`:

```env
BACKEND_URL=http://localhost:8001
```

## Development

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

If RFPs show as **0 opportunities**, the backend is likely stopped. Start it:

```bash
cd ../backend && uvicorn app.main:app --reload --port 8001
```

## Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Dev server (Turbopack) |
| `npm run build` | Production build |
| `npm run start` | Serve production build |
| `npm run lint` | ESLint |

## App structure

```text
src/app/(dashboard)/   # Dashboard, RFPs, Proposals, Pipeline, Analytics, KB
src/app/api/           # Next.js API routes (proxy to FastAPI)
src/components/        # UI components
src/lib/               # API clients (backend-api, rfp-service, proposal-api)
```

## Key routes

| Page | Path |
|------|------|
| Dashboard | `/` |
| Active RFPs | `/rfps` |
| RFP detail + Go/No-Go | `/rfps/[id]` |
| Proposal workspace | `/proposals?rfp=[id]` |
| Knowledge base | `/knowledge-base` |
| Pipeline | `/pipeline` |

## Environment reference

| Variable | Default | Notes |
|----------|---------|-------|
| `BACKEND_URL` | `http://localhost:8001` | FastAPI base URL (no trailing slash) |
| `USE_MOCK_RFPS` | — | Set `true` in dev to show mock data when backend is down |

Supermemory, Supabase, and LLM keys belong in **`backend/.env`** only.

## JustWin sync

JustWin Playwright sync is **disabled in the UI** (`src/lib/justwin-config.ts`). Add RFPs manually or via the backend API. Sync scripts live under `scripts/justwin-sync/` for future re-enablement.

## Deploy

Deploy as a standalone Next.js app (e.g. Railway). Set:

```env
BACKEND_URL=https://your-backend.railway.app
```

Do not add Supabase or database credentials to the frontend service.
