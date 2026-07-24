# Architecture Audit — RFP Intake to Proposal Generation

**Date:** 2026-07-23  
**Scope:** Ground-truth map from code in `/Users/mahipatel/ZO-AGENCY`  
**Rule for this pass:** Factual only — no optimization recommendations.

---

## 1. Entry Point & Intake

### How the RFP enters

| Path | Files / functions | Mechanism |
|------|-------------------|-----------|
| Manual UI upload | `frontend/src/components/AddManualRfpModal.tsx` → `POST /api/rfps` → `frontend/src/app/api/rfps/route.ts` → `backend/app/api/v1/rfps.py` `create_manual_rfp` | Multipart: `title`, `client`, `location`, `sector`, `dueDate`, `description`, `pageLimit`, `estimatedValue`, `priority`, optional `pdf` |
| Manual JSON create | Same `create_manual_rfp` | JSON body `ManualRfpCreate` (no PDF in same request) |
| JustWin upsert | `frontend/scripts/justwin-sync/index.ts` `upsertRfpViaBackend` → `PUT /api/v1/rfps/upsert` → `upsert_rfp_endpoint` → `rfp_repository.upsert_rfp` | Metadata by `external_id`; PDF via `POST /rfps/{id}/pdf` |
| PDF replace | `upload_rfp_pdf` in `rfps.py` → `rfp_storage.save_rfp_pdf` | Backend-only; frontend has no POST proxy for PDF |

**Router mount:** `backend/app/api/router.py` includes `rfps.router` and `proposals.router` under `/api/v1`.

**Webhooks:** No webhook handlers found in-repo (search of `*.py` / `*.ts` / `*.tsx`).

**JustWin sync status in code:** `JUSTWIN_SYNC_ENABLED = false` / CLI gate false — paths exist but are disabled in config.

**Proposal generate triggers** (`backend/app/api/v1/proposals.py`):

| Endpoint | Handler |
|----------|---------|
| `POST /{rfp_id}/proposal/generate` | `generate_proposal_endpoint` → `generate_full_proposal` |
| `POST /.../generate/full` | same |
| `POST /.../generate/sections-1-3` | background job → `generate_sections_1_3` |
| `POST /.../phase-2-retrieval` | → `run_phase2_retrieval` |
| `POST /.../phase-3-drafting` | → `run_phase3_drafting` |
| Other phase routes | `_enqueue_pipeline_phase` + `proposal_job_runner.start_proposal_job` |

### Accepted formats & parsing

- **Solicitation file:** PDF only.
  - `rfp_storage.save_rfp_pdf`: rejects if `len(content) < 500` or not `content.startswith(b"%PDF")`.
  - UI accept filter: `application/pdf,.pdf`.
- **Parser:** `pypdf.PdfReader` in `backend/app/services/pdf_text.py`
  - `extract_pdf_text_from_bytes`: page loop, `page.extract_text()`, stops at `max_chars` (default **120_000**).
  - `IMAGE_ONLY_TEXT_THRESHOLD = 100` chars → treated as image-only (no OCR in intake path).
- **DOCX for RFP intake:** not accepted. (KB uploads separately support PDF/Word/etc.)

### Pre-processing / chunking before graphs

There is **no** fixed-size chunk+overlap indexer for the RFP itself. Flow:

1. Extract PDF text (cap **120_000** chars) — `rfp_content.load_local_rfp_text` / `combine_rfp_text`.
2. Build prompt context — `go_no_go_service._build_rfp_context` → `_truncate_rfp_text` → `proposal_rfp_excerpt.build_priority_rfp_excerpt` with `RFP_PROMPT_MAX_CHARS = 50_000`.
3. Priority excerpt strategy (`build_priority_rfp_excerpt`): if over max, keep head (~32% capped 22k) + tail (~22% capped 14k) + regex priority windows (±**3200** chars); merge windows if gap ≤ **500** chars. **Not** uniform sliding-window overlap.

Downstream slice caps (examples):

| Consumer | Cap |
|----------|-----|
| `run_rfp_understanding` | `rfp_context[:100_000]` |
| Intelligence compliance/scope/eval | `[:40_000]` |
| Dynamic section planner | `[:50_000]` + submission excerpt `[:20_000]` |
| `run_proposal_context_agent` | `_RFP_EXCERPT_CHARS = 12_000` |
| Fulfill / Scan RFP | `load_local_rfp_text(..., max_chars=250_000)` |

