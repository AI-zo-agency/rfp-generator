# QuickBooks Online Integration

How ZÖ Agency connects to QuickBooks Online (QBO), which Intuit APIs we call during sync, what we store in Supabase, and how the Financial Insights dashboard reads that mirror.

**Access model: read-only against QBO.** Every call into the company ledger is an HTTP `GET`. There is no create, update, or delete path against invoices, bills, customers, or any other QBO entity. The only `POST` to Intuit is the OAuth token endpoint (credential refresh), which does not mutate company accounting data.

**Dashboard model: nightly mirror.** The QuickBooks tab does not call Intuit on page load. APScheduler POSTs to our backend at 11pm Pacific; the backend pulls from QBO and writes to Supabase. `GET /overview` reads precomputed panel JSON from `qb_panel_cache` only.

---

## Architecture overview

QuickBooks remains the source of truth. This app holds a nightly mirror in Supabase Postgres.

```
┌─────────────────────┐     GET /api/v1/financials/quickbooks/overview     ┌──────────────────────┐
│  Frontend           │ ────────────────────────────────────────────────► │  FastAPI backend     │
│  QuickBooksPanels   │     (reads qb_panel_cache only; never Intuit)     │  financial/router.py │
└─────────────────────┘                                                 └──────────┬───────────┘
                                                                                     │
                                                                                     ▼
                                                                          ┌──────────────────────┐
                                                                          │  Supabase Postgres   │
                                                                          │  qb_* entity tables  │
                                                                          │  qb_panel_cache      │
                                                                          │  qb_oauth_tokens     │
                                                                          │  qb_sync_state       │
                                                                          └──────────▲───────────┘
                                                                                     │
┌─────────────────────┐     POST /api/v1/financials/quickbooks/sync       │          │
│  APScheduler        │     Header: X-Cron-Secret                         │          │
│  23:00 America/     │     Body: {"mode":"auto"}                         │          │
│  Los_Angeles        │                                                   │          │
└─────────────────────┘ ────────────────────────────────────────────────►│          │
                                                                           │          │
                                    ┌──────────────────────────────────────┘          │
                                    │  qb_sync.py orchestrator                          │
                                    │  first run → Query backfill from 2024-01-01       │
                                    │  later runs → CDC since cdc_cursor                │
                                    ▼                                                 │
                         ┌─────────────────────┐                 ┌─────────────────────┐
                         │ quickbooks_oauth.py │                 │ financial/          │
                         │ Token refresh +     │                 │ quickbooks.py       │
                         │ refresh rotation    │                 │ Read-only QBO client│
                         └─────────┬───────────┘                 └─────────┬───────────┘
                                   │ POST (OAuth only)                     │ GET only
                                   ▼                                       ▼
                         ┌─────────────────────┐                 ┌─────────────────────┐
                         │ Intuit OAuth        │                 │ QuickBooks Online   │
                         │ tokens/bearer       │                 │ Accounting API v3   │
                         └─────────────────────┘                 └─────────────────────┘
```

| Layer | Path | Role |
|-------|------|------|
| Frontend | `frontend/src/financial/components/QuickBooksPanels.tsx` | Renders ledger panels; fetches overview from cache |
| Backend routes | `backend/app/financial/router.py` | `/quickbooks/status`, `/quickbooks/overview`, `/quickbooks/sync` |
| Sync orchestrator | `backend/app/financial/qb_sync.py` | Backfill, nightly CDC, panel-cache writes |
| DB repository | `backend/app/financial/qb_repository.py` | Supabase upserts, cache reads, token store |
| Panel builders | `backend/app/financial/qb_panels_from_db.py` | Build overview JSON from mirror rows at sync time |
| QBO client | `backend/app/financial/quickbooks.py` | Queries, reports, CDC (sync job only) |
| OAuth | `backend/app/services/quickbooks_oauth.py` | Access-token cache + refresh-token rotation |
| Config | `backend/app/core/config.py` | Credentials, realm, sandbox vs production, cron secret |

Two ingest modes, same writer:

