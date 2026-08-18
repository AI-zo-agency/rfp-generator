# Teamwork.com Integration

Teamwork now follows the same backend-managed mirror pattern as QuickBooks: the frontend reads only our API, the backend syncs Teamwork into Supabase on a schedule, and the cached dashboard payload is served from our own database.

Official docs: [Teamwork API](https://apidocs.teamwork.com/) · [Authentication](https://apidocs.teamwork.com/guides/teamwork/authentication) · [Paging](https://apidocs.teamwork.com/guides/teamwork/how-does-paging-work) · [Rate limits](https://apidocs.teamwork.com/guides/teamwork/rate-limit)

## Scope

Persisted Teamwork entities in this pass:

- projects
- tasks (`overdue` and `within14` operational slices)
- people
- time entries
- milestones

The browser never talks to Teamwork directly and never receives the API key.

## Environment

Set these on the backend only:

```env
TEAMWORK_BASE_URL=https://zoagency.teamwork.com
TEAMWORK_API_KEY=
QUICKBOOKS_CRON_SECRET=
```

`TEAMWORK_API_KEY` stays server-side. The scheduler reuses the shared cron secret when posting to `/api/v1/financials/teamwork/sync`.

## Architecture

```text
APScheduler
  -> POST /api/v1/financials/teamwork/sync
  -> teamwork_sync.py
  -> Teamwork V3 API
  -> teamwork_* mirror tables in Supabase
  -> teamwork_panel_cache
  -> GET /api/v1/financials/teamwork/overview
  -> Financial UI
```

Key modules:

- `backend/app/financial/teamwork/client.py`: Teamwork transport, auth, pagination, retry behavior
- `backend/app/financial/teamwork/teamwork_map.py`: API payload -> mirror row normalization
- `backend/app/financial/teamwork/teamwork_repository.py`: Supabase reads and writes
- `backend/app/financial/teamwork/teamwork_sync.py`: backfill and nightly sync orchestration
- `backend/app/financial/teamwork/teamwork_panels_from_db.py`: DB mirror -> dashboard payload

## Sync behavior

Initial backfill:

- pulls projects, people, milestones, and bounded current-year timelogs
- pulls tasks in the two dashboard buckets only
- writes mirror tables first
- builds the cached dashboard overview from the mirror
- marks the backfill complete only after the cache write succeeds

Nightly snapshot sync:

- refetches each dashboard slice without `updatedAfter`; due dates can change a task's
  operational bucket without Teamwork recording a remote edit
- prunes mirror rows not returned by the successful snapshot
- recomputes `teamwork_panel_cache` after entity upserts
- keeps the last known good cache if sync fails

Operational control tables:

- `teamwork_sync_state`
- `teamwork_sync_runs`
- `teamwork_panel_cache`

## Dashboard contract

`GET /api/v1/financials/teamwork/status` returns safe connection and sync metadata.

`GET /api/v1/financials/teamwork/overview` returns the latest cached payload from Supabase. It does not call Teamwork live during page load.

The frontend surfaces:

- sync freshness via `generated_at` / `synced_at`
- stale cache state via `sync_status`
- cached partial data if the last sync wrote a good snapshot but a later run failed

## Database

Migration: `backend/supabase/migrations/20260818_teamwork_mirror.sql`

Tables:

- `teamwork_projects`
- `teamwork_tasks`
- `teamwork_people`
- `teamwork_time_entries`
- `teamwork_milestones`
- `teamwork_panel_cache`
- `teamwork_sync_state`
- `teamwork_sync_runs`

All Teamwork mirror tables are service-role only with RLS enabled.

## Tests

Teamwork coverage is split across:

- `backend/tests/test_teamwork_client.py`
- `backend/tests/test_teamwork_repository.py`
- `backend/tests/test_teamwork_sync.py`
- `backend/tests/test_teamwork_panels_from_db.py`
- `backend/tests/test_teamwork_status.py`

These tests mock Teamwork and Supabase interactions; no live Teamwork key is required.