**Not determinable:** Supermemory’s internal chunk size for *KB* documents (RFP PDFs are not ingested to Supermemory per `rfp_content.py` module docstring).

### Classification before routing

| Stage | Function | What it classifies |
|-------|----------|-------------------|
| Intake metadata | `insert_manual_rfp` / JustWin `mapLeadToRfp` | `client`, `sector` (JustWin hardcodes `"Public Sector"`) |
| Go/No-Go | `go_no_go_service.analyze_rfp` | Fit/worth LLM analysis including `sectorMatch`; gates proposal start |
| Proposal gate | `proposal_common.can_start_proposal` | Requires `rfp.go_no_go in {"go", "review"}` |
| Phase 2 first node | `run_rfp_understanding` | `industry`, `orgType`, `projectType`, `services`, etc. |
| Sections CQ path | `run_proposal_context_agent` | `ProposalContext`: industry, buyerType, `proposalType` ∈ website_redesign\|branding\|campaign\|other |

**No** separate “RFP type → different intake graph” router. `justwin_tab` is stored but not used as a pipeline router in reviewed code.

### Storage

- **Metadata:** Supabase Postgres `rfps` when `use_supabase_db()`, else SQLite (`rfp_repository.init_db`).
- **PDF bytes:** Supabase Storage bucket default `"rfp-pdfs"` (`settings.supabase_rfp_bucket`) as `{rfp_id}/rfp.pdf`, or local `settings.pdf_storage_path / {rfp_id} / rfp.pdf`.
- **Proposal state:** `proposal_drafts`, `proposal_research` (same DB backends).

---

## 2. LangGraph Structure

### Orchestrator (not LangGraph)

**File:** `backend/app/services/proposal_generator.py`  
**Function:** `generate_full_proposal`

Execution order:

1. `generate_sections_1_3` — LangGraph sections
2. `run_phase2_retrieval` — **calls `run_intelligence_graph`**, sets `evidenceCorpus=[]`
3. `run_phase3_drafting` — LangGraph drafting
4. `run_phase3_6_self_edit` — Python loop (`proposal_self_edit_loop.run_self_edit_loop`)
5. `run_phase3_5_budget` — budget/pricing
6. Inline Phase 4: `run_presubmit_review` + `build_proposal_ending_report`
7. `assert_manuscript_ready` (hard gate)

**Checkpoint phases** (`proposal_pipeline_checkpoint.PIPELINE_PHASES`):

```
sections-1-3 → phase-2 → phase-3 → phase-3-6-self-edit → phase-3-5-budget → phase-4-review
```

**Jobs:** `proposal_job_runner.start_proposal_job` + `pipeline_phase` context manager.

**Four LangGraph `StateGraph` builders exist.** Company qualification agents, self-edit, checkpoint, and job runner are **not** LangGraphs.

**Important:** `run_retrieval_graph` (`proposal_retrieval_graph.py`) is **implemented but not called** from production Phase 2; Phase 2 uses intelligence graph instead.

---

### Graph A — Sections 1–3

**File:** `backend/app/services/proposal_sections_graph.py`  
**Builder:** `_build_graph()` → `_SECTIONS_GRAPH`  
**Entry:** `run_sections_1_3_graph` (uses `astream`)  
**Mode switch:** `settings.use_company_qualification_s1` — **default `False`** in `config.py` → **legacy path** unless env overrides.

#### State: `SectionsGraphState`

| Field | Role | Approx size by end (chars ≈ tokens/4) |
|-------|------|----------------------------------------|
| `rfp_id`, `rfp_title`, `rfp_client`, `rfp_sector`, `rfp_location` | Metadata | small |
| `rfp_context` | Combined RFP text | up to ~50k–120k chars (~12k–30k tokens) |
| `page_limit` | Optional int | tiny |
| `brand_voice` | Dict JSON | ~1–3k chars |
| `kb_zo_voice`, `kb_company`, `kb_master_roster`, `kb_bios`, `kb_case_studies` (+ `*_sources`) | KB buckets (legacy path) | each bucket capped **500_000** chars in tools; typical injected less |
| `sections` | Reducer-merged section dicts | grows to tens of KB of markdown |
| `skip_section_{1,2,3}` | Bools | tiny |
| `provider`, `error` | Strings | tiny |
| CQ-only: `company_truth`, `proposal_context`, `prioritized_capabilities`, `section1_plan`, `team_selection`, `evidence_selection`, `section1_editorial_review`, `manuscript_locks` | Structured agent outputs | tens of KB combined |