1. **Backfill** — paginated Query for listed entities from 2024-01-01 (full lists for Customer, Class, Department). Report snapshots for 2024, 2025, and the current year. Company info. Panel cache for those years. Sets `cdc_cursor` and `backfill_completed_at`.
2. **Nightly** — CDC since `cdc_cursor`. Upsert changed rows; set `is_deleted` on QBO tombstones; rebuild child rows for touched parents. Overwrite current-year report snapshots and company info. Rebuild current-year panel cache. Advance `cdc_cursor` only after the cache write succeeds.

---

## Database setup

Apply the mirror schema in the Supabase SQL editor (or via your migration tooling):

```
backend/supabase/migrations/20260813_quickbooks_mirror.sql
```

This creates entity tables (`qb_invoices`, `qb_bills`, …), child tables (`qb_purchase_lines`, `qb_txn_links`), report snapshots, `qb_panel_cache`, `qb_oauth_tokens`, `qb_sync_state`, `qb_sync_runs`, `qb_backfill_progress`, and the index catalog. Row Level Security revokes access from `anon` and `authenticated`; the backend uses the Supabase service role.

**After the first successful backfill**, run `ANALYZE` on all `qb_*` tables in the SQL editor so the new indexes have planner statistics. There is no ANALYZE RPC in the migration; this is a one-time manual step after backfill.

Example:

```sql
ANALYZE qb_invoices;
ANALYZE qb_bills;
ANALYZE qb_payments;
-- … repeat for every qb_* table created by the migration
```

---

## Configuration

Environment variables (loaded via settings):

| Setting | Purpose |
|---------|---------|
| `QUICKBOOKS_CLIENT_ID` | Intuit app client ID |
| `QUICKBOOKS_CLIENT_SECRET` | Intuit app client secret |
| `QUICKBOOKS_REFRESH_TOKEN` | Seed refresh token (first-run / fallback only) |
| `QUICKBOOKS_REALM_ID` | Company ID (`realmId`) |
| `QUICKBOOKS_ENVIRONMENT` | `sandbox` (default) or `production` |
| `QUICKBOOKS_MINOR_VERSION` | API minor version (default `75`) |
| `QUICKBOOKS_CRON_SECRET` | Shared secret for `POST /quickbooks/sync` (`X-Cron-Secret` header) |

API base URL:

- Sandbox: `https://sandbox-quickbooks.api.intuit.com`
- Production: `https://quickbooks.api.intuit.com`

All Accounting API paths are under:

```
{api_base}/v3/company/{realm_id}/...
```

Every request appends `minorversion={QUICKBOOKS_MINOR_VERSION}`.

**Token store:** Rotated refresh tokens are persisted in Supabase `qb_oauth_tokens` (keyed by `realm_id`). The env refresh token is seed/fallback only. Do not rely on `quickbooks_token.json` on Railway.

---

## Authentication (OAuth 2.0)

### API: Token refresh

| | |
|---|---|
| **Method** | `POST` |
| **URL** | `https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer` |
| **Access** | Credential exchange only — **not** a write to the company file |

**Request**

| Part | Value |
|------|--------|
| Headers | `Authorization: Basic {base64(client_id:client_secret)}` |
| | `Content-Type: application/x-www-form-urlencoded` |
| | `Accept: application/json` |
| Body | `grant_type=refresh_token` |
| | `refresh_token={stored or env token}` |

**Response (JSON)** — fields we use:

| Field | Use |
|-------|-----|
| `access_token` | Bearer token for Accounting API calls (~1 hour) |
| `expires_in` | TTL seconds (default assumed 3600); we refresh ~5 minutes early |
| `refresh_token` | New refresh token if Intuit rotated it — **must be persisted to `qb_oauth_tokens`** |
| `x_refresh_token_expires_in` | Used only for connection-status “days remaining” |

On `401` from any Accounting call, the client forces a token refresh and retries once.

---

## Intuit Accounting APIs we call

All of the following are **HTTP GET**, read-only. They are invoked only during sync (`POST /quickbooks/sync`), not on dashboard load.

