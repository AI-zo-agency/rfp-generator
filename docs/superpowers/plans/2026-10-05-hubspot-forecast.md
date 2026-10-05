# HubSpot-Augmented Financial Forecast — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feed HubSpot deals (weighted open pipeline + Closed Won not yet invoiced) into the Forecast tab so year, monthly remaining, and 13-week `from_new_billing` are deterministic hybrids on top of QuickBooks booked revenue — LLM narrates only.

**Architecture:** Extend the read-only HubSpot mirror with `hs_deals` + a stage catalog. Pure Python in `hs_forecast.py` computes monthly/year/cash week money figures from mirror + QB helpers. `qb_forecast_llm.generate_and_store` (and monthly path) persist those figures and keep LLM for `plain` narrative only. Forecast UI shows a three-layer composition breakdown.

**Tech Stack:** FastAPI, Supabase/Postgres, existing HubSpot private-app sync (`app/leads/hubspot.py`), QuickBooks mirror + `qb_forecast_llm` helpers, Next.js Forecast tab, pytest.

**Spec:** `docs/superpowers/specs/2026-10-05-hubspot-forecast-design.md`

---

## File structure

| File | Responsibility |
|------|----------------|
| `backend/supabase/migrations/20261005_hubspot_deals.sql` | `hs_deals`, `hs_deal_stages`, sync_runs column |
| `backend/app/leads/hubspot.py` | Sync deals + stages; row mappers |
| `backend/app/core/config.py` | Document deals read scope on `hubspot_api_key` |
| `backend/app/financial/hs_forecast.py` | Pure pipeline math + cash week assembly |
| `backend/app/financial/hs_forecast_match.py` | Closed Won ↔ QB invoice matching |
| `backend/app/financial/qb_forecast_llm.py` | Load HS evidence; replace money payloads; narrative only |
| `backend/app/financial/qb_forecast_monthly.py` | Prefer hybrid monthly points when HS data present |
| `backend/app/financial/router.py` | HubSpot source status when deals synced |
| `backend/tests/test_hs_forecast.py` | Weighting, monthly, year, cash weeks |
| `backend/tests/test_hs_forecast_match.py` | Won matching |
| `backend/tests/test_hubspot_deals_sync.py` | Deal row mapper / sync unit bits |
| `frontend/src/financial/types/quickbooks.ts` | `composition` on forecast payload |
| `frontend/src/financial/components/QuickBooksPanels.tsx` | Breakdown under year/cash/monthly |

Constants (live in `hs_forecast.py`):

```python
INVOICE_LAG_DAYS = 7
WON_MATCH_AMOUNT_RATIO = 0.80
WON_MATCH_DATE_SLACK_DAYS = 14
STAGE_FALLBACK = {"early": 0.10, "mid": 0.50, "late": 0.80, "won": 1.0, "lost": 0.0}
```

---

### Task 1: Migration — `hs_deals` + stage catalog

**Files:**
- Create: `backend/supabase/migrations/20261005_hubspot_deals.sql`
- Test: apply locally / review SQL only (no pytest for DDL)

- [ ] **Step 1: Add migration**

