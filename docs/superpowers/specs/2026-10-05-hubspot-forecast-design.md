# HubSpot-Augmented Financial Forecast

**Date:** 2026-10-05  
**Status:** design, awaiting review  
**Scope:** Year revenue + monthly series + 13-week cash (`from_new_billing`)

## Why

The Forecast tab today is QuickBooks-only. Cash and year numbers lean on past
invoices, open AR/AP, and an LLM that invents near-term “not billed yet” income
from history. HubSpot already holds the forward book: open deals, Closed Won,
amounts, close dates, and stage probabilities — but the Lead Finder mirror only
syncs contacts and companies. Deals never reach the forecast engine.

Leadership needs one year number and a cash chart that reflect **booked reality
plus real pipeline**, without double-counting once QuickBooks invoices a win.

## Decisions (locked)

| Choice | Decision |
|--------|----------|
| Approach | Deterministic hybrid: Python owns every money figure; LLM narrates only |
| Booked revenue | QuickBooks is source of truth once invoiced |
| Closed Won not yet invoiced | Count at 100% until a confident QB match drops it |
| Open deals | `amount × HubSpot stage probability`; Zo fallback if % missing |
| Closed Lost | Excluded |
| Weighting source | HubSpot pipeline stage % (live: two pipelines already configured) |
| v1 surfaces | Year + monthly remaining + 13-week orange bars |
| Unchanged | Green “already invoiced”, blue outflows, QB collection curve as timing |

## What HubSpot gives us (verified live)

Private-app token already lists deals and pipelines. Snapshot at design time:
~91 deals; ~40 open (~$4.6M face / ~$621k weighted); ~14 Closed Won this year
(~$920k); ~17 open closing in the next 13 weeks (~$2.4M face / ~$361k weighted).

Pipelines:

1. **New Clients and scope expansions** — Contact 10% → … → Finalizing 90% → Won / Lost  
2. **RFPs \| Govt \| Non Profit** — Opportunity 2% → … → Interview 40% → WON / Lost  

Useful properties: `dealname`, `amount`, `dealstage`, `pipeline`, `closedate`,
`hs_is_closed`, `hs_is_closed_won`, `hs_is_closed_lost`,
`hs_deal_stage_probability`, company associations. `hs_mrr` / `hs_arr` are unused
for Zo today — do not drive cash off them.

**Caveat:** HubSpot close date is a sales/decision date, not cash-in-bank.
Unweighted face amounts must never enter the cash chart.

## Year formula

```
year_point =
  QB booked YTD total income
+ Σ Closed Won (not yet invoiced) with close date in calendar year   × 100%
+ Σ open deals with close date in calendar year × stage_probability
```

## Monthly series

- **Months before `as_of` month:** QB actuals only (do not rewrite history with HubSpot).  
- **`as_of` month and later:**

```
month_M =
  QB booked income in M (if any)
+ Closed Won not yet invoiced with close date in M × 100%
+ open deals with close date in M × stage_probability
```

Open deals whose close date falls in a **past** month but are still open contribute
to the **current (`as_of`) month** (stale close date, still in pipeline).

**Consistency:** `year_point` = sum of monthly points (QB actuals for past months +
hybrid for current/future). The LLM must not emit a competing year total.

## 13-week cash

Keep the three chart series:

| Series | Source |
|--------|--------|
| Coming in — already invoiced | QB open AR (unchanged) |
| Going out | QB bills / outflow model (unchanged) |
| Coming in — not billed yet | HubSpot-driven (this design) |

Cash week rows are **Python-built** (same rule as year/monthly: model narrates only):

| Field | Construction |
|-------|----------------|
| `from_open_invoices` | Open AR scheduled via existing collection-curve helpers |
| `from_new_billing` | HubSpot weighted pipeline (below) |
| `outflow` | Existing QB outflow helpers (`monthly_outflow` / `outflow_shape`) |
| `closing_balance` | Prior close + inflows − outflow |

Orange / `from_new_billing`:

1. Candidates: open deals + Closed Won not yet invoiced (Closed Lost out).  
2. Weight: `amount × stage %` (Won = 100%).  
3. Billing week: `closedate + invoice_lag` (default 7 days; named constant).  
4. Cash week: apply existing QB **collection curve** after the billing week.  
5. Bucket weighted cash into that week’s `from_new_billing`.  