Common headers on every Accounting call:

```
Authorization: Bearer {access_token}
Accept: application/json
```

---

### 1. Query API

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/v3/company/{realmId}/query?query={sql}&minorversion=…` |
| **Access** | **Read-only** |

SQL is URL-encoded. Pagination: `startposition` / `maxresults` (page size **1000**), until a page returns fewer than 1000 rows.

**Raw response shape:** `{ "QueryResponse": { "<EntityName>": [ ... ], ... } }`

#### Queries used

| Panel | SQL | Entity key | What we take from each row |
|-------|-----|------------|----------------------------|
| AR Aging | `select * from Invoice where Balance > '0'` | `Invoice` | `Balance`, `DueDate` / `TxnDate`, `CustomerRef.name` |
| AP Aging | `select * from Bill where Balance > '0'` | `Bill` | `Balance`, `DueDate` / `TxnDate`, `VendorRef.name` |
| Unattached cost | `select * from Purchase where TxnDate >= '{year}-01-01'` | `Purchase` | `TotalAmt`, `Line[]` (account / customer refs, amounts) |

Backfill uses `TxnDate >= 2024-01-01` for transaction entities. Customer, Class, and Department are fetched as full lists.

---

### 2. Reports API

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/v3/company/{realmId}/reports/{ReportName}?{params}&minorversion=…` |
| **Access** | **Read-only** |

**Raw response shape:** Nested report document with `Columns.Column[]` (`ColTitle`) and `Rows` / `Row` / `ColData` / `Summary` (flattened in code).

#### Reports used

| Panel | Report name | Query parameters | What we extract |
|-------|-------------|------------------|-----------------|
| Revenue by class | `ProfitAndLoss` | `start_date={year}-01-01`, `end_date={year}-12-31`, `summarize_column_by=Classes` | Column titles + “Total Income” row → Project/Recurring × Government/Private matrix |
| By account manager | `ProfitAndLoss` | same dates, `summarize_column_by=Departments` | “Total Income” and “Net Income” per department column |
| Monthly trend | `ProfitAndLoss` | same dates, `summarize_column_by=Month` | “Total Income” per month column |
| Client profitability | `CustomerIncome` | `start_date={year}-01-01`, `end_date={year}-12-31` | Per-customer income / expense / net (top 20 by income) |

Empty optional params are omitted from the query string. Snapshots are stored in `qb_report_snapshots` (latest per `report_name`, `year`, `params_hash`).

Intuit **5020 Permission Denied** on a report is skipped so the rest of the sync can finish. See `docs/QUICKBOOKS_REPORT_PERMISSIONS.md`. The matching dashboard panel then lands in overview `errors`. Other report failures still abort the run.

---

### 3. Change Data Capture (CDC)

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/v3/company/{realmId}/cdc?entities={list}&changedSince={iso}&minorversion=…` |
| **Access** | **Read-only** |

**Parameters we pass**

| Param | Value |
|-------|--------|
| `entities` | `Invoice,Bill,Payment,Purchase,PurchaseOrder,BillPayment,CreditMemo,Customer,Class,Department` |
| `changedSince` | `cdc_cursor` from `qb_sync_state` (nightly sync only) |

**Raw response:** `CDCResponse[]` → `QueryResponse[]` → entity name → list of changed records.

Nightly sync upserts changed rows and marks tombstones `is_deleted = true`. The activity panel on the dashboard counts local rows with `qbo_updated_at >= since`; it does not call CDC at request time.

---

### 4. Company Info

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/v3/company/{realmId}/companyinfo/{realmId}?minorversion=…` |
| **Access** | **Read-only** |

**Raw response:** `{ "CompanyInfo": { ... } }`

**Fields mapped into the dashboard:**

| QBO field | Dashboard field |
|-----------|-----------------|
| `CompanyName` | `company_name` |
| `LegalName` | `legal_name` |
| `CompanyAddr.City` | `city` |
| `CompanyAddr.CountrySubDivisionCode` | `state` |
| `FiscalYearStartMonth` | `fiscal_year_start` |
| `CompanyStartDate` | `start_date` |
| `NameValue` where `Name == OfferingSku` | `sku` |