#### Legacy path (`use_company_qualification_s1=False`) — sequential

| Order | Node | Responsibility | Kind |
|------:|------|----------------|------|
| 1 | `fetch_knowledge_base` | Gather KB buckets + roster | Tool (`gather_proposal_kb_for_sections`, `fetch_master_team_roster`) |
| 2 | `synthesize_proposal_voice` | Brand voice JSON | LLM |
| 3 | `build_section_1` | Five Section 1 subsections | Sequential LLM |
| 4 | `build_section_2` | Team select + bios | LLM + KB tools |
| 5 | `build_section_3` | Evidence select + case studies | LLM + KB tools |

**Parallelism:** none at graph edges; bios/case studies sequential inside nodes.  
**Cycles:** none.

#### CQ path (`use_company_qualification_s1=True`) — sequential

| Order | Node | Responsibility | Kind |
|------:|------|----------------|------|
| 1 | `fetch_proposal_context` | `ProposalContext` | LLM |
| 2 | `fetch_company_truth` | Company facts from KB | LLM + Supermemory |
| 3 | `prioritize_capabilities` | Capability ranking | LLM |
| 4 | `plan_section_1` | Section 1 plan JSON | LLM |
| 5 | `build_section_1_cq` | Section 1 prose | LLM + tools |
| 6 | `select_team` | Team pick | LLM + KB |
| 7 | `build_bios` | Per-member bios | KB + optional LLM extract |
| 8 | `select_evidence` | Case study titles | LLM + KB index |
| 9 | `build_case_studies` | Case study sections | LLM + KB |
| 10 | `join_sections` | Sync no-op | Pure logic |
| 11 | `validate_sections_editorial` | Review-only editorial | LLM (non-fatal, no rewrite) |

**Cycles:** none. Registered nodes `synthesize_proposal_voice`, `build_section_2`, `build_section_3` are **not** on the CQ edge path.

---

### Graph B — Phase 2 Intelligence

**File:** `backend/app/services/proposal_intelligence/graph.py`  
**Builder:** `_build_graph()` → `_INTELLIGENCE_GRAPH`  
**Entry:** `run_intelligence_graph` via `run_phase2_retrieval`

#### State: `IntelligenceGraphState`

| Field | Role | Size note |
|-------|------|-----------|
| `rfp_id`, `rfp_title`, `rfp_client`, `rfp_sector`, `rfp_location` | Metadata | small |
| `rfp_context` | RFP string | up to ~50k–100k chars in early nodes |
| `plan` | Accumulating proposal plan dict | grows large (all planner JSON); order of tens–hundreds of KB |
| `legacy` | Derived legacy fields | medium |
| `provider`, `error` | Status | small |

#### Nodes (linear DAG)

| # | Node | Kind | Notes |
|---|------|------|-------|
| 1 | `rfp_understanding` | LLM | Hard fail → `IntelligenceError` |
| 2 | `compliance_mapping` | LLM | `safe_chat_json` |
| 3 | `scope_analysis` | LLM | |
| 4 | `evaluation_criteria` | LLM | |
| 5 | `success_criteria` | LLM | |
| 6 | `opportunity_strategy` | LLM | Plan-only (no raw RFP) |
| 7 | `delivery_pattern` | LLM + Supermemory | |
| 8 | `delivery_parallel` | **Parallel** 6 LLM planners | methodology, budget, risk, QA, communication, training under `_LLM_SEMAPHORE(2)` |
| 9 | `work_breakdown` | LLM | |
| 10 | `timeline` | LLM | |
| 11 | `resource` | LLM | |
| 12 | `dynamic_section` | LLM | Uses RFP + submission excerpt |
| 13 | `winning_pattern` | LLM + Supermemory | |
| 14 | `section_strategy` | LLM | |
| 15 | `retrieval_planner` | LLM | Plans JIT queries; does **not** execute Supermemory |
| 16 | `assemble` | Pure | `refresh_proposal_memory`, `stamp_metadata` |
| 17 | `validate` | Pure | Readiness/blockers |
| 18 | `derive_legacy` | Pure | Legacy field derivation |

