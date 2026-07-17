# Sections 1–3 Company Qualification Layer — Design Spec

**Date:** 2026-07-17  
**Status:** Approved for Phase 1 implementation  
**Scope:** Phased replacement of Sections 1–3 generation with a decision-driven architecture

## Goal

Redesign the current Sections 1–3 pipeline into an intelligent, decision-driven **Company Qualification Layer**.

Sections 1–3 are fixed across almost every proposal. Their purpose is **not** to sell the solution but to establish trust, credibility, team capability, and relevant experience before the proposal enters the RFP-specific response.

The architecture should prioritize **selection and editorial judgment** over generation.

## Design Principles

> **Every agent must make a decision before requesting content.**

- Retrieval is **just-in-time**, scoped to the responsibility of the current agent.
- **Structured JSON outputs are mandatory** for every agent — no prose between nodes.
- Each node emits JSON that is **independently testable**.
- The **Section 1 Composition Agent** is the only node responsible for producing human-readable proposal content for Section 1.

## Guiding Principle

Every agent should answer **“Should this be included?”** before asking **“How should this be written?”**

## What Stays the Same

Do **not** replace the orchestration layer. Reuse existing infrastructure:

| Layer | Keep |
|-------|------|
| LangGraph | `run_sections_1_3_graph`, `astream`, node streaming |
| Persistence | `generate_sections_1_3`, partial save, draft merge |
| Checkpointing | `sections-1-3` pipeline phase |
| Retries | Empty-section retry in `proposal_generator.py` |
| UI streaming | Partial callback after each section card |
| LLM routing | `llm.chat_json`, OpenRouter / Fireworks |

**Phase 1** swaps only the **decision-making agents and Section 1 composition** inside the graph. Sections 2–3 continue using the legacy path until Phases 2 and 3.

---

## Overall Architecture (Target State)

```text
Proposal Request
        │
        ▼
Company Intelligence Orchestrator (LangGraph — existing shell)
        │
        ├──────────────────────────────────────────┐
        │                                          │
        ▼                                          ▼
Company Truth Agent                    Proposal Context Agent
        │                                          │
        └──────────────────────────────────────────┘
                           │
                           ▼
                 Capability Prioritization Agent
                           │
                           ▼
                    Content Budget Agent
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
Section 1 Composition   Team Selection      Evidence Selection
      Agent (Phase 1)        Agent                 Agent
                         (Phase 2)           (Phase 3)
         │                 │                 │
         ▼                 ▼                 ▼
                    Bio Builder         Case Study Builder
                    (Phase 2)           (Phase 3)
         └─────────────────┼─────────────────┘
                           ▼
                 Editorial Validation Agent
                           │
                           ▼
                 Final Sections 1–3
```

---

## Phased Rollout

| Phase | Scope | Editorial |
|-------|--------|-----------|
| **1** | Replace Section 1 only | Review-only on Section 1 subsections |
| **2** | Replace Team Overview (Section 2) | Extend review to bios |
| **3** | Replace Our Work (Section 3) | Extend review to case studies |
| **Future** | Optional auto-fix for high-confidence issues (>95%), max 2 iterations | Human approval still required for team/case/positioning decisions |

---

# Phase 1 — Section 1 Company Qualification (Approved)

## Objective

Transform `build_section_1()` from a **generation pipeline** into a **decision pipeline** while preserving LangGraph orchestration, persistence, streaming, and retries.

## LangGraph Changes (Phase 1)

Replace the single node `build_section_1` with seven nodes. Sections 2–3 nodes unchanged.

```text
START
   │
   ├──────────────────────────────┐
   ▼                              ▼
Company Truth Agent       Proposal Context Agent
   │                              │
   └──────────────┬───────────────┘
                  ▼
     Capability Prioritization Agent
                  ▼
          Content Budget Agent
                  ▼
      Section 1 Composition Agent
                  ▼
      Editorial Validation Agent
                  ▼
 Persist + Stream + Inline Suggestions
                  ▼
build_section_2             (legacy — unchanged in Phase 1)
build_section_3             (legacy — unchanged in Phase 1)
                END
```

