# Demo prompts — Agents 1–3 + Generate (senior editor / QA)

Companion to [`PROMPTS_AFTER_FIRST_3_UNTIL_SECTION_LIST.md`](./PROMPTS_AFTER_FIRST_3_UNTIL_SECTION_LIST.md)
(outline hops **4a–5b**). This file covers every other LLM prompt on the
**rfp-two-agents** demo path: Agents 1–3 → writing briefs → Phase 3–4 → finalize.

Source of truth: production modules under `backend/app/services/` plus demo
`prompts/agent1_*.txt` / `prompts/agent2_*.txt` (editable via `/api/prompts`).

---

## Pipeline map


| # | Hop | Node / agent | Prompt source | On demo path? |
|---|-----|--------------|---------------|---------------|
| **1** | Opportunity extract | `opportunity_extract` | `prompts/agent1_opportunity_system.txt` | Yes |
| **1r** | Opportunity repair | same system | same | Yes (when validators fire) |
| **1qa** | Extraction QA patches | `agent1_tools._QA_PROMPT` | — | **Dead** (never called) |
| **2** | Strategy + delivery | `strategy_delivery` | `prompts/agent2_strategy_delivery_system.txt` | Yes |
| **3** | Execution plan | `execution_plan` | `merged_passes._EXECUTION_SYSTEM` | Yes |
| **4a–5b** | Outline / checklister / authority | — | **See companion doc** | Yes |
| **6** | Writing briefs | `writing_briefs` | `merged_passes._WRITING_SYSTEM` | Yes (Generate seed) |
| **7** | Phase 3 draft sections | `phase3_drafting` | `DRAFT_BATCH_PROMPT` | Yes |
| **8** | Phase 3.5 budget | `phase3_5` | `STAGE3_BUDGET_PROMPT` (+ helpers) | Soft-skip if no cost |
| **9** | Closing narrative tabs | post-budget attach | inline in `proposal_fulfill_rfp_gaps` | Yes (non-form tabs) |
| **10** | Phase 3.6 senior editor | `phase3_6_self_edit` | `SENIOR_EDITOR_SYSTEM` | **Lean default = skip LLM** |
| **11** | Section repair (tickets) | self-edit / empty tabs | `SECTION_REPAIR_SYSTEM` | When tickets / hollow tabs |
| **12** | Phase 4 stub fill | `phase4_presubmit` | `USER_REVISE_SYSTEM` | Hollow required tabs |
| **13** | Money intelligence A/B | Phase 4 | `_PASS_A` / `_PASS_B` | When budget present |
| **14** | Quality gate suite | `run_quality_gate` | claim / eval / consistency / repetition / slop | **Not wired** on this path |
| **15** | Surgical autofix | deep finalize | `SURGICAL_FIX_*` | Not in light Phase 4 |
| **16** | Build finalize | `build_finalize` | various scan LLMs | **Off** unless config on |

**Config that changes what actually runs** (`backend/app/core/config.py`):

- `senior_editor_lean_in_generate=True` → Phase 3.6 skips `SENIOR_EDITOR` / ticket-apply LLM
- `phase4_adversarial_repair=False` → no adversarial repair loop
- `build_finalize_enabled=False` → finalize no-ops
- Quality gate removed from fulfill/scan; `run_quality_gate` has no demo callers

---

## 1. Opportunity extract (Agent 1)

**File:** `demo/rfp-two-agents/prompts/agent1_opportunity_system.txt` (~8k chars)  
Loaded via `prompt_store` / `_load_prompt("agent1")`. Fallback unused on demo:
`merged_passes._OPPORTUNITY_SYSTEM`.

**Role:** Normalize opportunity JSON from the evidence pack (not proposal prose).

### System

Full text lives on disk (too long to paste here). Opening:

```text
You are zö agency's Opportunity Intelligence extraction agent.

Extract structured procurement intelligence from the RFP evidence provided.
…
Return JSON only.
```

### User (template)

