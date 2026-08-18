# zö agency — RFP Intelligence Platform

AI-assisted RFP pipeline for zö agency: sync opportunities, run Go/No-Go analysis against the knowledge base, and draft proposals with evidence-backed sections.

## Repository layout

| Path | Stack | Role |
|------|-------|------|
| [`frontend/`](frontend/) | Next.js 16 | Dashboard UI — RFPs, proposals, knowledge base, pipeline |
| [`backend/`](backend/) | FastAPI | API, Supabase Postgres + Storage, LLM + Supermemory |
| [`docs/`](docs/) | — | Design specs and implementation plans |
| [`branding/`](branding/) | — | Brand assets |

## Architecture

```text
Browser → Next.js (frontend) → FastAPI (backend) → Supabase Postgres + Storage
                                              └→ Supermemory (KB search)
                                              └→ Fireworks / OpenRouter (LLM)
                                              └→ Teamwork.com (projects, tasks, time; API key on backend only)
```

- **Frontend** never holds database credentials. It calls the backend via `BACKEND_URL`.
- **Backend** is the sole writer to Supabase (Postgres for RFPs/proposals, Storage for PDFs).
- **Supermemory** powers knowledge-base retrieval for Go/No-Go and proposal generation.
- **Teamwork** is read-only from the backend. See [`docs/TEAMWORK_INTEGRATION.md`](docs/TEAMWORK_INTEGRATION.md).

## Local development

You need **two terminals** — both must be running or the dashboard will show 0 RFPs.

### 1. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in Supabase, LLM, Supermemory keys
python -m app
```

Default port is **8001** (`PORT` in `.env`). Equivalent: `uvicorn app.main:app --reload --port 8001`.

Verify: [http://127.0.0.1:8001/api/v1/health](http://127.0.0.1:8001/api/v1/health) should report `"database": "supabase"`.

### 2. Frontend

```bash
cd frontend
npm install
cp .env.example .env        # set BACKEND_URL=http://localhost:8001
npm run dev
```

Default port is **3001**. Open [http://localhost:3001](http://localhost:3001).

## Environment variables

| Service | File | Required |
|---------|------|----------|
| Backend | `backend/.env` | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, LLM keys (`FIREWORKS_API_KEY` or `OPENROUTER_API_KEY`), `SUPERMEMORY_API_KEY` |
| Frontend | `frontend/.env` | `BACKEND_URL` |
| Teamwork (optional) | `backend/.env` | `TEAMWORK_BASE_URL`, `TEAMWORK_API_KEY` |

See `.env.example` in each folder for the full list.

## Supabase setup

1. Create a Supabase project.
2. Run [`backend/supabase/schema.sql`](backend/supabase/schema.sql) in the SQL editor.
3. Create a **private** Storage bucket named `rfp-pdfs` (or set `SUPABASE_RFP_BUCKET`).
4. Add URL + service role key to `backend/.env`.

**Migrate existing SQLite data (one-off):**

```bash
cd backend
python scripts/migrate_sqlite_to_supabase.py
python scripts/migrate_pdfs_to_supabase_storage.py
```

## Deployment (Railway)

Three services:

1. **Frontend** — root: `frontend/`, env: `BACKEND_URL` only.
2. **Backend** — root: `backend/`, start: Dockerfile default (uvicorn). Env: Supabase keys, LLM keys, `CORS_ORIGINS`, QuickBooks vars including `QUICKBOOKS_CRON_SECRET`.
3. **Scheduler** — root: `backend/`, start command: `python -m app.scheduler`. Env: `SCHEDULER_BACKEND_URL` (API private URL), `QUICKBOOKS_CRON_SECRET` (same as backend), `SCHEDULER_TIMEZONE=America/Los_Angeles`. One replica. Do not enable Railway Cron Schedule on this service.

QuickBooks nightly sync fires at 11pm Pacific via APScheduler. Details: [`docs/QUICKBOOKS_INTEGRATION.md`](docs/QUICKBOOKS_INTEGRATION.md).

## Common issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| **0 RFPs** on dashboard | Backend not running | Start with `python -m app` (port 8001) |
| `backend unavailable: fetch failed` | Wrong `BACKEND_URL` or backend down | Check `frontend/.env` and backend process |
| PDF won't open | Storage bucket missing or PDF not uploaded | Run PDF migration script or re-upload |