**Cycles:** none.  
**Could parallelize but don’t:** nodes 2–5 are independent of each other after understanding but run sequentially; nodes 9–11 after delivery are sequential.

---

### Graph C — Phase 3 Drafting

**File:** `backend/app/services/proposal_drafting_graph.py`  
**Builder:** `_build_graph()` → `_DRAFTING_GRAPH`  
**Entry:** `run_drafting_graph`

#### State: `DraftingGraphState`

Includes `rfp_*`, plan-derived inputs, `drafted_sections`, `provider`, `error`, `llm_semaphore`.

#### Topology

`START → draft_sections → END` (single LangGraph node).

Inside `_draft_all_sections`:

- `BATCH_SIZE = 1` — sequential section loop
- Per section: `_ensure_jit_evidence` (Supermemory JIT) then `_draft_batch_once` (`chat_json_with_repair`)
- `LLM_CONCURRENCY = 1`

**Cycles:** none in LangGraph.

---

### Graph D — Retrieval (orphan relative to Phase 2)

**File:** `backend/app/services/proposal_retrieval_graph.py`  
**Entry:** `run_retrieval_graph` — **no production caller** from `proposal_generator` Phase 2.

| Node | Kind |
|------|------|
| `analyze_rfp` | LLM |
| `retrieve_round` | LLM query plan + parallel Supermemory |
| `evaluate_coverage` | LLM coverage scores |
| Router `_should_continue_retrieval` | Conditional |
| `build_evidence` | Pure merge |

**Cycle:** `retrieve_round → evaluate_coverage → retrieve_round` while `round < MAX_RETRIEVAL_ROUNDS` (**3**) and any section `coveragePercent < COVERAGE_THRESHOLD` (**85**).

---

### Non-graph loops (orchestrator)

| Loop | File | Cap |
|------|------|-----|
| Self-edit | `proposal_self_edit_loop.run_self_edit_loop` | `MAX_SELF_EDIT_ITERATIONS = 4`, time **480** s, parallel **2**, ≤**3** sections/iteration |
| Presubmit autofix | `proposal_presubmit_autofix.run_presubmit_autofix_loop` | `MAX_ITERATIONS_LLM = 1` |
| Sections 1–3 incomplete retry | `generate_sections_1_3` | **1** graph retry |

---

## 3. Model Usage Per Node

### Provider resolution (`backend/app/services/llm.py`)

**Defaults** (`config.py`):

| Setting | Default |
|---------|---------|
| `openrouter_model` | `anthropic/claude-sonnet-4` |
| `llm_heavy_model` | `""` → falls back to `openrouter_model` |
| `llm_light_model` | `anthropic/claude-haiku-4.5` |
| `gemini_model` | `gemini-2.0-flash-exp` |
| `fireworks_model` | `accounts/fireworks/models/llama-v3p3-70b-instruct` |

**Cascade:** Gemini (if key + flags allow) → OpenRouter (`resolve_llm_model(tier)`) → Fireworks.  
**Timeouts:** `httpx` **180** s.  
**Runtime model string:** overridable via env; **not fixed** from code alone if `.env` differs.

**Not determinable from code:** exact model string on a given deploy; measured input/output token counts per node (no persisted counters — only logged `usage=` from OpenRouter/Fireworks).

### Approximate token guidance

Where code gives character caps, rough tokens ≈ chars/4. Actual usage varies with RFP length and section count.

### Intelligence graph LLM table