No LLM-invented `from_new_billing`. If HubSpot has no deals in-horizon, orange is
zero (do not fall back to hallucinated history for that series).

## Deal sync

Extend the read-only HubSpot mirror (`backend/app/leads/hubspot.py`):

- Add `deals` to `OBJECTS` with the properties listed above.  
- New table `hs_deals` (and sync-state row for `deals`).  
- Persist or cache pipeline stage catalog (id → label, probability, isClosed).  
- Keep existing cadence: incremental ~15 minutes, full nightly.  
- Associations: company (required for matching); contact optional.

Document deals read scope next to the existing HubSpot config comment. Live token
already returns deals; treat scope documentation as ops hygiene, not a blocker.

## Closed Won ↔ QuickBooks matching

Drop a Won deal from the HubSpot layer once QuickBooks has booked it:

1. Resolve HubSpot company → QB customer (domain / name; reuse client map where
   present; conservative fuzzy name only when unique).  
2. Treat as invoiced when matched customer has invoice(s) totaling **≥ 80%** of
   the deal amount, with invoice dates on/after `closedate` minus 14 days.  
3. If no confident match: **keep** the deal in the HubSpot layer and flag it as
   `unmatched_won` in evidence / narrative. Never silently drop.

No manual link UI in v1.

## Architecture

```
HubSpot API
  → hubspot sync (+ deals)
  → hs_deals + stage catalog

Forecast job (nightly with QB sync / on demand)
  → QB evidence (existing build_evidence)
  → HubSpot pipeline evidence (weighted open, unmatched won)
  → hs_forecast.py: monthly + year + full 13w cash rows (HS orange + QB AR/outflow)
  → LLM narrative only
  → ai_insights / overview merge
  → Forecast tab
```

| Component | Location |
|-----------|----------|
| Deal mirror | `app/leads/hubspot.py` + Supabase migration |
| Pipeline math | New `app/financial/hs_forecast.py` (pure, unit-tested) |
| Integration | Replace LLM money points in `qb_forecast_llm` / monthly path; keep LLM for `plain` narrative |
| UI | `QuickBooksPanels.tsx` ForecastView composition breakdown |
| Sources status | `/financials/sources` marks HubSpot active when deals mirror synced |

## UI

- Keep existing Forecast layout.  
- Under year, monthly, and cash: short composition lines — Booked (QB) · Won
  awaiting invoice (HS) · Weighted open (HS).  
- Leave the “if the year keeps this pace” card as pure QB annualize for contrast.  
- Update HubSpot CRM source row from “Pending” to active when deals data exists.

## Failure modes

| Condition | Behavior |
|-----------|----------|
| No HubSpot key / empty deals | QB-only forecast as today; log clearly |
| Sync failure | Use last good `hs_deals`; narrative notes stale pipeline |
| Missing stage % | Zo fallback: early open 10%, mid 50%, late 80%, won 100%, lost 0% |
| Unmatched Closed Won | Counted; listed as unmatched |
| Nightly math error | Must not fail QB sync; degrade to prior forecast / QB-only |

## Testing

Minimal checks that fail if the design breaks:

1. Stage weighting and Closed Lost exclusion.  
2. Year point equals sum of hybrid months.  
3. Won deal removed once QB match found; retained when unmatched.  
4. Close date → invoice lag → collection curve lands in the expected cash week.  
5. Cash path does not double-add HubSpot orange and LLM new billing.

## Out of scope (v1)

- HubSpot MRR/ARR-driven cash  
- Separate HubSpot-only forecast panel (parallel engine)  
- Manual deal↔invoice linking UI  
- Changing green AR or blue outflow construction  
- Writing back to HubSpot  

## Success criteria

- Forecast year and monthly remaining move when Sonja advances a deal stage or
  updates amount/close date (after sync).  
- Closing Won raises HubSpot layer to full amount; after QB invoices the same
  work, HubSpot layer drops and QB YTD absorbs it with no double count.  
- 13-week orange bars reflect weighted near-term pipeline (order of hundreds of
  thousands when the book looks like the design-time snapshot), not raw multi-
  million face totals.  
- With HubSpot down, Forecast still loads from QB.