Stored in `qb_company_info` and refreshed each sync.

---

## Backend HTTP endpoints (our API)

### `GET /api/v1/financials/quickbooks/status`

Safe health probe; never raises.

**Returns**

```json
{
  "connected": true,
  "realm_id": "<realm>",
  "environment": "sandbox|production",
  "refresh_token_days_remaining": 90,
  "last_success_at": "ISO-8601",
  "last_error": null,
  "backfill_completed": true
}
```

or

```json
{ "connected": false, "reason": "…" }
```

### `GET /api/v1/financials/quickbooks/overview`

Returns the latest persisted panel snapshot for the requested year. **Does not call Intuit.**

**Query parameters**

| Param | Default | Description |
|-------|---------|-------------|
| `year` | Current calendar year | Which `qb_panel_cache` row to read |
| `since` | (ignored) | Deprecated; activity counts come from mirror rows |
| `refresh` | (ignored) | Deprecated; snapshots refresh during sync only |

**Returns (shape)**

Same panel keys as before, plus:

| Field | Source |
|-------|--------|
| `as_of` | Sync date used for aging buckets (not `date.today()`) |
| `synced_at` | `qb_panel_cache.computed_at` |
| `sync_status` | `ok`, `failed`, `backfill_pending`, or `missing` |

If no cache row exists for the year, returns HTTP 200 with empty panels and an error key. Does not fall back to live QBO.

**Caching:** No in-memory cache. Data freshness is whatever the last successful sync wrote.

### `POST /api/v1/financials/quickbooks/sync`

Triggers backfill or nightly sync. Intended for Railway Cron and manual ops only — not exposed in the UI.

| | |
|---|---|
| **Auth** | Header `X-Cron-Secret` must match `QUICKBOOKS_CRON_SECRET`. Wrong or missing → **401**. No session/JWT. |
| **Body** | `{ "mode": "auto" \| "backfill" \| "nightly" }` (default `"auto"`) |
| **`auto`** | Backfill if `backfill_completed_at` is null; otherwise nightly |
| **Lease** | Only one sync per realm at a time. Held lease → **409** |
| **Writes** | Supabase mirror tables and `qb_panel_cache` only — **not** QBO |

On success, returns run metadata including `run_id` and entity counts. On failure, `cdc_cursor` is not advanced and the last-good panel cache remains served.

---

## Nightly scheduler (Railway)

A **third** Railway service, same `backend/` image as the API. Do **not** put a Cron Schedule on the FastAPI service — that would start/stop the web process.

| | |
|---|---|
| Root | `backend/` (same Dockerfile) |
| Start command | `python -m app.scheduler` |
| Replicas | **1** |
| Cron Schedule | leave empty (the process stays up; APScheduler is the clock) |

### Scheduler env

| Variable | Value |
|----------|--------|
| `SCHEDULER_BACKEND_URL` | Private URL of the API service, e.g. `http://<api-service>.railway.internal:8000` |
| `QUICKBOOKS_CRON_SECRET` | Same value as the API service |
| `SCHEDULER_TIMEZONE` | `America/Los_Angeles` |

The scheduler does not need QuickBooks client secrets or Supabase keys.

### Jobs

| Job id | When | Call |
|--------|------|------|
| `quickbooks_nightly` | `0 23 * * *` in `America/Los_Angeles` (11pm Pacific, DST-aware) | `POST /api/v1/financials/quickbooks/sync` body `{"mode":"auto"}` |

Timeout is 10 minutes. HTTP 409 (lease held) is logged and skipped. On process start the job runs immediately (`mode=auto`, CDC from `cdc_cursor`); the next run is 23:00 Pacific. Set `SCHEDULER_RUN_ON_START=false` to skip the startup fetch. Add future platforms as rows in `backend/app/scheduler/jobs.py`.

Local:

```bash
cd backend
python -m app.scheduler
```

Points at `SCHEDULER_BACKEND_URL` (default `http://127.0.0.1:8001`). The API must already be running.

