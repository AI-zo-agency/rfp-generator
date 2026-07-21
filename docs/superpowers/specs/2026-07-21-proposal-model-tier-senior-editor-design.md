# Proposal Model Tiers + Senior Editor Redesign — Design Spec

**Date:** 2026-07-21  
**Status:** Approved for implementation  
**Approach:** A — Role-tier router (`llm_heavy_model` / `llm_light_model`)  
**Priority:** Cost first, quality on heavy paths; zero cross-section corruption

## Problem

1. **One model for everything.** Almost all proposal LLM calls use a single Sonnet-class model (`openrouter_model`). Cheap steps (query planning, weak-section scoring, classify/route) burn heavy tokens.

2. **Senior Editor overlaps KB fact-checker.** Phase 3.6 Senior Editor still hunts VERIFY/facts via tools, while KB fact-checker already owns post-section fact repair. Duplicate work, higher cost, muddled ownership.

3. **Duplication + RFP gaps are editorial jobs, not fact jobs.** Who We Are content reappears in other sections; sections miss mapped RFP requirements. Senior Editor should detect and dispatch — not invent facts.

4. **Past bug: cover letter / neighboring sections disturbed.** Any redraft path that rewrites more than the requested section (or merges whole-manuscript patches) is unacceptable — especially on costly Sonnet calls.

## Goals

- Route **heavy** work to Sonnet, **light** work to Haiku (config-driven).
- Senior Editor: read proposal + RFP → smart dedupe tickets + RFP coverage tickets → dispatch **Phase 3 drafting path for that section only**.
- KB fact-checker remains sole fact owner; run after every drafted/redrafted section with cost-aware queries.
- **Hard isolation:** a ticket for section X may only mutate section X’s content (and its own metadata). Cover letter, bios, budget, etc. must stay untouched unless they are the ticketed section.

## Non-goals

- Replacing the full pipeline graph topology.
- Making Haiku write evaluator-facing narrative.
- Removing Phase 4 pre-submit / surgical fix (unchanged except model tier).
- Multi-section “batch rewrite” from Senior Editor.

---

## Current agentic loops (baseline)

```text
Phase 1   Sections 1–3
Phase 2   Intelligence / research
Phase 3   Draft RFP sections
            └─ after batches → KB fact-checker (targeted section ids)
Phase 3.5 Budget
Phase 3.6 Self-edit loop
            Senior Editor → patch instructions
            Section Repair → rewrite weak sections
Phase 4   Pre-submit / autofix / Scan RFP (+ fact-check)
```

Named roles today (`AgentRole`): `SENIOR_EDITOR`, `SECTION_REPAIR`, `USER_REVISE`, `SURGICAL_FIX`, `QUERY_PLANNER` — all share one model.

---

## Design

### 1. Role-tier model router

**Config** (`backend/app/core/config.py` + env):

| Setting | Example | Meaning |
|---------|---------|---------|
| `llm_heavy_model` | `anthropic/claude-sonnet-4` | Quality-critical |
| `llm_light_model` | `anthropic/claude-haiku-4.5` | High-volume cheap |
| `openrouter_model` | (existing) | Fallback = heavy if `llm_heavy_model` unset |

**API surface:**

- `chat_json(..., tier: Literal["heavy","light"] = "heavy")`
- `get_chat_model(tier=...)`
- Agent profiles / fact-check steps declare a tier; router resolves model id.

**Tier map**

| Step | Tier |
|------|------|
| Phase 2 intelligence / research / strategy / compliance mapping | heavy |
| Phase 3 section writers (original drafting path) | heavy |
| Senior Editor judgment + ticket emission | heavy |
| Ticketed section redraft (Phase 3 path, one section) | heavy |
| Budget narrative | heavy |
| User Revise / Surgical Fix prose | heavy |
| Query planner | light |
| Weak-section / gate / “ticket cleared?” checks | light |
| Classify / route / short JSON extract helpers | light |
| Deterministic VERIFY / bio Work History fills | **no LLM** |

If light model unset → use heavy (safe degradation).

---

### 2. Ownership split

| Owner | Owns | Must not |
|-------|------|----------|
| **KB fact-checker** | Facts, `[VERIFY]` fills, KB-backed repairs after write/redraft | Manuscript dedupe strategy, RFP coverage tickets |
| **Senior Editor** | Manuscript + RFP read; smart dedupe; coverage map; emit tickets; dispatch writers | Invent/verify facts; rewrite multiple sections; patch cover letter when ticket is elsewhere |

Senior Editor system prompt **drops** “hunt VERIFY / fill facts” as a primary job. Fact gaps → leave for fact-checker (or emit a coverage ticket if RFP requires content that is missing entirely).

---

### 3. Senior Editor redesign (Phase 3.6)

#### Inputs

- Full draft (all section titles + content; truncate long sections with stable headers if needed).
- RFP context + mapped `rfp_sections` requirements per section id.
- Optional short “home section” excerpts for Who We Are / team / cases (for dedupe decisions).