(`synthesize_proposal_voice` kept for legacy S2/S3; optional slim-down later.)

**Parallelism:** `fetch_company_truth` and `fetch_proposal_context` run concurrently (no dependency between them).

**Deprecation in Phase 1:** Remove bulk `kb_company` dump into five independent LLM writers inside `_build_section_1`. The old `fetch_knowledge_base` node may remain for Sections 2–3 until Phase 2/3, or Section 1 path bypasses shared KB bundles for company facts.

## Agent 1 — Company Truth Agent

### Purpose

Provide verified company facts. **Never generate. Never hallucinate. Never infer.**

### Decisions (before retrieval)

1. Which company document categories are needed for Section 1?
2. Which Supermemory queries map to each category?

### Retrieval (JIT, scoped)

Target categories only — **never** bios (`04_Bio_`) or case studies (`03_CS_`, `06_WON_`):

| Category | Example query focus |
|----------|---------------------|
| Company overview | `01_companyfacts`, who we are (facts only) |
| Business information | legal name, EIN, registration, addresses |
| Organization structure | departments, leadership (not full roster) |
| Certifications | WBENC, WOSB, agency certs |
| Insurance | coverage types |
| Capabilities | service lines, departments |
| Legal / ownership | LLC, DBA, ownership |
| Office locations | addresses, phone, email, web |
| Company history | founded, years in operation |

~7 targeted queries (not 18–25 bucket merges).

### Output schema: `CompanyTruth`

```json
{
  "legalName": "Z'Onion Creative Group LLC",
  "dba": "zo agency",
  "founded": "2013",
  "yearsInOperation": 13,
  "ownership": "Women-owned; Sonja Anderson, Director and CEO",
  "locations": {
    "office": "...",
    "mailing": "...",
    "remittance": "..."
  },
  "contact": {
    "phone": "...",
    "email": "...",
    "website": "..."
  },
  "businessRegistration": {
    "ein": "...",
    "stateIds": [{"state": "OR", "id": "..."}]
  },
  "employeeCount": null,
  "departments": [
    {"name": "Client Services", "head": "...", "summary": "..."}
  ],
  "capabilities": ["Website Development", "Accessibility", "..."],
  "certifications": [
    {"name": "WBENC", "agency": "...", "number": "...", "expires": "..."}
  ],
  "insurance": [
    {"type": "General Liability", "amount": "[VERIFY: amount]"}
  ],
  "sources": ["01_companyfacts_verified.pdf", "..."]
}
```

- Missing fields: `null` or explicit `[VERIFY: field]` — never invented.
- Extraction: parse structured fields from retrieved docs; LLM may **normalize into JSON** but must not add facts not in source text.

## Agent 2 — Proposal Context Agent

### Purpose

Read **only** the RFP summary. Classify context for downstream prioritization.

### Decisions (before retrieval)

What is this solicitation about? No KB. No writing.

### Output schema: `ProposalContext`

```json
{
  "industry": "Municipality",
  "servicesRequested": ["Website", "Accessibility", "Content"],
  "buyerType": "Government",
  "evaluationPriorities": ["Accessibility", "Past performance", "MWBE"],
  "projectComplexity": "medium",
  "proposalType": "website_redesign",
  "summary": "One paragraph classification only — not proposal prose"
}
```

## Agent 3 — Capability Prioritization Agent

### Purpose

Rank company capabilities for **this** RFP. Do not dump every capability.

### Input

- `CompanyTruth.capabilities`
- `ProposalContext`

### Decisions

Which capabilities matter most for Section 1.1 ordering?

### Output schema: `PrioritizedCapabilities`

Three explicit tiers — **Primary**, **Secondary**, and **Omit** — so Section 1 stays focused.