---

## First production run

1. Apply `backend/supabase/migrations/20260813_quickbooks_mirror.sql` in Supabase.
2. Set env vars on Railway (including `QUICKBOOKS_CRON_SECRET`).
3. Trigger the initial backfill manually:

```bash
curl -X POST "$BACKEND_URL/api/v1/financials/quickbooks/sync" \
  -H "Content-Type: application/json" \
  -H "X-Cron-Secret: $QUICKBOOKS_CRON_SECRET" \
  -d '{"mode":"backfill"}'
```

4. Confirm completion:

```bash
curl "$BACKEND_URL/api/v1/financials/quickbooks/status"
```

Expect `"backfill_completed": true`.

5. Run `ANALYZE` on all `qb_*` tables in the Supabase SQL editor.
6. Deploy the scheduler service (`python -m app.scheduler`) so nightly `auto` runs at 11pm Pacific.

---

## Panel → data source map

At **sync time**, panels are built from mirror rows and report snapshots (via `qb_panels_from_db.py`) and written to `qb_panel_cache`. At **request time**, `/overview` returns that cached JSON.

| Dashboard panel | Sync-time source | Intuit API (during sync) |
|-----------------|------------------|--------------------------|
| Company header | `qb_company_info` | CompanyInfo |
| AR aging | `qb_invoices` | Query: Invoice where balance > 0 |
| AP aging | `qb_bills` | Query: Bill where balance > 0 |
| Revenue by class | `qb_report_snapshots` | Report: ProfitAndLoss by Classes |
| By account manager | `qb_report_snapshots` | Report: ProfitAndLoss by Departments |
| Client profitability | `qb_report_snapshots` | Report: CustomerIncome |
| Monthly trend | `qb_report_snapshots` | Report: ProfitAndLoss by Month |
| Unattached cost | `qb_purchases`, `qb_purchase_lines` | Query: Purchase from year start |
| Recent activity | entity `qbo_updated_at` counts | CDC (nightly ingest) |
| Cash collections | `qb_payments` | Query: Payment from year start |
| Billing vs cash | `qb_invoices`, `qb_payments`, `qb_txn_links` | Query + links |
| DSO | `qb_txn_links` | Linked Payment → Invoice |
| Aged AR detail | `qb_report_snapshots` | Report: AgedReceivableDetail |
| Purchase orders | `qb_purchase_orders` | Query: PurchaseOrder |
| Expenses by vendor | `qb_report_snapshots` | Report: ExpensesByVendorSummary |
| Bill payments | `qb_bill_payments` | Query: BillPayment from year start |
| Customers | `qb_customers` | Query: active Customer |
| Sales by customer | `qb_report_snapshots` | Report: SalesByCustomer |
| Credit memos | `qb_credit_memos` | Query: CreditMemo from year start |
| Class / department coverage | `qb_classes`, `qb_departments` + P&L | Query + P&L |
| Liquidity | `qb_report_snapshots` | Report: BalanceSheet, CashFlow |

Aging buckets (computed at sync time from due dates vs `as_of`): `Not yet due`, `1-30 days`, `31-60 days`, `61-90 days`, `90+ days`.

Unattached cost treats purchases with no `CustomerRef` on expense lines as unattached; accounts whose names start with `COSS`, `COL -`, or `COGS` are flagged as cost-of-service.

**Source of truth:** QuickBooks ledger only. The zö dashboard Google Sheet is **not** ingested.

---

## Are all APIs read-only?

| Surface | Methods used | Mutates company data? |
|---------|--------------|------------------------|
| Query API | `GET` only | **No** |
| Reports API | `GET` only | **No** |
| CDC | `GET` only | **No** |
| CompanyInfo | `GET` only | **No** |
| OAuth token endpoint | `POST` | **No** (token rotation only) |
| Our `GET /quickbooks/*` routes | `GET` only | **No** |
| Our `POST /quickbooks/sync` | `POST` | **No** — writes to **our** Supabase mirror only |