```text
RFP meta: {rfp_meta JSON}

EVIDENCE PACK (use this first — do not re-read the whole RFP):
{pack_for_llm JSON[:55000]}

Extract the full opportunity JSON now. You MUST represent LangExtract compliance
candidates in compliance.items (no silent drops). Tools only if a required field
cannot be filled from this pack.
```

### Repair user (when validators fire)

Same system. User:

```text
{_targeted_repair_instruction(triggers)}

Issues: {triggers}

Current JSON:
{cleaned without provenance[:20000]}

Evidence pack (abbreviated):
{pack without sections/pages[:20000]}

Use at most 2 targeted tool calls if needed. Do NOT shrink successCriteria,
compliance, or evaluation.emphasis if already populated.
```

---

## 1qa. Agent 1 extraction QA (dead)

**File:** `demo/rfp-two-agents/agent1_tools.py` → `_QA_PROMPT`  
`_qa_patch_pass` is defined but has **zero call sites**.

### System

```text
You are a procurement extraction QA agent.
Given RFP tool excerpts and a draft opportunity JSON, return ONLY JSON patches:

{
  "patches": [
    {"operation":"replace","path":"/compliance/items/0/mandatory","value":false,"reason":"..."}
  ],
  "conditionalityErrors": [],
  "quantityMismatches": [],
  "contradictionsNotFlagged": [],
  "schemaErrors": []
}

Do NOT regenerate the whole document. Prefer empty patches when correct.
Paths use JSON Pointer. Allowed operations: replace, add, remove.
Focus on: conditionality, quantity fidelity, successCriteria pollution, unflagged contradictions.
```

### User (template)

```text
Draft JSON:
{payload[:20000]}

RFP excerpts:
{excerpt[:8000]}
```

---

## 2. Strategy + delivery (Agent 2)

**File:** `demo/rfp-two-agents/prompts/agent2_strategy_delivery_system.txt` (~14k chars)  
Injected into `merged_passes._STRATEGY_DELIVERY_SYSTEM` via `_apply_prompts()`.

**Role:** Win theme + delivery / methodology / budget / risk / QA plans (JSON).

### System

Full text on disk. Opening: `You are zö agency's Strategy + Delivery Intelligence agent.`

### User (template)

```text
Understanding:
{plan.opportunity.understanding JSON}
Scope:
{plan.opportunity.scope JSON}
Evaluation:
{plan.opportunity.evaluation JSON}
Success:
{plan.opportunity.success_criteria JSON}
Budget intel:
{understanding.budget_intel JSON}

Won-proposal pattern excerpts (patterns only):
{won_excerpts[:12000]}

Methodology intel:
{method_hits[:8000]}

Pricing knowledge:
{price_hits[:8000]}

Playbook/standards intel:
{playbook_hits + qa_hits[:8000]}
```

---

## 3. Execution plan (Agent 3)

**File:** `backend/app/services/proposal_intelligence/merged_passes.py` → `_EXECUTION_SYSTEM`

### System

```text
You are zö agency's Execution Planner.
Decompose delivery into work packages, timeline, and role allocations.
Return JSON ONLY:
{
  "workBreakdown": {
    "packages": [{"workPackage": "string", "phase": "Discovery", "deliverables": ["string"]}],
    "confidence": 0.0
  },
  "timeline": {
    "milestones": [{"name": "string", "offset": "Week 2", "dependsOn": ["string"]}],
    "goLive": "string",
    "reviewCycles": "string",
    "confidence": 0.0
  },
  "resources": {
    "allocations": [{"role": "string", "allocationPct": null, "phase": "string"}],
    "confidence": 0.0
  }
}
Keep output COMPACT so the JSON always completes:
- At most 10 work packages; ≤3 short deliverable phrases each
- At most 8 milestones; short names/offsets only
- Role allocations only — no prose paragraphs
No proposal prose. Do not invent named people — roles only.
Do NOT invent allocationPct / percent-time / FTE figures — leave allocationPct null
unless the RFP explicitly states a required %. Never copy static 10/35/25 grids.
```

### User (template)

