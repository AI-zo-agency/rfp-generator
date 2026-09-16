# RFP two-agent demo (+ strict section list)

Client-call demo for Phase 2 Intelligence:

1. **Agent 1** — `opportunity_extract` (read RFP → structured opportunity)
2. Human **approve**
3. **Agent 2** — `strategy_delivery` (win strategy + delivery; **real Supermemory KB**)
4. Human **approve**
5. **Build section list** — `execution_plan` → `dynamic_section` (`strict_rfp`) → `checklister` → titles only

Same production runners as `backend/app/services/proposal_intelligence/`. Prompts for Agents 1–2 live in this folder / Supabase.

## Prerequisites

- Repo checkout on this branch
- Root `.venv` already set up (`backend/requirements.txt` installed)
- `backend/.env` with `OPENROUTER_API_KEY`, `SUPERMEMORY_API_KEY`, `SUPABASE_*`, etc. (same as the main app)

## Run (from this folder only)

```bash
cd demo/rfp-two-agents
../../.venv/bin/python server.py
```

Open **http://127.0.0.1:8765**

Uvicorn **reload** watches `demo/rfp-two-agents` and `backend/app` — Python edits restart the process automatically (in-memory demo sessions clear on reload). Prompt / HTML edits do not need a process restart.

Agent runs stream **real step progress** over SSE (`text/event-stream`). The UI shows an execution-progress list + step count (not a fake percentage timer).

You do **not** need the main Next.js frontend or the normal `uvicorn app.main` API.

`app` is a symlink to `../../backend/app` (plus `pyrightconfig.json`) so `from app…` resolves in the IDE and at runtime.

## Live prompt edits

| Store | Agent |
|------|--------|
| Supabase `demo_rfp_agent_prompts` row `rfp-two-agents` (cols `agent1`, `agent2`) | Durable (deploy-safe) |
| `prompts/agent1_opportunity_system.txt` / `agent2_…txt` | Disk fallback + local mirror |

**One-time:** run migration `backend/supabase/migrations/20260914_demo_rfp_agent_prompts.sql` in the Supabase SQL editor. First read/save then seeds/updates that row from the disk files.

Edit in the UI or editor — **Run auto-saves** the matching textarea. Each agent loads from Supabase when configured (else disk). No server restart for prompt text.

Hops 3–5 (outline) use production module prompts (not editable in this demo).

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

| Agent 2 | Production `run_strategy_delivery` + real Supermemory |
| Outline | Production `run_execution_plan` + `run_dynamic_section_planner(strict_rfp)` + `run_proposal_checklister` |

## Flow on the call

1. Upload RFP PDF → **Run Agent 1** → show JSON + cost  
2. Client feedback → tweak prompt → re-run Agent 1 if needed  
3. **Approve → unlock Agent 2**  
4. **Run Agent 2** → show strategy/delivery JSON + cumulative cost  
5. **Approve → unlock section list**  
6. **Build section list** → nested titles only (`writing.proposalOutline.sections`) + cumulative cost  

Cost uses `llm_call_context` → `llm_call_log` / `get_rfp_cost_breakdown` (per-node USD + tokens). The same Supabase `llm_call_log` rows feed the main app sidebar **AI / Proposals** spend meter (not Finance). Outline nodes: `execution_plan`, `dynamic_section`, `checklister`.

Session ids use the prefix `rfpda-` (not `demo-`) so production `llm_call_guards` does not treat them as ephemeral test burns.

Redeploy the Railway demo staging service after merging for the live site to pick this up.

## Sanity check

```bash
../../.venv/bin/python -c "import server; print('ok', server._load_prompt('agent1')[:40])"
```