```sql
-- HubSpot deals mirror for financial forecast (read-only; rebuilt by sync).

CREATE TABLE IF NOT EXISTS hs_deals (
  hs_id BIGINT PRIMARY KEY,
  dealname TEXT,
  amount NUMERIC,
  dealstage TEXT,
  pipeline TEXT,
  closedate TIMESTAMPTZ,
  hs_is_closed BOOLEAN NOT NULL DEFAULT false,
  hs_is_closed_won BOOLEAN NOT NULL DEFAULT false,
  hs_is_closed_lost BOOLEAN NOT NULL DEFAULT false,
  stage_probability NUMERIC,          -- hs_deal_stage_probability at sync time
  company_hs_id BIGINT,               -- associated company when present
  owner_name TEXT,
  properties JSONB NOT NULL DEFAULT '{}'::jsonb,
  hs_updated_at TIMESTAMPTZ,
  archived BOOLEAN NOT NULL DEFAULT false,
  synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS hs_deals_closedate_idx ON hs_deals (closedate);
CREATE INDEX IF NOT EXISTS hs_deals_company_idx ON hs_deals (company_hs_id);
CREATE INDEX IF NOT EXISTS hs_deals_open_idx ON hs_deals (hs_is_closed, closedate)
  WHERE archived = false;

CREATE TABLE IF NOT EXISTS hs_deal_stages (
  pipeline_id TEXT NOT NULL,
  stage_id TEXT NOT NULL,
  pipeline_label TEXT,
  stage_label TEXT,
  probability NUMERIC,
  is_closed BOOLEAN NOT NULL DEFAULT false,
  display_order INT,
  synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (pipeline_id, stage_id)
);

ALTER TABLE hubspot_sync_runs
  ADD COLUMN IF NOT EXISTS deals_upserted INT,
  ADD COLUMN IF NOT EXISTS deals_archived INT;

ALTER TABLE hs_deals ENABLE ROW LEVEL SECURITY;
ALTER TABLE hs_deal_stages ENABLE ROW LEVEL SECURITY;
```

- [ ] **Step 2: Apply migration** in the usual local Supabase flow for this repo (same as prior HubSpot mirror). Confirm tables exist.

- [ ] **Step 3: Commit**

```bash
git add -f backend/supabase/migrations/20261005_hubspot_deals.sql
git commit -m "feat(hubspot): add hs_deals and stage catalog tables"
```

---

### Task 2: Sync deals + stages in HubSpot mirror

**Files:**
- Modify: `backend/app/leads/hubspot.py`
- Modify: `backend/app/core/config.py` (scope comment only)
- Create: `backend/tests/test_hubspot_deals_sync.py`

- [ ] **Step 1: Write failing mapper tests**

```python
# backend/tests/test_hubspot_deals_sync.py
from app.leads import hubspot

def test_deal_row_maps_core_fields():
    raw = {
        "id": "99",
        "updatedAt": "2026-10-01T12:00:00.000Z",
        "properties": {
            "dealname": "Speedee Delivery",
            "amount": "10000",
            "dealstage": "3342914252",
            "pipeline": "default",
            "closedate": "2026-10-01T00:00:00.000Z",
            "hs_is_closed": "false",
            "hs_is_closed_won": "false",
            "hs_is_closed_lost": "false",
            "hs_deal_stage_probability": "0.5",
            "hubspot_owner_id": "1",
        },
        "associations": {
            "companies": {"results": [{"id": "55", "type": "deal_to_company"}]}
        },
    }
    row = hubspot.deal_row(raw, owners={"1": "Sonja"}, synced_at="2026-10-05T00:00:00+00:00")
    assert row["hs_id"] == 99
    assert row["dealname"] == "Speedee Delivery"
    assert float(row["amount"]) == 10000.0
    assert row["company_hs_id"] == 55
    assert float(row["stage_probability"]) == 0.5
    assert row["hs_is_closed"] is False


def test_stage_rows_from_pipeline_payload():
    pipe = {
        "id": "default",
        "label": "New Clients and scope expansions",
        "stages": [
            {"id": "a", "label": "Contact made", "displayOrder": 0,
             "metadata": {"probability": "0.1", "isClosed": "false"}},
            {"id": "b", "label": "Closed won", "displayOrder": 1,
             "metadata": {"probability": "1.0", "isClosed": "true"}},
        ],
    }
    rows = hubspot.stage_rows(pipe, synced_at="2026-10-05T00:00:00+00:00")
    assert len(rows) == 2
    assert rows[0]["probability"] == 0.1
    assert rows[1]["is_closed"] is True
```

- [ ] **Step 2: Run tests — expect fail**

```bash
cd /Users/princepatel/Projects/zo-agency && .venv/bin/python -m pytest backend/tests/test_hubspot_deals_sync.py -v
```