#### Outputs (JSON only)

```json
{
  "dedupeTickets": [
    {
      "sectionId": "…",
      "keepHomeSectionId": "section-1-who-we-are",
      "trimGuidance": "Keep one cross-ref sentence; drop repeated firm history; keep section-specific RFP asks"
    }
  ],
  "coverageTickets": [
    {
      "sectionId": "…",
      "unmetRequirements": ["…"],
      "rewriteBrief": "Address these RFP points; do not expand into Who We Are"
    }
  ],
  "notes": []
}
```

#### Dedupe rule (smart trim)

- Detect repeated Who We Are / brand story / FEIN / full bios / full case dumps in non-home sections.
- **Do not blank** just because content exists elsewhere.
- Trim to what **that** section needs for its RFP job + one short cross-reference when useful.

#### Coverage rule

- Map each section to RFP requirements / RFP-named sections.
- If unmet → **coverage ticket** only (no Senior Editor prose fill).

#### Dispatch (approved choice)

- **Coverage tickets** → **Phase 3 drafting path** (original writer + evidence corpus for that section).
- **Dedupe-only tickets** → same Phase 3 path with `rewriteBrief` = trim guidance (still one-section write), **or** a constrained Section Repair pass if cheaper — default to Phase 3 path for consistency unless implementers prove Repair is safer for trim-only.

#### Hard isolation (non-negotiable)

When redrafting section `S`:

1. Call Phase 3 writer with **only** `S` in scope (single-section draft API / graph node).
2. Persist by replacing **only** `draft.sections[i]` where `id == S`.
3. Assert before save: every other section’s `content` hash/length unchanged.
4. No manuscript-wide merge, no “also fix cover letter while we’re here,” no sibling patches in the same call.
5. After success → KB fact-check **only** `S` (`only_section_ids=[S]`).

This prevents the historical cover-letter / neighbor corruption class of bugs and avoids wasting Sonnet tokens on unsolicited rewrites.

#### Loop / caps

```text
Senior Editor (heavy) → tickets
for each ticket (bounded, prioritize critical coverage):
  Phase 3 single-section draft (heavy)  # section S only
  KB fact-check S (cost-aware)
Haiku gate: ticket cleared? if no → at most 1 more heavy rewrite of S
Stop: tickets cleared | max iterations | time budget
```

Parallelism: at most `SELF_EDIT_PARALLEL` tickets, each still single-section isolated.

---

### 4. KB fact-checker (cost-aware, keep after every section)

Runs after Phase 3 batches and after each ticketed redraft.

```text
per section S:
  if no [VERIFY] and requirements look covered → skip LLM agent
  light Query Planner → 2–4 queries (VERIFY fields + mapped reqs only)
  retrieve (prefer named docs / 04_Bio; avoid spray)
  deterministic fills first
  heavy repair agent only if still open
  never rewrite other sections
```

Bio path stays subsection-scoped (Work History etc.) so Key Accounts are not clobbered.

---

### 5. Files likely touched (implementation later)

- `backend/app/core/config.py` — heavy/light model settings
- `backend/app/services/llm.py` — tier routing in `chat_json`
- `backend/app/services/proposal_langchain.py` — `get_chat_model(tier=)`
- `backend/app/services/proposal_langchain_agents.py` — Senior Editor prompt + tier on profiles; dispatch API
- `backend/app/services/proposal_self_edit_loop.py` — ticket loop + single-section Phase 3 call + isolation assert
- `backend/app/services/proposal_drafting_graph.py` / `proposal_generator.py` — export single-section redraft entrypoint
- `backend/app/services/proposal_kb_fact_checker.py` — light planner / skip-when-clean / tier on repair

No frontend required for v1 (backend behavior).

---

## Success criteria

1. Light-tier calls use Haiku (or configured light model); heavy use Sonnet.
2. Senior Editor tickets never mutate non-ticketed sections (assert in tests).
3. Coverage redraft uses Phase 3 writer + evidence for that section only.
4. KB fact-checker still runs after section writes; skips wasteful agent when clean.
5. Dedupe trims with guidance, not blind deletion of shared topics.
6. Cover letter / unrelated sections unchanged when repairing e.g. a mid-proposal section.

## Test plan (for implementation)

- Unit: tier resolver returns heavy/light model ids from settings.
- Unit: isolation assert fails if a mock writer changes two sections.
- Unit: Senior Editor prompt/contract rejects fact-fill-as-primary (schema = tickets only).
- Integration-style: self-edit ticket for section A leaves section B content identical.
- Fact-check: skip path when no VERIFY; query planner capped at 4 queries.

## Out of scope / follow-ups

- Exact Haiku model string may vary by OpenRouter availability — set via env.
- Optional: move dedupe-only tickets to Section Repair after metrics prove equal quality at lower cost.
- Optional: UI badge for “Senior Editor ticketed N sections” — not required for v1.