| Node | API | Tier (default) | max_tokens | temp | RFP in prompt |
|------|-----|----------------|------------|------|---------------|
| `rfp_understanding` | `chat_json` | heavy | 4096 | 0.1 | `[:100_000]` |
| `compliance_mapping` | `safe_chat_json` | heavy | 3072 | 0.15 | `[:40_000]` |
| `scope_analysis` | `safe_chat_json` | heavy | 2048 | 0.15 | `[:40_000]` |
| `evaluation_criteria` | `safe_chat_json` | heavy | 2048 | 0.15 | `[:40_000]` |
| `success_criteria` | `safe_chat_json` | heavy | 2048 | 0.15 | `[:30_000]` |
| `opportunity_strategy` | `safe_chat_json` | heavy | 3072 | 0.15 | Plan only |
| `delivery_pattern` | `safe_chat_json` | heavy | 2048 | 0.15 | Plan + KB hits |
| delivery_parallel ×6 | `safe_chat_json` | heavy | 1536–2048 | 0.15 | Plan + KB |
| `work_breakdown` / `timeline` / `resource` | `safe_chat_json` | heavy | 1536–2048 | 0.15 | Plan only |
| `dynamic_section` | `safe_chat_json` | heavy | 3072 | 0.15 | `[:50_000]` + submission `[:20_000]` |
| `winning_pattern` | `safe_chat_json` | heavy | 4096 | 0.15 | KB excerpts |
| `section_strategy` | `safe_chat_json` | heavy | 4096 | 0.15 | Plan only |
| `retrieval_planner` | `safe_chat_json` | heavy | 3072 | 0.15 | Plan only |
| `assemble` / `validate` / `derive_legacy` | — | — | — | — | No LLM |

Single call per node (no multi-sample self-consistency). Failures: understanding raises; others degrade via `safe_chat_json`.

### Sections graph (selected)

| Call site | Tier | max_tokens | temp | RFP |
|-----------|------|------------|------|-----|
| `_synthesize_proposal_voice` | heavy | (caller) | 0.4 | `[:12_000]` |
| Team/case selection (legacy) | light | varies | varies | context |
| CQ `proposal_context` | light | 1536 | 0.1 | `[:12_000]` |
| CQ `company_truth` | heavy | 8192 | 0.0 | KB `[:28_000]` only |
| `case_study_builder` | heavy | 3072 | 0.0 | case doc |
| `editorial_validation` | heavy | 4096 | 0.1 | sections ≤100k |

### Drafting graph

| Call | Tier | max_tokens | temp | Context |
|------|------|------------|------|---------|
| `_draft_batch_once` | heavy | 8192 or 12288 | 0.35 | Metadata + `zo_ctx[:6000]` + requirements + evidence (≤1800 chars/excerpt) — **not** full RFP body |
| Truncation retry | heavy | 8192 or 16384 | 0.4 | Same + repair |
| `chat_json_with_repair` | — | — | min(temp, 0.15) on repair | Extra user message on bad JSON |

### Go/No-Go (pre-proposal)

`go_no_go_service.analyze_rfp`: `max_tokens` up to **12_000**, `temperature=0.25`, RFP via priority excerpt (**50_000** chars).

---

## 4. Supermemory / KB Retrieval

### Client

**File:** `backend/app/services/supermemory.py`  
- Base: `https://api.supermemory.ai`  
- Container: `settings.resolved_container_tag` (default `zo-agency`)  
- Filter: exclude `type=rfp` documents from KB search  
- Doc list cache TTL: **60** s  
- `format_search_hits` default `max_chars=12_000`  
- HTTP timeout: **120** s

### Where called

| Flow | Module | Pattern |
|------|--------|---------|
| Sections legacy | `proposal_knowledge_base_tools.gather_proposal_kb_for_sections` | LLM query plan + per-bucket searches (4 buckets parallel; queries sequential; hybrid+chunks per query) |
| Sections CQ | `company_queries.py` + evidence/case tools | **8** fixed company queries `limit=3`; evidence index **4** queries; per selected case `fetch_single_case_study` |
| Phase 2 intel | `proposal_intelligence/retrieval.retrieve_intelligence` | ~**8** planner calls (`limit` 4–6); **not** full corpus build |
| Phase 2 loss lessons | `proposal_loss_lessons.py` | ≤**5** `search_knowledge_base` |
| Phase 3 JIT | `jit_retrieval.retrieve_for_section` | Per section: ≤**5** queries × `limit=6`, stop at **18** hits, return ≤**12** `EvidenceItem`, excerpt **2000** chars |
| Go/No-Go | `_gather_knowledge_context` | Variable parallel queries, `KB_SEARCH_LIMIT=8` |
| Chat improve | `kb_rag_retrieve.retrieve_for_question` | Per message, default `limit=8`, `max_chars=80_000` |
| Client list gate | `load_client_list_registry` | Cached **600** s |

### Key constants (`proposal_knowledge_base_tools.py`)