```json
{
  "primary": [
    {"capability": "Website Development", "rationale": "Primary RFP scope"},
    {"capability": "Accessibility", "rationale": "Stated evaluation priority"},
    {"capability": "UX", "rationale": "Core to website redesign"}
  ],
  "secondary": [
    {"capability": "SEO", "rationale": "Supporting, not central"}
  ],
  "omit": [
    {"capability": "Podcast Production", "rationale": "Not in RFP scope"},
    {"capability": "Media Buying", "rationale": "Not in RFP scope"},
    {"capability": "Event Marketing", "rationale": "Not in RFP scope"}
  ]
}
```

No retrieval in this node. **Omit** capabilities must not appear in Section 1 prose.

## Agent 4 — Content Budget Agent

### Purpose

Assign explicit word/format budgets per subsection. **Do not let the LLM decide length.** Won proposals consistently use tight Section 1 budgets; this prevents the 800-word Organization Structure problem.

### Input

- `ProposalContext`
- `PrioritizedCapabilities`

### Decisions

What is the right length and format for each subsection given this RFP type?

### Output schema: `Section1ContentBudget`

```json
{
  "budgets": [
    {
      "sectionId": "section-1-who-we-are",
      "title": "1.1 — Who We Are",
      "format": "narrative",
      "wordMin": 250,
      "wordMax": 350
    },
    {
      "sectionId": "section-1-org-structure",
      "title": "1.2 — Organizational Structure",
      "format": "narrative",
      "wordMin": 150,
      "wordMax": 250
    },
    {
      "sectionId": "section-1-business-info",
      "title": "1.3 — Business Information",
      "format": "table",
      "wordMin": null,
      "wordMax": null,
      "notes": "Mostly table; facts only, no narrative"
    },
    {
      "sectionId": "section-1-certifications",
      "title": "1.4 — Certifications",
      "format": "list",
      "wordMin": 75,
      "wordMax": 150
    },
    {
      "sectionId": "section-1-insurance",
      "title": "1.5 — Insurance Information",
      "format": "facts",
      "wordMin": 50,
      "wordMax": 100
    }
  ]
}
```

Default budgets above apply unless RFP context warrants adjustment (e.g. government RFP may expand 1.4). No retrieval in this node.

## Agent 5 — Section 1 Composition Agent

### Purpose

**Only node that produces human-readable proposal content for Section 1.** This is not stitching — it **chooses what to include**, **orders information**, **applies content budgets**, **applies capability prioritization**, and **produces the final subsection prose**.

Does not invent facts. Does not retrieve.

### Input

- `CompanyTruth`
- `ProposalContext`
- `PrioritizedCapabilities`
- `Section1ContentBudget`
- Existing `brand_voice` (tone/register only — not fact source)

### Subsection rules

| ID | Title | Budget | Rules |
|----|-------|--------|-------|
| `section-1-who-we-are` | 1.1 — Who We Are | 250–350 words | Structure: who we are → core expertise → **primary** capabilities → why clients trust us. **No client name. No solution. No proposal strategy. No omit-tier capabilities.** |
| `section-1-org-structure` | 1.2 — Organizational Structure | 150–250 words | Leadership → Client Services → Strategy → Creative → Digital → Development → Project Management. Department heads only if useful. **Never list every employee. Never resumes.** Message: "organized to deliver efficiently." |
| `section-1-business-info` | 1.3 — Business Information | Mostly table | Founded, legal name, ownership, office, employees, registration. **No narrative.** |
| `section-1-certifications` | 1.4 — Certifications | 75–150 words | **Filter** certs by `ProposalContext` (government, accessibility, MWBE, etc.) |
| `section-1-insurance` | 1.5 — Insurance Information | 50–100 words | From `CompanyTruth.insurance` |

### Output schema: `Section1CompositionResult`

Structured JSON first; prose nested inside. Enables independent testing of the composition plan vs generated text.

