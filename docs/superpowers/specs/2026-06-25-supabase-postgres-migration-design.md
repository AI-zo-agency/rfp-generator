# Supabase Postgres Migration — Design Spec

**Date:** 2026-06-25  
**Status:** Implemented (2026-06-25)  
**Deployment:** Railway — two services (Next.js dashboard + FastAPI backend)

## Goal

Remove SQLite (`rfp-dashboard/data/rfps.db`) entirely. All structured data lives in **Supabase Postgres**; PDF binaries stay in **Supabase Storage** (`rfp-pdfs` bucket, already implemented). Supermemory remains the knowledge-base search layer (unchanged).

## Architecture

```text
┌─────────────────────┐         ┌─────────────────────┐
│  Railway: Frontend  │  HTTP   │  Railway: Backend   │
│  Next.js (3000)     │ ──────► │  FastAPI (8001)     │
│  No DB credentials  │         │  Service role key   │
└─────────────────────┘         └──────────┬──────────┘
                                           │
                    ┌──────────────────────┼──────────────────────┐
                    ▼                      ▼                      ▼
            Supabase Postgres      Supabase Storage         Supermemory
            (rfps, proposals,      (PDF files)              (KB search)
             sync_jobs)
```

### Access rules

| Layer | Postgres | Storage | Notes |
|-------|----------|---------|-------|
| FastAPI | ✅ service role | ✅ service role | Sole writer |
| Next.js | ❌ never | ❌ never | Proxies via `BACKEND_URL` only |
| JustWin sync CLI | ❌ | ❌ | Calls FastAPI upsert + PDF upload endpoints |

### Railway env

**Backend service**

```env
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_RFP_BUCKET=rfp-pdfs
CORS_ORIGINS=https://<frontend>.up.railway.app
# existing: SUPERMEMORY_*, OPENROUTER_*, FIREWORKS_*, etc.
```

**Frontend service**

```env
BACKEND_URL=https://<backend>.up.railway.app
# Remove: DATABASE_PATH, PDF_STORAGE_PATH
```

Use Railway private networking for `BACKEND_URL` if both services are in the same project (faster, no public hop).

## Postgres schema

Run once in Supabase SQL editor (snake_case columns; JSON stored as `jsonb`).

```sql
-- rfps
CREATE TABLE rfps (
  id TEXT PRIMARY KEY,
  external_id TEXT UNIQUE,
  title TEXT NOT NULL,
  client TEXT,
  source TEXT DEFAULT 'justwin',
  sector TEXT,
  location TEXT,
  due_date TEXT,
  received_date TEXT,
  stage TEXT DEFAULT 'intake',
  status TEXT DEFAULT 'new',
  priority TEXT DEFAULT 'medium',
  fit_score INTEGER,
  worth_score INTEGER,
  go_no_go TEXT,
  assigned_to TEXT,
  estimated_value INTEGER,
  page_limit INTEGER,
  last_activity TIMESTAMPTZ,
  last_activity_note TEXT,
  contract_role TEXT DEFAULT 'prime',
  description TEXT,
  justwin_tab TEXT,
  pdf_path TEXT,
  justwin_detail_url TEXT,
  synced_at TIMESTAMPTZ,
  go_no_go_analysis JSONB
);

CREATE INDEX idx_rfps_status ON rfps(status);
CREATE INDEX idx_rfps_due_date ON rfps(due_date);

-- proposal cache (large JSON blobs)
CREATE TABLE proposal_research (
  rfp_id TEXT PRIMARY KEY REFERENCES rfps(id) ON DELETE CASCADE,
  payload JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE proposal_drafts (
  rfp_id TEXT PRIMARY KEY REFERENCES rfps(id) ON DELETE CASCADE,
  payload JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- JustWin sync job tracking
CREATE TABLE sync_jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  rfps_found INTEGER DEFAULT 0,
  pdfs_downloaded INTEGER DEFAULT 0,
  error TEXT
);
```