```text
Scope:
{scope JSON}
Timeline intel:
{understanding.timeline_intel JSON}
Methodology:
{delivery.methodology JSON}
Delivery model:
{delivery.delivery_model JSON}
Budget roleEffort:
{delivery.budget JSON}
```

---

## 4a–5b. Outline window

Documented in [`PROMPTS_AFTER_FIRST_3_UNTIL_SECTION_LIST.md`](./PROMPTS_AFTER_FIRST_3_UNTIL_SECTION_LIST.md):

Align extract → dynamic section planner (`strict_rfp`) → closing ledger →
missing-submittals (×2) → checklister → submission authority → **section list UI**.

---

## 6. Writing briefs (Generate seed)

**File:** `merged_passes.py` → `_WRITING_SYSTEM`  
**When:** `_seed_demo_rfp_for_generate` after TOC freeze.

### System

```text
You are zö agency's Writing Intelligence agent.
For EACH outline section produce: a writing pattern, a writer brief, and a retrieval plan.
Do NOT write proposal prose. Do NOT return excerpts, quotes, or rewritten sentences.

HARD RULES (writer briefs):
- Each section has ONE distinct job — purpose must NOT overlap another section's purpose.
- Prefer wordBudget 250–500. When a page limit is given, SUM of wordBudgets must fit
  (~350 words/page), leaving room for static Sections 1–3. wordBudget is a HARD CEILING.
- ATTACHMENT / form-return tabs: wordBudget 80–120. writerInstructions must tell the writer
  to emit ONLY a short checklist plus [DESIGNER NOTE: Attach <file>] / [MANUAL FILL: attach …].
- writerInstructions MUST say: do not rehash other tabs; add only NEW RFP-specific detail;
  no generic agency marketing filler; hit the scored ask then stop.

Retrieval queries: ONE natural-language question per section (human KB style), not fragments.

Return JSON ONLY:
{
  "patterns": [ { "sectionId": "…", "openingPattern": "…", … } ],
  "plans": [ { "sectionId": "…", "purpose": "…", "writerInstructions": "…", "wordBudget": 500, … } ],
  "entries": [ { "sectionId": "…", "queries": ["…"], … } ],
  "confidence": 0.0
}
```

### User (template)

```text
{page_limit_line}

Opportunity:
{understanding JSON}
Outline:
{proposal_outline JSON}
Strategy:
{strategy JSON}
Evaluation:
{evaluation JSON}
Success:
{success_criteria JSON}
Delivery methodology:
{methodology JSON}
Proof strategy:
{proof_strategy}

Won proposal excerpts for pattern extraction only.
Do not return or paraphrase their text:
{excerpts JSON}
```

---

## 7. Phase 3 drafting

**File:** `backend/app/services/proposal_drafting_graph.py` → `DRAFT_BATCH_PROMPT`  
(~19–22k chars after `ANTI_RFP_ECHO_RULES` + Ralph inject). Too long to paste.

**Role:** Batch-write section prose JSON with anti-hallucination + anti-RFP-echo rules.

### System

See constant `DRAFT_BATCH_PROMPT` in source. Opening:

```text
You draft zö agency proposal section content for a government/commercial RFP response.

## CRITICAL: ANTI-HALLUCINATION RULES (ENFORCE STRICTLY) — DO NOT RELAX THESE
…
```

### User (shape)

Multi-zone payload (not a single short template):

- **Zone A (cached):** client / RFP / sidebar titles / locks / avoidances / brand voice / …
- **Zone B:** prior drafted sections
- **Zone C:** batch — per section `sectionId`, `title`, `register`, requirements, wordTarget, evidence, planContext + policy stanzas

Retry path: same system + compact “fix truncated JSON” user.

Related post-batch LLMs (when wired): KB fact-check (`QUERY_PLANNER_SYSTEM` + inline),
manuscript fact contradictions, sidebar title clean, structure reframe
(`proposal_fulfill_rfp_structure.py`).

---

## 8. Phase 3.5 budget