```json
{
  "sectionPlan": {
    "section-1-who-we-are": {
      "includedCapabilities": ["Website Development", "Accessibility", "UX"],
      "omittedCapabilities": ["Podcast Production", "Media Buying"],
      "targetWords": {"min": 250, "max": 350}
    }
  },
  "generatedSections": [
    {
      "id": "section-1-who-we-are",
      "title": "1.1 — Who We Are",
      "content": "...markdown prose...",
      "wordCount": 312
    }
  ]
}
```

After validation, map `generatedSections` to five `ProposalSection` cards (same IDs as today for UI compatibility). Emit via existing `_emit_partial` after composition.

### Post-processing (keep)

- `_sanitize_content`
- `_apply_verified_corrections` (name/legal fixes)
- `enforce_narrative_voice`
- Enforce `Section1ContentBudget` word limits (truncate or flag if exceeded)

## Agent 6 — Editorial Validation Agent (Version 1: Review Only)

### Purpose

Review composed Section 1 subsections. **The Editorial Validation Agent does not modify proposal content in Phase 1.**

It only returns structured feedback. The writer decides whether to apply it. This is safer and produces valuable feedback data for future automation.

### Does NOT do (Phase 1)

- Auto-rewrite section content
- Re-run the composition agent
- Mutate `ProposalSection.content` directly

### Detects

- Irrelevant content
- Redundant content (e.g. Who We Are repeated in Business Information)
- Incorrect people (if mentioned in S1)
- Weak certifications (irrelevant to context)
- Missing proof / `[VERIFY]` gaps that should be filled
- Sections exceeding recommended length

### Output schema: `EditorialReviewResult`

```json
{
  "recommendations": [
    {
      "sectionId": "section-1-business-info",
      "sectionTitle": "1.3 — Business Information",
      "issueType": "redundant_content",
      "issue": "Contains 'Who We Are' narrative duplicated from 1.1",
      "recommendation": "Remove narrative paragraphs; keep registration table only",
      "confidence": 0.92,
      "suggestedReplacement": "...(optional excerpt or null)...",
      "status": "pending"
    }
  ]
}
```

Each recommendation carries exactly: **issue**, **recommendation**, **confidence**, **suggested replacement** (optional).

### Persistence

Store in `ProposalResearchCache`:

```json
{
  "section1EditorialReview": {
    "reviewedAt": "ISO8601",
    "recommendations": [ "...EditorialRecommendation[]" ],
    "provider": "openrouter|fireworks"
  }
}
```

New field on `ProposalResearchCache` — does not mutate section `content` until user acts.

### UI (Phase 1) — Inline review

Per subsection in the proposal workspace:

- Show recommendation cards below section content when `status === "pending"`
- Actions: **Approve** (apply `suggestedReplacement` to section content + mark approved), **Reject** (dismiss), **Edit** (open section editor)
- Approved/rejected state persists in research cache

No auto-fix loop in v1.

---

## Retrieval Strategy (Phase 1)

```text
OLD: fetch_knowledge_base → 18–25 queries × 4 buckets → dump into all writers

NEW:
  Company Truth Agent          → ~7 company-scoped queries → CompanyTruth JSON
  Proposal Context Agent       → 0 queries (RFP text only)
  Capability Prioritization    → 0 queries → PrioritizedCapabilities JSON
  Content Budget Agent         → 0 queries → Section1ContentBudget JSON
  Section 1 Composition Agent  → 0 retrieval → Section1CompositionResult JSON
  Editorial Validation Agent   → 0 retrieval → EditorialReviewResult JSON
```

Sections 2–3 in Phase 1 may still use legacy `fetch_knowledge_base` until Phases 2–3.

---

## Code Layout (Proposed)

```text
backend/app/services/company_qualification/
  schemas.py                 # All agent I/O schemas (Pydantic, camelCase aliases)
  agents/
    company_truth.py
    proposal_context.py
    capability_prioritization.py
    content_budget.py
    section_1_composition.py
    editorial_validation.py
  retrieval/
    company_queries.py         # Query templates + JIT Supermemory fetch
```