No RLS policies required initially — only the backend service role touches Postgres. RLS can be added later if browser clients ever talk to Supabase directly (not planned).

## Backend changes

### New module: `app/services/supabase_db.py`

Thin wrapper around `supabase-py` for CRUD:

- `fetch_all_rfps()`, `fetch_rfp(id)`, `insert_rfp()`, `upsert_rfp()`, `update_rfp_fields()`, `delete_rfp()`
- `get/save_research_cache`, `get/save_proposal_draft`
- `create/finish/get sync_jobs`

Replace `sqlite3` usage in:

- `rfp_repository.py`
- `proposal_repository.py`

Keep the same public function signatures so API routes and services need minimal changes.

### New API endpoints (gaps vs current Next.js `db.ts`)

| Endpoint | Purpose |
|----------|---------|
| `PUT /api/v1/rfps/upsert` | JustWin sync bulk upsert (by `external_id`) |
| `GET /api/v1/sync-jobs/latest` | Dashboard sync status |
| `GET /api/v1/sync-jobs/running` | Prevent duplicate sync |
| `POST /api/v1/sync-jobs` | Start job |
| `PATCH /api/v1/sync-jobs/{id}` | Finish job |

Existing endpoints already cover dashboard list (`GET /rfps/dashboard`), manual create, analyze, go, PDF, delete.

### Config cleanup

- Remove `database_path` from `config.py` (or keep only for one-off migration script)
- `supabase_url` + `supabase_service_role_key` required in production

### Migration script

`backend/scripts/migrate_sqlite_to_supabase.py`:

1. Read `rfps.db` with sqlite3 (read-only)
2. Insert rows into Supabase tables in order: `rfps` → `proposal_*` → `sync_jobs`
3. Idempotent (`ON CONFLICT` upsert)
4. Report counts + any failures

## Frontend changes

### Remove

- `better-sqlite3` dependency
- `src/lib/db.ts`
- `DATABASE_PATH`, `PDF_STORAGE_PATH` from `.env`
- `serverExternalPackages: ["better-sqlite3"]` from `next.config.ts`

### Update

| File | Change |
|------|--------|
| `src/lib/rfp-service.ts` | `fetch(`${BACKEND_URL}/api/v1/rfps/dashboard`)` |
| `src/app/api/rfps/route.ts` | Proxy POST to backend (or call backend only, drop local insert) |
| `src/app/api/rfps/[id]/go/route.ts` | Proxy to `POST /api/v1/rfps/{id}/go` |
| `src/lib/justwin-sync-runner.ts` | Sync job APIs via backend |
| `scripts/justwin-sync/index.ts` | `upsert` + PDF upload via `BACKEND_URL` env |

### Fallback behavior

- If backend unreachable: show error banner, not mock data (production)
- Mock data only when `NODE_ENV=development` and explicit flag (optional)

## JustWin sync on Railway

Playwright sync is currently disabled. When re-enabled:

- Run sync as a **Railway cron job or one-off worker** on the backend service (has Playwright deps), not on the lightweight frontend container
- Frontend triggers sync via `POST /api/v1/sync-jobs` → backend spawns worker or queues job

Defer full Playwright-on-Railway setup until sync is re-enabled; schema + API stubs ship with this migration.

## Rollout plan

1. Create Supabase tables (SQL above)
2. Implement `supabase_db.py` + swap repositories (feature-flag: `USE_SUPABASE_DB=1`)
3. Run migration script against prod Supabase
4. Update Next.js to backend-only data access
5. Remove SQLite files and deps
6. Deploy backend first, then frontend (backend must be live before frontend cutover)

## Out of scope

- Moving Supermemory KB to Supabase
- Auth / multi-tenant RLS
- Real-time subscriptions (not needed today)

## Success criteria

- No `rfps.db` or `better-sqlite3` in repo
- Dashboard, Go/No-Go, proposals, PDF view/upload work on Railway with two services
- Existing local SQLite data migrated without loss
