# QuickBooks Online Integration

How ZÖ Agency connects to QuickBooks Online (QBO), which Intuit APIs we call, what we send and receive, and how that data reaches the Financial Insights dashboard.

**Access model: read-only.** Every call into the company ledger is an HTTP `GET`. There is no create, update, or delete path against invoices, bills, customers, or any other QBO entity. The only `POST` is Intuit’s OAuth token endpoint (credential refresh), which does not mutate company accounting data.

---

## Architecture overview

```
┌─────────────────────┐     GET /api/v1/financials/quickbooks/*      ┌──────────────────────┐
│  Frontend           │ ──────────────────────────────────────────► │  FastAPI backend     │
│  QuickBooksPanels   │                                             │  financial/router.py │
└─────────────────────┘                                             └──────────┬───────────┘
                                                                               │
                                    ┌──────────────────────────────────────────┼────────────────────────┐
                                    │                                          │                        │
                                    ▼                                          ▼                        ▼
                         ┌─────────────────────┐                 ┌─────────────────────┐   ┌────────────────────┐
                         │ quickbooks_oauth.py │                 │ financial/          │   │ In-memory cache    │
                         │ Token refresh +     │                 │ quickbooks.py       │   │ (5 min TTL)        │
                         │ refresh rotation    │                 │ Read-only QBO client│   └────────────────────┘
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
| Frontend | `frontend/src/financial/components/QuickBooksPanels.tsx` | Renders ledger panels; fetches overview |
| Backend routes | `backend/app/financial/router.py` | `/quickbooks/status`, `/quickbooks/overview` |
| QBO client | `backend/app/financial/quickbooks.py` | Queries, reports, CDC, panel transforms |
| OAuth | `backend/app/services/quickbooks_oauth.py` | Access-token cache + refresh-token rotation |
| Config | `backend/app/core/config.py` | Credentials, realm, sandbox vs production |

---

## Configuration

Environment variables (loaded via settings):

| Setting | Purpose |
|---------|---------|
| `QUICKBOOKS_CLIENT_ID` | Intuit app client ID |
| `QUICKBOOKS_CLIENT_SECRET` | Intuit app client secret |
| `QUICKBOOKS_REFRESH_TOKEN` | Seed refresh token (rotated tokens are persisted separately) |
| `QUICKBOOKS_REALM_ID` | Company ID (`realmId`) |
| `QUICKBOOKS_ENVIRONMENT` | `sandbox` (default) or `production` |
| `QUICKBOOKS_MINOR_VERSION` | API minor version (default `75`) |

API base URL:

- Sandbox: `https://sandbox-quickbooks.api.intuit.com`
- Production: `https://quickbooks.api.intuit.com`

All Accounting API paths are under:

```
{api_base}/v3/company/{realm_id}/...
```

Every request appends `minorversion={QUICKBOOKS_MINOR_VERSION}`.

**Token store:** Rotated refresh tokens are written next to the SQLite DB as `quickbooks_token.json` (same directory as `database_path`). The env refresh token is only the seed for first use / fallback.

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
| `refresh_token` | New refresh token if Intuit rotated it — **must be persisted** |
| `x_refresh_token_expires_in` | Used only for connection-status “days remaining” |

On `401` from any Accounting call, the client forces a token refresh and retries once.

---

## Intuit Accounting APIs we call

All of the following are **HTTP GET**, read-only.

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

Empty optional params are omitted from the query string.

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
| `entities` | `Invoice,Bill,Payment,PurchaseOrder,Customer,Purchase` |
| `changedSince` | ISO timestamp (default: first day of current month, `-07:00` offset) |

**Raw response:** `CDCResponse[]` → `QueryResponse[]` → entity name → list of changed records.

**What we return to the UI:** counts only (`{ entity, changed }`), not full entity payloads.

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

---

## Backend HTTP endpoints (our API)

These are **our** FastAPI routes that wrap the Intuit client. They are also read-only from the client’s perspective (`GET` only).

### `GET /api/v1/financials/quickbooks/status`

Safe health probe; never raises.

**Returns**

```json
{
  "connected": true,
  "realm_id": "<realm>",
  "environment": "sandbox|production",
  "refresh_token_days_remaining": 90
}
```

or

```json
{ "connected": false, "reason": "…" }
```

### `GET /api/v1/financials/quickbooks/overview`

Loads every dashboard panel. Panels fail independently (`null` + message in `errors`).

**Query parameters**

| Param | Default | Description |
|-------|---------|-------------|
| `year` | Current calendar year | Report / purchase filter year |
| `since` | `{year}-{month}-01T00:00:00-07:00` | CDC `changedSince` |
| `refresh` | `false` | Bypass 5-minute in-memory cache |

**Returns (shape)**

