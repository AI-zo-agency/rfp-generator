# RFP two-agent demo

Client-call demo for Phase 2 Intelligence hops 1–2 only:

1. **Agent 1** — `opportunity_extract` (read RFP → structured opportunity)
2. Human **approve**
3. **Agent 2** — `strategy_delivery` (win strategy + delivery; **real Supermemory KB**)

Same production functions as `backend/app/services/proposal_intelligence/merged_passes.py`. Prompts live in this folder so you can edit them mid-call.

## Prerequisites

- Repo checkout on this branch
- Root `.venv` already set up (`backend/requirements.txt` installed)
- `backend/.env` with `OPENROUTER_API_KEY`, `SUPERMEMORY_API_KEY`, etc. (same as the main app)

## Run (from this folder only)

```bash
cd demo/rfp-two-agents
../../.venv/bin/python server.py
```

Open **http://127.0.0.1:8765**

You do **not** need the main Next.js frontend or the normal `uvicorn app.main` API.

`app` is a symlink to `../../backend/app` (plus `pyrightconfig.json`) so `from app…` resolves in the IDE and at runtime.

## Live prompt edits

| File | Agent |
|------|--------|
| `prompts/agent1_opportunity_system.txt` | Opportunity extract |
| `prompts/agent2_strategy_delivery_system.txt` | Strategy + delivery |

Edit in the UI (Save prompt) or in the editor — each Run reloads from disk.

## Agent 1 pipeline (demo only) — budget A1 (~$0.50 target)

Does not modify production `merged_passes`.

```
PDF → cheap evidence pack (sections + shall/must harvest + scoring/date scan)
    → ONE Sonnet extraction (tools only if gaps, max ~2)
    → schema validate (free)
    → repair Sonnet call ONLY if triggers fire
```

| Piece | Role |
|-------|------|
| `prompts/agent1_opportunity_system.txt` | Variation A1 — evidence-first, tools only when needed |
| `agent1_tools.py` | `build_evidence_pack`, single extract, conditional repair, provenance |

Repair triggers: schema issues, no compliance items, low confidence, scoring without evidence, mandatory/optional overlap, long RFP, etc.

| Agent 2 | Still production `run_strategy_delivery` + real Supermemory |

## Flow on the call

1. Upload RFP PDF → **Run Agent 1** → show JSON + cost  
2. Client feedback → tweak prompt → re-run Agent 1 if needed  
3. **Approve → unlock Agent 2**  
4. **Run Agent 2** → show strategy/delivery JSON + cumulative cost  

Cost uses the same `llm_call_log` / `get_rfp_cost_breakdown` path as production (per-node USD + tokens).

## Sanity check

```bash
../../.venv/bin/python -c "import server; print('ok', server._load_prompt('agent1')[:40])"
```