| Constant | Value |
|----------|-------|
| `SEARCH_CHARACTER_LIMIT` | 500_000 |
| `PROPOSAL_KB_SEARCH_LIMIT` | 50 |
| Per-bucket char caps | 500_000 |
| `search_knowledge_base` default limit | 6 |
| Query planner RFP slice | `rfp_excerpt[:8000]` |
| Single case study | `max_chars=120_000` |

### Caching / reuse

- Legacy sections: KB buckets held in `SectionsGraphState` and reused within that graph.
- Phase 2 → Phase 3: **`evidenceCorpus=[]`** stored after intelligence; Phase 3 relies on **JIT** per section (re-fetch).
- Process caches: Supermemory doc list **60** s; ClientList **600** s.
- **No** shared in-memory cache of bucket text across Phase 2 and Phase 3.

### Calls per proposal run

**Not a fixed number.** Depends on: CQ vs legacy, planned query count, number of sections in dynamic plan, JIT `retrievalPlan` entries, go/no-go path, and chat usage.

**Not determinable without a specific run log:** average injected tokens per call.

---

## 5. Critique / Self-Revision Loops

### A. KB fact-check (once per invocation)

**File:** `proposal_kb_fact_checker.py` — `run_kb_fact_check_pass`  
**Not an iterative score loop.** Parallelism: `FACT_CHECK_SECTION_PARALLEL = 4`, `FACT_CHECK_KB_QUERY_PARALLEL = 4`.  
**Triggered:** start of self-edit; after Sections 1–3 partial persist; Scan RFP step 6; standalone API.  
**Context re-sent:** requirements, RFP ≤45k, KB ≤45k, draft ≤14k, brand voice, anti-hallucination rules.

### B. Self-edit loop

**File:** `proposal_self_edit_loop.py` — `run_self_edit_loop`  
**Entry:** `run_phase3_6_self_edit`

| Constant | Value |
|----------|-------|
| `MAX_SELF_EDIT_ITERATIONS` | 4 |
| `SELF_EDIT_TIME_BUDGET_SEC` | 480 |
| `SELF_EDIT_PARALLEL` | 2 |
| Weak sections / iteration | 3 |
| `TARGET_FLAG_COUNT` | 10 |
| VERIFY dedicated round | max **1** |
| Senior-editor tickets (iter 1) | 3 |

**Triggers:** weak sections (`is_weak_section` in `proposal_section_quality.py`: VERIFY, grammar, score ≥30), submission blockers, compliance gaps, manuscript lock conflicts.  
**Stop:** flag target, time, stall, no improvement, max iterations, locks failed.  
**Context re-sent:** repair brief / auto-repair checklist, locks, anti-dup, **prior draft first 5000 chars**, evidence excerpts — not full manuscript every time.

### C. Editorial validation (CQ only)

**File:** `company_qualification/agents/editorial_validation.py`  
**Node:** `validate_sections_editorial`  
**Single LLM review** — recommendations only; **does not rewrite**; non-fatal.

### D. Presubmit autofix

**File:** `proposal_presubmit_autofix.py`  
`MAX_ITERATIONS_LLM = 1`. Deterministic fixes + optional surgical LLM fix. Then gap finalize → MANUAL FILL flags.

### E. Hallucination detector

**File:** `proposal_hallucination_detector.py`  
Regex/heuristic only; used in `proposal_presubmit_review._scan_hallucinations`. **Not** a revise loop.

### F. Legal attestation gate

**File:** `evidence_trust/legal_attestation_gate.py`  
Converts unverified E-Verify / conflict assertions into locked `[VERIFY]` tags. Applied after self-edit / fact-check. **Human confirmation required** — not auto-cleared.

---

## 6. Output Assembly

### Stitching

Sections live as `ProposalDraft.sections: list[ProposalSection]` (`backend/app/models/proposal.py`).

Merge path:

1. Sections 1–3 graph → `_persist_sections_1_3_partial` / `generate_sections_1_3` merge with existing RFP-mapped tabs.
2. Phase 3 → `_merge_static_with_rfp_sections` for drafted RFP sections.
3. Phase 3.5 → budget content incorporated into draft.
4. Self-edit / fact-check / autofix mutate section `content` in place.

**No final “coherence” LLM over the whole manuscript** as a dedicated assemble node. Ordering for export: `proposal_manuscript.manuscript_sections_for_export` / `manuscript_rank`.