All agents use `llm.chat_json` with Pydantic response models — **structured JSON is mandatory**, never free-form prose between nodes.

`proposal_sections_graph.py` — wire new nodes; gate Section 1 behind feature flag `USE_COMPANY_QUALIFICATION_S1=true` (config) for safe rollout.

---

## Testing Strategy

Each agent output is **independently unit-testable** with fixture JSON:

| Test | Assert |
|------|--------|
| Company Truth | Valid `CompanyTruth` JSON; no bio/case study sources; nulls for missing fields |
| Proposal Context | Valid `ProposalContext` JSON; no KB refs |
| Capability prioritization | Municipal website RFP → primary: web/accessibility/UX; omit: podcast/media/events |
| Content budget | Returns all 5 subsection budgets with correct word ranges |
| Section 1 composition | Valid `Section1CompositionResult`; 1.3 has no narrative; org structure ≤ 250 words |
| Editorial | Returns `EditorialReviewResult` only; **does not modify** section content |
| Name corrections | `Ron Corner` → `Ron Comer` in composed output |

Integration: run Phase 1 graph on fixture RFP; assert 5 section cards + editorial recommendations in cache.

---

## Success Criteria (Phase 1)

- [ ] Section 1 establishes credibility in **under ~3 pages** without overselling
- [ ] Content budgets enforced: 1.1 **250–350w**, 1.2 **150–250w**, 1.3 table, 1.4 **75–150w**, 1.5 **50–100w**
- [ ] Organization structure stays within budget (+ optional chart later), delivery-focused
- [ ] Omit-tier capabilities never appear in Section 1 prose
- [ ] 1.3 is facts/table only — no narrative duplication from 1.1
- [ ] 1.4 shows only **context-relevant** certifications
- [ ] Every fact traceable to `CompanyTruth.sources`
- [ ] Retrieval is JIT per agent — no upfront company dump for S1
- [ ] Editorial recommendations visible inline; **no auto-rewrite** in v1
- [ ] Sections 2–3 behavior unchanged
- [ ] Partial streaming and checkpoint resume still work

---

# Phase 2 — Team Overview (Outline)

- Replace `build_section_2` with Team Selection Agent + Bio Builder
- **Remove** mandatory Sonja/Rachael; skill-based role matching (max 5)
- JIT: one `04_Bio_{Name}.pdf` fetch per selected person only
- Bio template ≤300 words each
- Extend editorial review to bios (review-only)

---

# Phase 3 — Our Work (Outline)

- Replace `build_section_3` with Evidence Selection Agent + Case Study Builder
- Score candidates: Industry 35%, Service 30%, Eval alignment 20%, Proof 10%, Recency 5%
- Top **3–5** only — never 10
- JIT full retrieval per selected case study
- Extend editorial review to case studies (review-only)

---

# Future — Editorial Auto-Fix (Not Phase 1)

After user feedback:

- Optional auto-fix for recommendations with **confidence > 0.95**
- Maximum **2** validation iterations
- **Never** auto-approve: team selection, case study selection, proposal positioning
- Human approval remains default for strategic decisions

---

## Approval

- **Phased rollout:** Phase 1 → 2 → 3 — **Approved**
- **LangGraph nodes (separate agents):** — **Approved**
- **Content Budget Agent:** Explicit per-subsection word/format budgets — **Approved**
- **Section 1 Composition Agent** (renamed from Assembler): Chooses, orders, budgets, composes — **Approved**
- **Capability tiers:** Primary / Secondary / Omit — **Approved**
- **Structured JSON mandatory** between all agents — **Approved**
- **Editorial v1:** Review-only, no content mutation; inline UI with Approve/Reject/Edit — **Approved**
- **Design principle:** Decision before retrieval; JSON nodes; composition-only prose — **Approved**

**Next step:** Implementation plan for Phase 1 (`writing-plans` skill), then build behind feature flag.