```json
{
  "year": 2026,
  "generated_at": "ISO-8601",
  "errors": { "<panel>": "error message" },
  "company": { "company_name", "legal_name", "city", "state", "fiscal_year_start", "start_date", "sku" },
  "ar": {
    "total", "invoice_count", "overdue_total",
    "buckets": [{ "label", "amount", "count", "pct" }],
    "clients": [{ "client", "amount", "invoices", "oldest_days" }]
  },
  "ap": {
    "total", "bill_count",
    "buckets": [{ "label", "amount" }],
    "vendors": [{ "vendor", "amount" }]
  },
  "revenue_by_class": {
    "matrix": [{ "parent", "segment", "amount" }],
    "parents", "segments", "unclassified", "total", "coverage_pct"
  },
  "by_account_manager": {
    "managers": [{ "manager", "income", "net", "is_overhead" }]
  },
  "client_profitability": {
    "clients": [{ "client", "income", "expense", "net", "margin_pct" }],
    "attributed_expense"
  },
  "monthly_trend": {
    "months": [{ "month", "amount" }],
    "total", "peak", "last_booked_month"
  },
  "unattached_cost": {
    "purchase_count", "purchase_total", "unattached_count", "unattached_pct",
    "cost_of_service_unattached",
    "accounts": [{ "account", "amount", "is_cost_of_service" }]
  },
  "activity": {
    "since",
    "total",
    "entities": [{ "entity", "changed" }]
  }
}
```

Any panel key may be `null` if that Intuit call failed.

**Caching:** Overview results are cached in process memory for **300 seconds** (key includes `year` and `since`).

---

## Panel → Intuit API map

| Dashboard panel | Intuit API | Operation |
|-----------------|------------|-----------|
| Company header | CompanyInfo | `GET companyinfo/{realmId}` |
| AR aging | Query | `Invoice` where balance &gt; 0 |
| AP aging | Query | `Bill` where balance &gt; 0 |
| Revenue by class | Report | `ProfitAndLoss` by Classes |
| By account manager | Report | `ProfitAndLoss` by Departments |
| Client profitability | Report | `CustomerIncome` |
| Monthly trend | Report | `ProfitAndLoss` by Month |
| Unattached cost | Query | `Purchase` from year start |
| Recent activity | CDC | Invoice, Bill, Payment, PurchaseOrder, Customer, Purchase |
| Cash collections | Query | `Payment` from year start |
| Billing vs cash | Query | `Invoice` + `Payment` from year start |
| DSO | Query | Linked `Payment` → `Invoice` |
| Aged AR detail | Report | `AgedReceivableDetail` |
| Purchase orders | Query | `PurchaseOrder` |
| Expenses by vendor | Report | `ExpensesByVendorSummary` |
| Bill payments | Query | `BillPayment` from year start |
| Customers | Query | Active `Customer` |
| Sales by customer | Report | `SalesByCustomer` |
| Credit memos | Query | `CreditMemo` from year start |
| Class / department coverage | Query + P&amp;L | `Class`, `Department` |
| Liquidity | Report | `BalanceSheet`, `CashFlow` |

Aging buckets (computed in-app from due dates): `Not yet due`, `1-30 days`, `31-60 days`, `61-90 days`, `90+ days`.

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
| Our FastAPI QB routes | `GET` only | **No** |

**Yes — all Accounting API usage is read-only.** The codebase states this explicitly in `quickbooks.py` and does not implement any QBO create/update/delete helpers.

**Caveats (ops, not data writes):**

1. OAuth refresh **rotates** the refresh token; failing to persist it breaks future access.
2. Intuit app scopes / company authorization should still be configured as **read** (or the minimum needed for reports + query + CDC) in the Intuit Developer portal — the app code never issues writes even if broader scopes were granted.
3. The `/financials/sources` status list may still show QuickBooks as “Pending Integration”; the live ledger UI is driven by `/quickbooks/overview`, not that static sources payload.

---

## Frontend wiring

- Tab: **QuickBooks Ledger** in `FinancialInsightsClient.tsx`
- Component: `QuickBooksPanels` fetches  
  `{BACKEND}/api/v1/financials/quickbooks/overview?year={year}`  
  (optional `refresh=true`)
- **Sectioned Operate UI:** sticky section nav → Health strip → Cash → Receivables → Payables → Revenue → Clients → Costs → Activity footnote
- Year selector in the header; progressive disclosure (“Show all”) on long lists
- New overview keys degrade independently via `errors` without blanking the page

---

## Source files

| File | Responsibility |
|------|----------------|
| `backend/app/services/quickbooks_oauth.py` | OAuth refresh, token cache, connection status |
| `backend/app/financial/quickbooks.py` | Intuit GET client + panel builders |
| `backend/app/financial/router.py` | `/quickbooks/status`, `/quickbooks/overview` |
| `backend/app/core/config.py` | QB settings + API base URL |
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
| 10 | `GET` | `…/cdc` | `entities`, `changedSince` | Changed-entity lists (we keep counts) |
| 11 | `GET` | `…/companyinfo/{realmId}` | (realm in path) | `CompanyInfo` object |

**Query entities (all GET):** Invoice, Bill, Payment, Purchase, PurchaseOrder, BillPayment, CreditMemo, Customer, Class, Department.