Expected: `AttributeError` / import fail for `deal_row` / `stage_rows`.

- [ ] **Step 3: Implement mappers + wire into `run_sync`**

In `hubspot.py`:

```python
DEAL_PROPS = [
    "dealname", "amount", "dealstage", "pipeline", "closedate",
    "hs_is_closed", "hs_is_closed_won", "hs_is_closed_lost",
    "hs_deal_stage_probability", "hubspot_owner_id", "hs_lastmodifieddate",
]

# Extend OBJECTS — deals use hs_lastmodifieddate for search watermark:
# "deals": (DEAL_PROPS, "hs_lastmodifieddate"),
```

- `deal_row(obj, owners, synced_at) -> dict` — mirror `contact_row` style; parse booleans from HubSpot `"true"`/`"false"` strings; take first company association id.
- `stage_rows(pipeline, synced_at) -> list[dict]`.
- `sync_deal_stages(http, db, synced_at)` — `GET /crm/v3/pipelines/deals`, upsert all into `hs_deal_stages`.
- In `run_sync`: after companies/contacts, call `sync_deal_stages`, then sync `deals` (same full/incremental pattern). Prefer listing with `associations=companies` when using `iter_all` / search (add optional associations param to list/search helpers).
- Full sync: archive stale deals like contacts (`archived=True` when `synced_at` old).
- Update `hubspot_sync_runs` with `deals_upserted` / `deals_archived`.
- Log: `operation=hubspot_sync ... deals_upserted=N`.

Update `config.py` comment:

```python
# ... crm.objects.deals.read, crm.schemas.deals.read (pipelines) ...
```

- [ ] **Step 4: Re-run tests — expect pass**

```bash
.venv/bin/python -m pytest backend/tests/test_hubspot_deals_sync.py -v
```

- [ ] **Step 5: Manual smoke (optional if key set)**

```bash
# POST /api/v1/leads/hubspot/sync {"mode":"auto"} with auth — confirm deals_upserted > 0
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/leads/hubspot.py backend/app/core/config.py backend/tests/test_hubspot_deals_sync.py
git commit -m "feat(hubspot): sync deals and pipeline stages into mirror"
```

---

### Task 3: Pure forecast math (`hs_forecast.py`)

**Files:**
- Create: `backend/app/financial/hs_forecast.py`
- Create: `backend/tests/test_hs_forecast.py`

- [ ] **Step 1: Write failing tests for weighting + monthly + year**

```python
from datetime import date
from app.financial import hs_forecast as F

def _deal(**kw):
    base = {
        "hs_id": 1, "dealname": "X", "amount": 10000.0, "dealstage": "s",
        "pipeline": "default", "closedate": date(2026, 10, 15),
        "hs_is_closed": False, "hs_is_closed_won": False, "hs_is_closed_lost": False,
        "stage_probability": 0.5, "company_hs_id": 9,
    }
    base.update(kw)
    return base


def test_closed_lost_excluded():
    deals = [_deal(hs_is_closed_lost=True, hs_is_closed=True, stage_probability=0)]
    assert F.weighted_contribution(deals[0], stages={}) == 0.0


def test_open_uses_hubspot_probability():
    assert F.weighted_contribution(_deal(stage_probability=0.5), stages={}) == 5000.0


def test_missing_probability_uses_fallback_from_stage_catalog():
    deal = _deal(stage_probability=None, dealstage="late1")
    stages = {("default", "late1"): {"probability": None, "label": "Finalizing terms", "is_closed": False}}
    # late bucket → 0.80
    assert F.weighted_contribution(deal, stages) == 8000.0


def test_monthly_hybrid_locks_past_months_to_qb_only():
    as_of = date(2026, 10, 5)
    qb = {"2026-01": 100.0, "2026-10": 50.0}  # booked by month
    deals = [_deal(closedate=date(2026, 1, 10), amount=999, stage_probability=1.0)]  # stale open
    months = F.monthly_points(qb_booked=qb, deals=deals, stages={}, as_of=as_of, year=2026)
    assert months["2026-01"]["point"] == 100.0  # no HubSpot rewrite
    # stale open close date rolls into as_of month
    assert months["2026-10"]["point"] == 50.0 + 999.0


def test_year_equals_sum_of_months():
    as_of = date(2026, 10, 5)
    qb = {f"2026-{m:02d}": 10.0 for m in range(1, 10)}
    deals = [_deal(closedate=date(2026, 11, 1), amount=1000, stage_probability=0.5)]
    months = F.monthly_points(qb_booked=qb, deals=deals, stages={}, as_of=as_of, year=2026)
    year = F.year_point(months)
    assert year == sum(m["point"] for m in months.values())
```

