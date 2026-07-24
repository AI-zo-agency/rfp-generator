# Cost Instrumentation + Guarded Model Tiering — Status Report

**Date:** 2026-07-23  
**Scope completed:** Part 1 only (cost & token instrumentation)  
**Scope deferred:** Part 2 (guarded model tiering) and Part 3 final tiering report

---

## Why Part 2 is not done yet

The task explicitly requires:

> Part 2 — Guarded Model Tiering (**do this after Part 1 is merged and has run on at least a few real proposals**)

and

> Every model-tier change must ship with a shadow test, not a blind swap.

No real `llm_call_log` rows from production runs exist yet, so **no nodes were moved to light tier**. Hard-rule nodes (fact-check, drafting, compliance, etc.) were not touched.

---

## Part 1 — What shipped

### Cost logging schema (`llm_call_log`)

| Column | Type | Meaning |
|--------|------|---------|
| `id` | UUID / TEXT | Row id |
| `run_id` | TEXT | One id per `generate_full_proposal` invocation |
| `rfp_id` | TEXT | RFP being generated |
| `node_name` | TEXT | Graph node / draft section label |
| `model` | TEXT | Resolved model string |
| `tier` | TEXT | `heavy` or `light` requested |
| `provider` | TEXT | `gemini` / `openrouter` / `fireworks` |
| `input_tokens` | INT | From provider usage, or char/4 estimate |
| `output_tokens` | INT | Same |
| `cost_usd` | FLOAT | From `llm_pricing.py` table |
| `latency_ms` | INT | Wall time for that provider attempt |
| `tokens_estimated` | BOOL/INT | True when usage was estimated |
| `created_at` | TIMESTAMPTZ/TEXT | UTC timestamp |

**Migration (Supabase):** `backend/supabase/migrations/20260723_llm_call_log.sql`  
**Also added to:** `backend/supabase/schema.sql`, SQLite via `proposal_repository.init_proposal_db` / `llm_call_log.ensure_llm_call_log_table`

**Apply on Supabase before relying on Postgres persistence:**

```sql
-- run contents of 20260723_llm_call_log.sql in the SQL editor
```

### How to query a run

```python
from app.services.llm_call_log import get_run_cost_breakdown, format_cost_breakdown_log

breakdown = get_run_cost_breakdown("<run_id>")
print(format_cost_breakdown_log(breakdown))
# breakdown["total_cost_usd"], breakdown["by_node"], ...
```

`generate_full_proposal` logs this automatically at the end (observability only — does not gate or change the pipeline). Look for log lines:

- `Full proposal pipeline starting for … run_id=…`
- `LLM cost: node=… cost_usd=…`
- `LLM cost summary run_id=…`

### Files touched (instrumentation only)

| File | Role |
|------|------|
| `backend/app/services/llm_pricing.py` | **New** — price table |
| `backend/app/services/llm_call_context.py` | **New** — contextvars |
| `backend/app/services/llm_call_log.py` | **New** — persist + `get_run_cost_breakdown` |
| `backend/app/services/llm.py` | Record usage/cost after each successful call |
| `backend/app/services/proposal_generator.py` | `run_id` + end-of-run summary |
| `backend/app/services/proposal_intelligence/graph.py` | Set `node_name` per intel node |
| `backend/app/services/proposal_intelligence/agent_base.py` | Pass `node_name` into `chat_json` |
| `backend/app/services/sections_agent_log.py` | Set `node_name` for sections nodes |
| `backend/app/services/proposal_draft_llm.py` | Forward instrumentation kwargs |
| `backend/app/services/proposal_drafting_graph.py` | Label draft calls `draft_sections:{id}` |
| `backend/app/services/proposal_repository.py` | SQLite DDL |
| `backend/test_llm_call_log.py` | Unit tests |

**Not changed:** fact-check, hallucination detector, legal attestation gate, manuscript-ready gate, drafting prompts/logic, VERIFY/self-edit stop conditions, pipeline stage order, model tiers.

---

## Part 2 — Checklist when ready (after ≥3 real runs)

1. Pull `get_run_cost_breakdown` / SQL aggregates; confirm largest spend nodes.
2. Shadow-test candidates in order: `retrieval_planner` → WBS/timeline/resource → delivery_parallel → (optional) scope/success.
3. Write `docs/tiering_validation/{node}.md` per node.
4. Only then flip via config map + heavy-tier fallback on schema failure.
5. Publish Part 3 summary with measured savings.

---

## Nodes moved / rejected (Part 2)

| Category | Status |
|----------|--------|
| Moved to light tier | **None** (awaiting real cost data + shadow tests) |
| Considered but rejected | **N/A** — Step 1 not started |
| Estimated cost reduction | **$0** until Part 2 ships |

---

## Ops note

Until the Supabase migration is applied, Postgres inserts log a warning and do not break generation. Local SQLite creates the table automatically on first record.