**Primary:** `proposal_pricing_service.py` → `STAGE3_BUDGET_PROMPT` (~25k+ with playbook)  
**Helpers:** `judge_rfp_budget_format._SYSTEM`, `_MINIMUM_REPAIR_SYSTEM`,
`FEE_SLOT_PLAN_PROMPT`, `FEE_GROUNDING_CHECK_PROMPT`, optional `STAGE3A_GROUNDING_PROMPT`.

### User (shape) — main budget call

Assembled blocks: Go/No-Go, map, pricing guide, rate card, RFP excerpt[:28k],
cost demands, optional fixed-instrument note.

Full systems stay in those modules (too long for this index).

---

## 9. Closing narrative (post-budget attach)

**File:** `proposal_fulfill_rfp_gaps.py` → `_draft_closing_section` (inline system ~2k)

### User (shape)

Closing excerpt + insurance/KPI excerpts + component meta → one narrative closing tab
(forms stay template stubs).

---

## 10. Phase 3.6 Senior Editor

**File:** `backend/app/services/proposal_langchain_agents.py` → `SENIOR_EDITOR_SYSTEM`  
**Caller:** `senior_editor_emit_tickets` / `run_phase3_6_self_edit` → `run_self_edit_loop`

**Demo default:** lean generate → **empty tickets, no LLM emit**.

### System

```text
You are zö agency's Senior Proposal Editor — the final manuscript director.
You read the FULL TABLE OF CONTENTS against THIS RFP, then emit a few surgical tickets.
You do NOT fill [VERIFY] tags or invent KB facts — the KB fact-checker owns facts.
You do NOT rewrite the book yourself. You do NOT hunt grammar.

HARD RULE — NEVER DELETE A TOC TAB:
- Every section id in the digest TOC stays in the proposal. …
- deleteSectionTickets MUST always be []. …
…

Your ONLY jobs for ONE pass:
1. TOC vs RFP — empty/stub scored tabs → coverageTicket
2. DEDUPE (trim, never drop) → dedupeTicket
3. COMPACT FORMAT — format only, never drop asks
4. GOV / BUYER COMPLIANCE → complianceTicket
5. BUDGET CROSS-SECTION (notes[] only)
6. No style/tone polish tickets
7. ANTI-RFP-ECHO in rewriteBrief / trimGuidance

Return ONLY JSON:
{"deleteSectionTickets":[],
 "dedupeTickets":[…],
 "compactFormatTickets":[…],
 "coverageTickets":[…],
 "complianceTickets":[…],
 "notes":[]}
```

(Full text in source — ~3.2k chars.)

### User (template)

```text
Client: {rfp_client}
RFP: {rfp_title}

DIRECTOR RULES: Keep every TOC tab. Overlap → dedupeTicket (trim + cross-ref),
never deleteSectionTickets. Empty/stub scored tabs → coverageTicket.
Missing mandatory forms/attestations THIS RFP names → complianceTicket.
Few high-value tickets only.

Mapped requirements by section:
{sid}:
  - {req}
  …

Proposal manuscript digest:
{digest[:40000]}
```

---

## 11. Section Repair

**File:** `proposal_langchain_agents.py` → `SECTION_REPAIR_SYSTEM` (~4.5k)  
Used when senior-editor tickets apply, empty-tab generate, or quality-gate Act 3.

### User (template)

```text
Client: {rfp_client}
RFP: {rfp_title}
Section: {title}
Word target: {word_target}
Requirements:
- {req}
…

Repair task:
{message}

{budget_context?}
{locks_brief}
{dedup_brief}

Previous draft:
{content[:5000]}

Evidence corpus (cite as [E#]):
{evidence_block or "(search tools for more)"}
```

Empty-tab generate uses the same shape with `Previous draft:\n(empty)`.

---

## 12. User Revise (Phase 4 stub fill / chat)

**File:** `USER_REVISE_SYSTEM` in `proposal_langchain_agents.py` (~5.1k)

**On demo Generate:** hollow required tabs in Phase 4 via `improve_proposal_section`.

### User (shape)