- [ ] **Step 2: Run — expect fail**

```bash
.venv/bin/python -m pytest backend/tests/test_hs_forecast.py -v
```

- [ ] **Step 3: Implement `hs_forecast.py` (weighting + monthly + year)**

Public API:

```python
def weighted_contribution(deal: dict, stages: dict[tuple[str, str], dict]) -> float: ...
def monthly_points(*, qb_booked: dict[str, float], deals: list[dict], stages: dict,
                   as_of: date, year: int, unmatched_won_ids: set[int] | None = None) -> dict[str, dict]: ...
def year_point(months: dict[str, dict]) -> float: ...
def composition(months: dict[str, dict]) -> dict[str, float]:
    """Sums layers: qb_booked, won_awaiting_invoice, weighted_open."""
```

Rules from spec: Closed Lost → 0; Won not matched → 100%; open → amount × prob; missing prob → stage catalog then `STAGE_FALLBACK` by label keywords (contact/discussion=early, proposal/summary=mid, finalizing=late).

- [ ] **Step 4: Add cash-week tests**

```python
def test_new_billing_lands_after_invoice_lag_and_collection_median():
    as_of = date(2026, 10, 5)
    deals = [_deal(
        closedate=date(2026, 10, 6), amount=10000, stage_probability=1.0,
        hs_is_closed_won=True, hs_is_closed=True,
    )]
    curve = {"median_days": 14, "cumulative_pct": {"7": 40.0, "14": 70.0, "30": 90.0}}
    weeks = F.cash_weeks(
        as_of=as_of,
        cash_on_hand=1000.0,
        open_ar=[{"balance": 500.0, "raised": "2026-09-01", "due": "2026-09-15"}],
        deals=deals,
        stages={},
        collection_curve=curve,
        weekly_outflow=100.0,
    )
    assert len(weeks) == 13
    assert sum(w["from_new_billing"] for w in weeks) > 0
    # billing week ≈ close+7; cash ≈ billing+median_days → not week 1
    assert weeks[0]["from_new_billing"] == 0.0


def test_face_amount_never_enters_unweighted():
    deals = [_deal(amount=2_400_000, stage_probability=0.1)]
    weeks = F.cash_weeks(
        as_of=date(2026, 10, 5), cash_on_hand=0,
        open_ar=[], deals=deals, stages={},
        collection_curve={"median_days": 0, "cumulative_pct": {"0": 100.0}},
        weekly_outflow=0,
    )
    assert sum(w["from_new_billing"] for w in weeks) == pytest.approx(240_000.0)
```

Implement `cash_weeks(...)` returning 13 dicts with `week`, `ending`, `from_open_invoices`, `from_new_billing`, `outflow`, `closing_balance`. Schedule open AR using due/raised + curve median (simple deterministic: put full open balance into week containing `raised + median_days` or `due`, whichever is later, clamped into horizon; YAGNI vs full curve split — document in a `ponytail:` comment if using median-only). HubSpot new billing: `cash_date = closedate + INVOICE_LAG_DAYS + median_days`.

- [ ] **Step 5: Run all `test_hs_forecast` — pass**

- [ ] **Step 6: Commit**

