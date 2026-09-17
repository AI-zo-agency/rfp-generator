# RFP intelligence demo (outline → generate → Word)

Client-call demo for Phase 2 Intelligence plus a **frozen-outline** full proposal path:

1. Upload an RFP PDF **or** paste a PDF URL  
2. Pipeline runs opportunity → strategy/delivery → strict section list  
3. Review the **section list** (client-approved TOC)  
4. Click **Generate proposal** (does **not** re-run the section planner)  
5. **Download Word** for client review  

Same production runners as `backend/app/services/proposal_intelligence/` and drafting/export as the main app. Prompts for opportunity / strategy live in this folder / Supabase.

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

Agent runs stream **real step progress** over SSE (`text/event-stream`).

You do **not** need the main Next.js frontend or the normal `uvicorn app.main` API.

`app` is a symlink to `../../backend/app` (plus `pyrightconfig.json`) so `from app…` resolves in the IDE and at runtime.

## API

| Endpoint | Role |
|----------|------|
| `POST /api/run` | **Primary** — PDF file *or* `rfp_url` → outline (SSE); auto-saves checkpoint |
| `GET /api/checkpoints` | List saved outlines under `checkpoints/` |
| `POST /api/checkpoints/load` | Restore a frozen outline into session (no re-planner) |
| `POST /api/generate` | Frozen outline → writing briefs → Phase 3 → gated 3.5 → 3.6 (SSE) |
| `GET/POST /api/export/docx?demo_id=` | Word (or ZIP if separate cost file) via `build_export_packets` |
| `GET /api/health` | Models + keys configured |
| `GET/PUT /api/prompts` | Agent 1 / 2 system prompts |
| `GET /api/cost/{demo_id}` | Cumulative spend |

Legacy per-hop routes (`/api/agent1`, `/api/agent2`, `/api/outline`) remain ungated for debugging; the UI calls `/api/run` (or **Load outline** from a checkpoint) then `/api/generate`.

## Outline checkpoints

Every successful `/api/run` writes:

- `checkpoints/{demo_id}.json` — frozen execution plan + section list + RFP text/meta  
- `checkpoints/{demo_id}.pdf` — PDF sidecar (when available)

**Load outline** restores that exact TOC into the session. **Generate proposal** drafts **only those tabs** (no section planner / checklister re-run). Re-running **Run → sections** creates a *new* checkpoint; it does not overwrite an older file’s id.

Files under `checkpoints/` are gitignored (local only).

## Generate path (frozen outline)

After `/api/run`, the session holds the execution plan + RFP text (+ PDF bytes). **Generate** then:

1. Upserts an `rfpda-*` RFP (`goNoGo=go`) and optional PDF  
2. Stamps every outline tab `protectFromCap=true` so Phase 3 lean/cap cannot drop Todd-approved titles  
3. Runs `writing_briefs` + `derive_legacy_fields` → research cache (`outlineMode=strict_rfp`)  
4. Phase 3 drafting → Phase 3.5 budget (**soft-skipped** when cost is absent/ambiguous) → Phase 3.6 senior editor  
5. Marks `draft_ready` so export can download Word  

Does **not** re-run opportunity / strategy / section planner / Align-to-RFP / Complete Scan.

## Live prompt edits

| Store | Agent |
|------|--------|
| Supabase `demo_rfp_agent_prompts` row `rfp-two-agents` (cols `agent1`, `agent2`) | Durable (deploy-safe) |
| `prompts/agent1_opportunity_system.txt` / `agent2_…txt` | Disk fallback + local mirror |

**One-time:** run migration `backend/supabase/migrations/20260914_demo_rfp_agent_prompts.sql` in the Supabase SQL editor. First read/save then seeds/updates that row from the disk files.

Edit in the UI (collapsed “Edit agent prompts”) — **Run auto-saves** both textareas. Outline hops use production module prompts (not editable here).

## Pipeline

```
PDF (upload or URL)
  → Opportunity extract (LangExtract + Sonnet + validators)
  → Strategy + delivery (Supermemory KB)
  → execution_plan → dynamic_section(strict_rfp) → checklister
  → nested section titles
  → [client reviews TOC]
  → Generate: seed RFP → writing_briefs → Phase 3 → 3.5 (gated) → 3.6
  → Download Word
```

Cost uses `llm_call_context` → `llm_call_log` / `get_rfp_cost_breakdown`. Session ids use prefix `rfpda-`.

## Sanity check

```bash
../../.venv/bin/python -c "import server; print('ok', server._load_prompt('agent1')[:40])"
../../.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Manual smoke: OSFM (or similar) PDF → section list → Generate → Download Word. If cost is `ambiguous`, generate still finishes; budget step shows skipped; DOCX has no invented Cost Sheet body.

Redeploy the Railway demo staging service after merging for the live site to pick this up.