Large composed block: verbatim ask + RFP excerpt + coverage checklist + open-tab-only
constraints + other sections for consistency (do not rewrite them).

---

## 13. Phase 4 money intelligence

**File:** `proposal_money_intelligence.py` → `_PASS_A_PROMPT`, `_PASS_B_PROMPT`

- Pass A: triage `$` hits outside budget tables  
- Pass B: budget narrative / math integrity  

Deterministic presubmit checklist; manuscript auditor attaches with `use_llm=False`.

---

## 14. Quality gate suite (QA agents — not on demo Generate)

**Module:** `proposal_quality_gate.py` + roles in `proposal_langchain_agents.py`  
**Status:** former fulfill step 17 removed; **not invoked** by `_run_generate_proposal`.

Documented here because these are the “QA prompts” people ask about.


| Role | Constant | User (template) |
|------|----------|-----------------|
| Claim verifier | `CLAIM_VERIFIER_SYSTEM` | `Verify the fact-bound claims…` + per-section text + evidence blocks |
| Evaluator | `EVALUATOR_SYSTEM` | `Client/RFP` + RFP TEXT[:24k] + weights + `PROPOSAL:` digest |
| Consistency | `CONSISTENCY_AUDITOR_SYSTEM` | Whole-manuscript digest |
| Repetition | `REPETITION_AUDITOR_SYSTEM` | Whole-manuscript digest |
| Slop | `SLOP_AUDITOR_SYSTEM` | Scoped digest |
| Repair | `SECTION_REPAIR_SYSTEM` | Gate ticket instruction + section |

### Claim verifier — System (abbrev)

```text
You extract fact-bound claims from a proposal section and
judge each against the knowledge-base evidence provided.
… status: verified | contradicted | unresolved …
Return JSON only: {"claims":[…]}
```

### Evaluator — System (abbrev)

```text
You are an RFP evaluator scoring a proposal the way the issuing agency will score it.
score is 0-5 …
Return JSON only: {"verdicts":[…]}
```

### Consistency / Repetition / Slop

Full systems in `proposal_langchain_agents.py` (short; return `conflicts` / `repeats` / `findings`).

---

## 15. Surgical fix (deep path only)

**Systems:** `SURGICAL_FIX_SYSTEM` (langchain) + `SURGICAL_FIX_PROMPT` /
`PROCUREMENT_FIX_PROMPT` in `proposal_presubmit_autofix.py`  

Not called by light demo Phase 4.

---

## 16. Shared query planner

**File:** `QUERY_PLANNER_SYSTEM` — plans 2–4 Supermemory queries (zö-only; never buyer name).  
Used by fact-check / section editor paths, not as a standalone demo step.

### Return

```text
{"queries":["query 1","query 2","query 3","query 4"]}
```

---

## 17. Manual-fill triage

**File:** `MANUAL_FILL_TRIAGE_SYSTEM`  
Criticality: `disqualifying` | `scored` | `optional` with verbatim `rfpEvidence`.  
Build-finalize / scan path when enabled.

---

## Quick map for a client doc

1. **Agent 1** — Extract opportunity JSON from RFP evidence  
2. **Agent 2** — Strategy + delivery plans  
3. **Agent 3** — WBS / timeline / resources  
4. **Outline (4a–5b)** — Tab list → [companion doc](./PROMPTS_AFTER_FIRST_3_UNTIL_SECTION_LIST.md)  
5. **Writing briefs** — Per-tab writer instructions (no prose)  
6. **Phase 3** — Draft section prose (`DRAFT_BATCH_PROMPT`)  
7. **Phase 3.5** — Budget JSON from pricing guide  
8. **Senior editor** — Ticket pass (often skipped in lean generate)  
9. **Section repair / User revise** — Patch one tab  
10. **Quality gate QA** — Claim / eval / consistency / repetition / slop (**not wired** on demo)

---

## Related index

Repo-wide file inventory (paths only, not full prompt text):
[`docs/PROMPTS_AND_GUARDRAILS.md`](../../docs/PROMPTS_AND_GUARDRAILS.md)