```bash
git add backend/app/financial/hs_forecast.py backend/tests/test_hs_forecast.py
git commit -m "feat(financial): HubSpot-weighted year monthly and cash math"
```

---

### Task 4: Closed Won ↔ QB matching

**Files:**
- Create: `backend/app/financial/hs_forecast_match.py`
- Create: `backend/tests/test_hs_forecast_match.py`

- [ ] **Step 1: Failing tests**

```python
from datetime import date
from app.financial.hs_forecast_match import match_won_deals

def test_won_matched_when_invoices_cover_80_percent():
    deals = [{
        "hs_id": 1, "amount": 10000, "closedate": date(2026, 8, 1),
        "hs_is_closed_won": True, "company_hs_id": 7, "dealname": "Acme",
    }]
    companies = {7: {"name": "Acme Corp", "domain": "acme.com"}}
    invoices = [{
        "customer_name": "Acme Corp", "txn_date": "2026-08-10",
        "total_amt": 9000, "is_deleted": False,
    }]
    result = match_won_deals(deals, companies=companies, invoices=invoices, as_of=date(2026, 10, 5))
    assert 1 in result.matched_ids
    assert result.unmatched == []


def test_won_unmatched_kept_when_no_customer():
    deals = [{
        "hs_id": 2, "amount": 5000, "closedate": date(2026, 9, 1),
        "hs_is_closed_won": True, "company_hs_id": None, "dealname": "Mystery",
    }]
    result = match_won_deals(deals, companies={}, invoices=[], as_of=date(2026, 10, 5))
    assert 2 in {d["hs_id"] for d in result.unmatched}
    assert result.matched_ids == set()
```

Matching rules: normalize names (`client_map_normalize.normalize_name`); optional domain equality; invoices on/after `closedate - 14d`; sum `total_amt` (or amount field used by `list_invoices`) ≥ 80% of deal amount → matched.

- [ ] **Step 2: Run — fail; implement; run — pass; commit**

```bash
git add backend/app/financial/hs_forecast_match.py backend/tests/test_hs_forecast_match.py
git commit -m "feat(financial): match HubSpot Closed Won to QuickBooks invoices"
```

---

### Task 5: Wire into forecast generate + monthly

**Files:**
- Modify: `backend/app/financial/qb_forecast_llm.py`
- Modify: `backend/app/financial/qb_forecast_monthly.py`
- Add loaders in `hs_forecast.py`: `load_deals()`, `load_stages()` via Supabase service client (same pattern as hubspot `_db()`)

- [ ] **Step 1: Add `build_hubspot_forecast(realm_id, overview, year, as_of) -> dict | None`**

Returns `None` if no deals / HubSpot unconfigured. Else:

```python
{
  "cash_13w": {"weeks": [...], "trough": {...}, "low": None, "high": None,
               "assumptions": "HubSpot weighted pipeline + QB AR/outflow", "risks": [...]},
  "year": {"point": ..., "low": None, "high": None, "remaining_months": ...,
           "confidence": "medium", "reasoning": "deterministic HubSpot+QB hybrid"},
  "composition": {"qb_booked": ..., "won_awaiting_invoice": ..., "weighted_open": ...},
  "unmatched_won": [{"hs_id", "dealname", "amount"}],
  "months": { "2026-10": {"point", "qb_booked", "won_awaiting_invoice", "weighted_open"}, ... },
}
```

Use existing `open_invoices`, `collection_curve`, `monthly_outflow` for cash inputs. Map QB booked months from overview / panel month rows (same source monthly forecast uses).

- [ ] **Step 2: Change `generate_and_store`**

```python
hybrid = build_hubspot_forecast(...)
if hybrid:
    payload = {
        "cash_13w": hybrid["cash_13w"],
        "year": hybrid["year"],
        "composition": hybrid["composition"],
        "unmatched_won": hybrid["unmatched_won"],
    }
    payload["plain"] = asyncio.run(_narrate_hybrid(evidence, hybrid))  # LLM prose only
else:
    payload = asyncio.run(_generate(evidence, year))  # existing LLM path
```

