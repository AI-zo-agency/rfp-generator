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

Uvicorn **reload** watches `demo/rfp-two-agents` and `backend/app` — Python edits restart the process automatically (in-memory demo sessions clear on reload). Prompt / HTML edits do not need a process restart.

Agent runs stream **real step progress** over SSE (`text/event-stream`): LangExtract passes, Sonnet normalize, validators, repair, KB/strategy for Agent 2. The UI shows an execution-progress list + step count (not a fake percentage timer).

You do **not** need the main Next.js frontend or the normal `uvicorn app.main` API.

`app` is a symlink to `../../backend/app` (plus `pyrightconfig.json`) so `from app…` resolves in the IDE and at runtime.

## Live prompt edits

| Store | Agent |
|------|--------|
| Supabase `demo_rfp_agent_prompts` row `rfp-two-agents` (cols `agent1`, `agent2`) | Durable (deploy-safe) |
| `prompts/agent1_opportunity_system.txt` / `agent2_…txt` | Disk fallback + local mirror |

**One-time:** run migration `backend/supabase/migrations/20260914_demo_rfp_agent_prompts.sql` in the Supabase SQL editor. First read/save then seeds/updates that row from the disk files.

Edit in the UI or editor — **Run auto-saves** the matching textarea. Each agent loads from Supabase when configured (else disk). No server restart for prompt text.

## Agent 1 pipeline (demo only)

Does not modify production `merged_passes`.

```
PDF → RfpDoc
    → Stage 1: section classification (section_classifier.py)
    → Stage 2: evidence pack + LangExtract harvest (Flash, 4 passes: compliance/eval/facts/SOW)
    → Stage 3: ONE Sonnet normalize + gap fill (tools max ~2)
    → schema validate + deterministic validators (opportunity_validators.py)
    → targeted Sonnet repair ONLY when triggers fire
    → provenance merge (model + pack + LangExtract)
```

| Piece | Role |
|-------|------|
| `langextract_harvest.py` | High-recall grounded extraction via OpenRouter Flash |
| `section_classifier.py` | Section types + template-page detection |
| `opportunity_validators.py` | Post-award/conditional mandatory, scoring guard, complexity rubric, contradictions |
| `agent1_tools.py` | Orchestration, repair triggers, apply to plan |
| `prompts/agent1_opportunity_system.txt` | Normalizer prompt (not a summarizer) |

Optional deps: `pip install -r requirements.txt` (langextract) into repo `.venv`.

### Regression (County RFQ 13180)

No PDF is committed. Point at your local copy:

```bash
export RFP_FIXTURE_PDF=/path/to/RFQ13180.pdf
../../.venv/bin/python run_fixture_compare.py
../../.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

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
