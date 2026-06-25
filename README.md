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
```

- **Frontend** never holds database credentials. It calls the backend via `BACKEND_URL`.
- **Backend** is the sole writer to Supabase (Postgres for RFPs/proposals, Storage for PDFs).
- **Supermemory** powers knowledge-base retrieval for Go/No-Go and proposal generation.

## Local development

You need **two terminals** — both must be running or the dashboard will show 0 RFPs.

### 1. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in Supabase, LLM, Supermemory keys
uvicorn app.main:app --reload --port 8001
```

Verify: [http://127.0.0.1:8001/api/v1/health](http://127.0.0.1:8001/api/v1/health) should report `"database": "supabase"`.

### 2. Frontend

```bash
cd frontend
npm install
cp .env.example .env        # set BACKEND_URL=http://localhost:8001
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Environment variables

| Service | File | Required |
|---------|------|----------|
| Backend | `backend/.env` | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, LLM keys (`FIREWORKS_API_KEY` or `OPENROUTER_API_KEY`), `SUPERMEMORY_API_KEY` |
| Frontend | `frontend/.env` | `BACKEND_URL` |

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

Two services:

1. **Frontend** — root: `frontend/`, env: `BACKEND_URL` only.
2. **Backend** — root: `backend/`, env: Supabase keys, LLM keys, `CORS_ORIGINS`.

Details: [`docs/superpowers/specs/2026-06-25-supabase-postgres-migration-design.md`](docs/superpowers/specs/2026-06-25-supabase-postgres-migration-design.md).

## Common issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| **0 RFPs** on dashboard | Backend not running | Start `uvicorn` on port 8001 |
| `backend unavailable: fetch failed` | Wrong `BACKEND_URL` or backend down | Check `frontend/.env` and backend process |
| PDF won't open | Storage bucket missing or PDF not uploaded | Run PDF migration script or re-upload |