`_narrate_hybrid` must receive **precomputed numbers** and only write `plain.cash` / `plain.year` / `plain.watch` — instruct model not to invent alternate totals. Include unmatched won names in the prompt.

Preserve never-raise behavior.

- [ ] **Step 3: Monthly path**

In `qb_forecast_monthly.generate_and_store_current` (or refresh): if hybrid months exist for open months, **set `forecast` to hybrid point** and `method="hubspot_qb_hybrid"`; keep LLM only when HubSpot empty. Past months stay actuals.

- [ ] **Step 4: Test glue with monkeypatched loaders** (optional thin test in `test_hs_forecast.py` or `test_qb_forecast_llm.py`) proving `generate_and_store` stores hybrid year when deals present.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(financial): wire HubSpot hybrid into year cash and monthly forecasts"
```

---

### Task 6: UI composition + sources status

**Files:**
- Modify: `frontend/src/financial/types/quickbooks.ts`
- Modify: `frontend/src/financial/components/QuickBooksPanels.tsx`
- Modify: `backend/app/financial/router.py` (`get_sources_status`)

- [ ] **Step 1: Extend types**

```typescript
composition?: {
  qb_booked: number;
  won_awaiting_invoice: number;
  weighted_open: number;
} | null;
unmatched_won?: { hs_id: number; dealname: string; amount: number }[] | null;
```

under `forecast.llm`.

- [ ] **Step 2: Render breakdown** under Expected income / cash strip / monthly summary — three small lines using existing typography classes (no new card chrome):

`Booked $X · Won awaiting invoice $Y · Weighted pipeline $Z`

Only when `composition` present.

- [ ] **Step 3: Sources status**

```python
from app.leads import hubspot as hs
hs_ok = hs.configured() and _hs_deals_synced()  # SELECT count/limit 1 from hs_deals
# HubSpot CRM: status Connected / active_data True when hs_ok
```

- [ ] **Step 4: Manual UI check** on Forecast tab after a sync + forecast regen.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(financial): show HubSpot forecast composition and source status"
```

---

### Task 7: End-to-end verification checklist

- [ ] **Step 1: Sync deals** — `POST /api/v1/leads/hubspot/sync` `{"mode":"full"}` → `deals_upserted` ≈ 90+.

- [ ] **Step 2: Trigger QB nightly path or forecast regenerate** so `ai_insights` gets hybrid payload.

- [ ] **Step 3: Confirm**
  - Year point ≈ QB YTD + unmatched won + weighted open (spot-check against HubSpot UI).
  - Orange bars ≪ raw face amounts; order-of-magnitude matches ~weighted 13w book.
  - Closed Lost deals absent from composition.
  - With `HUBSPOT_API_KEY` unset / empty `hs_deals`, Forecast still loads (LLM/QB path).

- [ ] **Step 4: Final commit** only if checklist fixes were needed.

---

## Spec coverage (self-review)

| Spec requirement | Task |
|------------------|------|
| Deal sync + stage catalog | 1–2 |
| Stage % + Zo fallback | 3 |
| Year hybrid | 3, 5 |
| Monthly hybrid + year = sum | 3, 5 |
| Stale open close → as_of month | 3 |
| 13w orange from HS; AR/outflow QB | 3, 5 |
| Close ≠ cash (lag + curve) | 3 |
| Won until invoiced; ≥80% match | 4 |
| Unmatched won kept + flagged | 4–6 |
| Python owns figures; LLM narrative | 5 |
| UI composition | 6 |
| Sources HubSpot active | 6 |
| Degrade QB-only | 5, 7 |
| No MRR; no writeback; no manual link UI | out of scope (honored) |

## Placeholder / consistency check

- Constants named once in Task 3 (`INVOICE_LAG_DAYS`, `WON_MATCH_*`, `STAGE_FALLBACK`).
- `composition` / `unmatched_won` keys shared across backend payload and frontend types.
- No TBD steps remaining.