### Export formats

| Format | Generator | API |
|--------|-----------|-----|
| **DOCX** | `proposal_docx_export.build_proposal_docx_bytes` ← `build_manuscript_structured` | `POST .../proposal/export/docx` |
| **Google Doc** | `proposal_google_doc_export.export_proposal_to_google_doc` | `POST .../proposal/export/google-doc` |
| **PDF export** | — | **Not found** |
| **Full Markdown download** | — | **Not found** (markdown used for review/issues/budget internals) |

Export API requires non-empty draft; **does not** call `assert_manuscript_ready`. Full `generate_full_proposal` **does** call `assert_manuscript_ready` (VERIFY must be 0 on mapped sections, budget present, etc.).

---

## 7. Cost & Latency Instrumentation

| Mechanism | Present? | Detail |
|-----------|----------|--------|
| LangSmith / `LANGCHAIN_TRACING` | **No** | No matches in `backend/` |
| Aggregated $/token cost per run | **No** | Not persisted |
| Per-request usage log | **Partial** | `llm.py` logs `usage=` for OpenRouter/Fireworks success; Gemini logs `response_chars` only |
| Intelligence timeline file | **Yes** | `proposal_intelligence/log.py` → `backend/logs/langgraph_intelligence.txt` |
| Sections node elapsed | **Yes** | `sections_agent_log.py` `elapsed_s` per node |
| Standard Python logging | **Yes** | Phase labels in `proposal_generator.py` |

**Flag:** There is **no** code-level cost-per-node or cost-per-run aggregation. Latency is partially logged to files/stdout but **actual dollars and p50/p95 run times are not in-repo artifacts** — not determinable from code alone.

---

## 8. Error Handling & Retries

### LLM layer (`llm.py` `_post_chat`)

| Condition | Behavior |
|-----------|----------|
| HTTP 429 | Up to **4** attempts (`range(4)`), backoff `2**(attempt+1)` seconds |
| 402 / 403 | No retry; break |
| Other ≥400 | Break after failure |
| Gemini | **No** retry loop |
| Invalid JSON after OpenRouter | Fireworks skipped to avoid duplicate spend |
| Fireworks 412 | Process-global `_FIREWORKS_SUSPENDED` |
| `chat_json_with_repair` | One repair user message on parse failure |

Retries **re-call the full HTTP LLM request** for that attempt.

### Graph / phase level

| Site | Behavior |
|------|----------|
| Intelligence `rfp_understanding` | Raises → graph error |
| Other intelligence nodes | `safe_chat_json` → empty/degraded; `_wrap` can continue |
| Drafting batch failure | Per-section retry; else placeholder content |
| Plan-driven empty draft | `_retry_plan_driven_section` once |
| Sections incomplete | One full graph re-run in `generate_sections_1_3` |
| Self-edit patch reject | Revert / log exhausted; no infinite retry |
| Supermemory errors | Usually empty hits / fallback; rarely abort |
| Google Doc export | Rate-limit retries **2** (`proposal_google_doc_export`) |

---

## Appendix A — End-to-end sequence (full generate)

```
Manual/JustWin intake (PDF + metadata)
    → optional Go/No-Go analyze_rfp
    → POST proposal/generate
        → generate_sections_1_3  [LangGraph sections]
        → run_phase2_retrieval   [LangGraph intelligence; evidenceCorpus=[]]
        → run_phase3_drafting    [LangGraph draft + JIT Supermemory]
        → run_phase3_6_self_edit [Python loop ≤4]
        → run_phase3_5_budget
        → run_presubmit_review + ending report
        → assert_manuscript_ready
    → export DOCX / Google Doc (separate API)
```

## Appendix B — Explicit unknowns

1. Deployed `.env` model overrides (may differ from `config.py` defaults).  
2. Exact Supermemory call count / injected tokens for a given RFP (runtime-variable).  
3. Measured $/latency per node or per run (no aggregation instrumentation).  
4. Whether CQ mode (`use_company_qualification_s1`) is enabled in production env (code default **False**).  
5. Supermemory server-side chunking parameters (not in this repo).  
6. OCR path for image-only PDFs (detect only; no OCR library in intake).

---

*Audit generated from repository source on 2026-07-23. No optimization recommendations in this document.*