**Yes — all Accounting API usage is read-only.** The codebase states this explicitly in `quickbooks.py` and does not implement any QBO create/update/delete helpers.

**Caveats (ops, not data writes):**

1. OAuth refresh **rotates** the refresh token; failing to persist it to `qb_oauth_tokens` breaks future access.
2. Intuit app scopes / company authorization should still be configured as **read** (or the minimum needed for reports + query + CDC) in the Intuit Developer portal — the app code never issues writes even if broader scopes were granted.
3. The `/financials/sources` status list may still show QuickBooks as “Pending Integration”; the live ledger UI is driven by `/quickbooks/overview`, not that static sources payload.

---

## Frontend wiring

- Tab: **QuickBooks Ledger** in `FinancialInsightsClient.tsx`
- Component: `QuickBooksPanels` fetches  
  `{BACKEND}/api/v1/financials/quickbooks/overview?year={year}`
- **Synced stamp:** shows `synced_at` and `sync_status` from the overview response. No Refresh control.
- **Sectioned Operate UI:** sticky section nav → Health strip → Cash → Open (Who owes us / What we owe side by side) → Revenue → Clients → Costs → Activity footnote
- Year selector in the header; progressive disclosure (“Show all”) on long lists
- New overview keys degrade independently via `errors` without blanking the page

---

## Source files

| File | Responsibility |
|------|----------------|
| `backend/app/services/quickbooks_oauth.py` | OAuth refresh; tokens in `qb_oauth_tokens` |
| `backend/app/financial/quickbooks.py` | Intuit GET client (sync job) |
| `backend/app/financial/qb_sync.py` | Backfill + nightly orchestrator |
| `backend/app/financial/qb_repository.py` | Supabase reads/writes |
| `backend/app/financial/qb_map.py` | QBO payload → typed row mapping |
| `backend/app/financial/qb_panels_from_db.py` | Panel builders from mirror |
| `backend/app/financial/router.py` | `/quickbooks/status`, `/overview`, `/sync` |
| `backend/app/core/config.py` | QB settings + cron secret + scheduler URL |
| `backend/app/scheduler/` | APScheduler worker; POSTs `/quickbooks/sync` |
| `backend/supabase/migrations/20260813_quickbooks_mirror.sql` | Mirror schema + indexes |
| `frontend/src/financial/components/QuickBooksPanels.tsx` | Sectioned ledger dashboard UI |

---

## Quick reference: every Intuit call

| # | Method | Endpoint | Key parameters | Returns (to us) |
|---|--------|----------|----------------|-----------------|
| 1 | `POST` | `oauth.platform.intuit.com/oauth2/v1/tokens/bearer` | `grant_type`, `refresh_token` | `access_token`, rotated `refresh_token`, TTLs |
| 2 | `GET` | `…/query` | SQL for entities below + pagination | Entity arrays under `QueryResponse` |
| 3 | `GET` | `…/reports/ProfitAndLoss` | `start_date`, `end_date`, `summarize_column_by` | Nested P&amp;L columns/rows |
| 4 | `GET` | `…/reports/CustomerIncome` | `start_date`, `end_date` | Nested customer income rows |
| 5 | `GET` | `…/reports/AgedReceivableDetail` | `report_date` | Nested AR detail |
| 6 | `GET` | `…/reports/ExpensesByVendorSummary` | `start_date`, `end_date` | Vendor expense rows |
| 7 | `GET` | `…/reports/SalesByCustomer` | `start_date`, `end_date` | Customer sales rows |
| 8 | `GET` | `…/reports/BalanceSheet` | `date` | Balance sheet rows |
| 9 | `GET` | `…/reports/CashFlow` | `start_date`, `end_date` | Cash flow rows |
| 10 | `GET` | `…/cdc` | `entities`, `changedSince` | Changed-entity lists |
| 11 | `GET` | `…/companyinfo/{realmId}` | (realm in path) | `CompanyInfo` object |

**Query entities (all GET):** Invoice, Bill, Payment, Purchase, PurchaseOrder, BillPayment, CreditMemo, Customer, Class, Department.
