# Phase 2 — Proposal Intelligence Layer — Design Spec

**Date:** 2026-07-17  
**Status:** Approved for implementation  
**Last refined:** Proposal Memory, per-artifact confidence, parallel fan-out where independent, Reviewer Persona Planner reserved as future  

**Scope:** Replace current Phase 2 (RFP section map + bulk evidence corpus) with a decision-driven Proposal Intelligence Layer that produces a single canonical `ProposalExecutionPlan`

---

## Goal

Transform the raw RFP into a fully structured **Proposal Execution Plan** before any proposal prose is written.

Today's Phase 2 does:

```text
RFP → Map sections → Retrieve everything → Evidence corpus → Write 
```

The new Phase 2 does:

```text
RFP → Understand → Plan strategy → Plan delivery → Plan sections → Plan retrieval → Persist plan
```

Phase 3 then becomes:

```text
Read Proposal Execution Plan → Retrieve only planned assets → Write assigned section
```

---

## Design Philosophy

> **Every agent in Phase 2 must make a decision before producing an artifact.**

Phase 2 is a **planning layer**, not a generation layer. Agents normalize the RFP into structured planning data, determine strategy, allocate priorities, and define retrieval requirements. They must **not** produce proposal prose. Every output is structured, deterministic, and independently testable. Proposal writing begins only in Phase 3.

### Pipeline phases (system-wide)

| Phase | Name | Question answered |
|-------|------|-------------------|
| 1 | Company Qualification | Who are we? (Sections 1–3) |
| 2 | Proposal Intelligence | What do we need to do, and how do we win? |
| 3 | Proposal Writing | Explain the plan using targeted evidence |
| 4 | Editorial & Compliance | Review, refine, and verify |

---

## Hard Rules

1. **Phase 2 never writes proposal content.** No section prose, no executive summaries, no marketing copy.
2. **Phase 2 never retrieves writing evidence.** No case studies, testimonials, references, resumes, portfolio examples, images, or project descriptions for use in section drafting.
3. **Phase 2 retrieves only decision intelligence** — delivery patterns, methodology docs, pricing knowledge, playbooks, and company standards.
4. **`evidenceCorpus` stays empty until Phase 3.** Phase 3 is solely responsible for retrieving writing assets using the `retrievalPlan`.
5. **One canonical contract:** `ProposalExecutionPlan` is the single source of truth. Every planner updates it; every Phase 3 writer reads it.
6. **Every agent emits structured JSON only.** Independently testable. Appends a `decisionLog` entry.
7. **Agent failures degrade gracefully.** Log a warning, use a safe empty default, never fail the whole Phase 2 pipeline (same pattern as editorial validation).
8. **Keep the existing `/phase-2-retrieval` endpoint** during migration. Internals change; API surface stays.
9. **Backward-compatible fields** (`rfpSections`, `sectionQueries`, `evidenceCorpus`, `proofPoints`, `writingAvoidances`) are **derived** from the plan — they are not the source of truth.

---

## Overall Architecture

```text
Sections 1–3 Complete
        │
        ▼
───────────────────────────────────────
Phase 2 — Proposal Intelligence Layer
───────────────────────────────────────
        │
        ├── Opportunity Intelligence
        │
        ├── Delivery Intelligence
        │
        └── Writing Intelligence
        │
        ▼
Proposal Execution Plan
(single source of truth)
        │
        ▼
───────────────────────────────────────
Phase 3 — Writing Layer
───────────────────────────────────────
        │
Retrieve planned assets (JIT per section)
        │
Write assigned section
        │
Editorial review
        │
Persist
```

---

## Three Internal Layers

### 1. Opportunity Intelligence

**Purpose:** Understand the opportunity.

| Agent | Responsibility |
|-------|----------------|
| RFP Understanding Agent | Normalize full RFP into structured opportunity overview (no Supermemory, no writing) |
| Compliance Mapping Agent | Build compliance matrix from understanding |
| Scope Analysis Agent | Mandatory / optional / future / out-of-scope / dependencies |
| Evaluation Criteria Agent | Scoring weights, emphasis, writing style |
| Success Criteria Agent | What success looks like; recurring themes |
| Opportunity Strategy Agent | Winning theme, differentiators, why-us, executive narrative |

**Output branch:** `proposalExecutionPlan.opportunity`

### 2. Delivery Intelligence

**Purpose:** Decide how the project should be executed.

| Agent | Responsibility | Intelligence retrieval |
|-------|----------------|------------------------|
| Delivery Pattern Intelligence | Patterns from similar won proposals (never copy content) | Won proposals by industry / client type / service / complexity |
| Methodology Planner | Delivery phases and activities | Methodology docs |
| Work Breakdown Planner | Work packages per phase | — |
| Timeline Planner | Milestones, go-live, review cycles | — |
| Budget Planner | Pricing strategy + model + constraints | Pricing guide, rate cards, historical pricing knowledge |
| Resource Planner | Role allocation by phase | — |
| Risk Planner | Risks + mitigations | Risk playbooks |
| QA Planner | QA approach and gates | QA standards / playbooks |
| Communication Planner | Cadence, channels, reporting | Communication playbooks |
| Training Planner | Training + transition plans | Training playbooks |

**Output branch:** `proposalExecutionPlan.delivery`

**Delivery Model vs Methodology:**
- `deliveryModel` = **how** work happens (Agile, cadence, governance, client engagement)
- `methodology` = **what** phases exist (Discovery → UX → Development → QA → Training)

### 3. Writing Intelligence

**Purpose:** Prepare writers — not write.

| Agent | Responsibility |
|-------|----------------|
| Dynamic Section Planner | Nested proposal outline (required / conditional / order / parent / children / dependencies) |
| Section Strategy Planner | Per-section purpose, key messages, success definition, retrieval goal, writer instructions, word budget, tone |
| Retrieval Planner | Per-section required assets, queries, priority, expected sources, constraints — **no retrieval** |

**Output branch:** `proposalExecutionPlan.writing`

---

## Canonical Contract: `ProposalExecutionPlan`

```text
ProposalExecutionPlan
│
├── metadata
│     { rfpId, generatedAt, provider, planVersion, generationMode,
│       wonPatternsUsed[], planConfidence, validationStatus,
│       layerStatus: { opportunity, delivery, writing } }
│
├── opportunity
│     ├── understanding      { client, industry, orgType, projectType, services[],
│     │                        businessGoals[], painPoints[], desiredOutcomes[],
│     │                        complexity, budgetIntel{}, timelineIntel{}, confidence }
│     ├── compliance         { items[ { id, requirement, mandatory, sourceRef,
│     │                        targetSection, evidenceNeeded, status, owner } ], confidence }
│     ├── scope              { mandatory[], optional[], futurePhases[], outOfScope[],
│     │                        dependencies[], confidence }
│     ├── evaluation         { criteria[ {name, weight, priorityRank} ], emphasis[],
│     │                        writingStyle, confidence }
│     ├── successCriteria    { items[ { criterion, why, recurringTheme } ], confidence }
│     └── strategy           { winningTheme, coreMessage, differentiators[],
│                              trustBuilders[], riskMitigation[], proofStrategy,
│                              tone, keyMessages[],
│                              primaryEvaluatorConcerns[], competitivePosition,
│                              whyUs, executiveNarrative, confidence }
│
├── delivery
│     ├── deliveryModel      { type, governance, cadence, clientEngagement,
│     │                        reviewModel, decisionMaking, confidence }
│     ├── deliveryPattern    { patternsObserved[], sourceWonProposals[],
│     │                        staffingShape, phaseShape, confidence }
│     ├── methodology        { phases[ {name, activities[], governance} ], confidence }
│     ├── workBreakdown      { packages[ { workPackage, phase, deliverables[] } ], confidence }
│     ├── timeline           { milestones[], goLive, reviewCycles, confidence }
│     ├── budget             { pricingStrategy, pricingModel, pricingTier,
│     │                        contractType, ceiling, constraints[], costWeight,
│     │                        pricingValidation, roleEffort[], confidence }
│     ├── resources          { allocations[ { role, allocationPct, phase } ], confidence }
│     ├── risk               { risks[ { risk, likelihood, impact, mitigation } ], confidence }
│     ├── qa                 { approach, gates[], confidence }
│     ├── communication      { cadence, channels[], reportingPlan, confidence }
│     └── training           { trainingPlan, transitionPlan, confidence }
│
├── writing
│     ├── proposalOutline    { sections[ { id, title, order, required,
│     │                        conditionalReason, parentId, children[],
│     │                        dependencies[] } ], confidence }
│     ├── sectionPlans       { plans[ { sectionId, title, purpose, keyMessages[],
│     │                        evaluationCriteria[], evidenceNeeded[],
│     │                        retrievalGoal, writerInstructions,
│     │                        successDefinition, wordBudget, tone, register,
│     │                        audience } ], confidence }
│     ├── retrievalPlan      { entries[ { sectionId, requiredAssets[], queries[],
│     │                        priority, constraints[], expectedSources[],
│     │                        whyNeeded } ], confidence }
│     └── reviewerPersonas   null   # reserved — Reviewer Persona Planner (future)
│
├── proposalMemory           { facts: { key → value }, updatedBy[], confidence }
│     # Normalized fact cache for writers — clientName, organizationType, cms, hosting,
│     # accessibilityStandard, contractLength, deliveryApproach, pricingModel, etc.
│     # Updated by planners as facts are discovered; writers read this first.
│
├── decisionLog              [ { agent, decision, reason, confidence } ]
│
└── validation               { readinessStatus, blockers[], warnings[],
                               consistencyChecks[], lowConfidenceArtifacts[] }
```

### Field notes

| Field | Rule |
|-------|------|
| `delivery.deliveryModel` | How work happens (Agile/Hybrid, sprint cadence, steering committee) |
| `delivery.methodology` | What phases exist |
| `delivery.budget.pricingStrategy` | Compete aggressively / value premium / etc. |
| `delivery.budget.pricingModel` | Fixed fee / T&M / hybrid |
| `writing.proposalOutline` | Nested hierarchy; source of truth for section structure |
| `writing.sectionPlans` | Writer brief per section — purpose, success, instructions; optional `audience` for future personas |
| `writing.retrievalPlan` | Planned assets + queries only; **no retrieved content** |
| `writing.reviewerPersonas` | Reserved `null` until Reviewer Persona Planner ships |
| `proposalMemory` | Normalized reusable facts discovered during planning; writers prefer this over scanning branches |
| `decisionLog` | Every planner appends: decision, reason, agent, confidence |
| `validation` | Consistency + readiness; gates Phase 2 completion; flags low-confidence artifacts for human review |

### Confidence (required on every planner artifact)

Every major branch and planner output includes `confidence: float` in `[0.0, 1.0]`.

Examples: `delivery.timeline.confidence`, `delivery.budget.confidence`, `opportunity.strategy.confidence`.

Validation rules:
- If any critical artifact confidence is below a threshold (default **0.70**), add to `validation.lowConfidenceArtifacts` and emit a warning (e.g. "Budget confidence low — needs human review").
- Low confidence alone does **not** block `readinessStatus = "ready"` unless the artifact is empty/missing; it surfaces for human review.
- `decisionLog` entries already carry confidence; artifact-level confidence is separate and required.

### Retrieval plan `expectedSources` values

```text
won_proposals | case_studies | testimonials | references |
methodology | pricing | bios | company_facts | portfolio |
images | diagrams | playbooks | standards
```

Phase 2 planners may *reference* these source types in the plan. Only Phase 3 actually retrieves writing assets (`case_studies`, `testimonials`, `references`, `bios`, `portfolio`, `images`, etc.).

---

## Intelligence Retrieval (Phase 2 only)

Phase 2 asks: **"How should we execute this type of project?"** — not **"What should I write?"**

### Allowed retrieval

| Bucket | Match / source | Extract |
|--------|----------------|---------|
| Delivery Pattern Intelligence | Won proposals by industry, client type, service, complexity | Patterns only: phase shapes, staffing ratios, governance models. **Never copy content.** |
| Methodology Intelligence | Internal methodology documents | Delivery phases, governance, discovery, Agile/Waterfall, QA approach |
| Pricing Intelligence | Pricing Guide, historical pricing knowledge, cost models, rate cards | Pricing knowledge — **never proposal pricing text** |
| Delivery Playbooks | Project, communication, risk, QA, training playbooks | Planning patterns |
| Company Standards | Standard methodology, QA, accessibility, security process | Standards for planners |

### Forbidden in Phase 2

- Case studies
- Testimonials
- References
- Project descriptions
- Marketing copy
- Executive summaries
- Section-level writing evidence
- Resumes / bios for drafting
- Portfolio examples / images for drafting

---

## LangGraph Orchestration

**New package:** `backend/app/services/proposal_intelligence/`

**Entrypoint:** Existing `run_phase2_retrieval()` in `proposal_generator.py` calls the new graph instead of `run_retrieval_graph()`.

**Endpoint:** `POST /{rfp_id}/proposal/phase-2-retrieval` — unchanged.

### Node order (hybrid: sequential layers, parallel within layers)

```text
START
  │
  ▼ Opportunity Intelligence (sequential — each needs prior output)
  1.  rfp_understanding
  2.  compliance_mapping
  3.  scope_analysis
  4.  evaluation_criteria
  5.  success_criteria
  6.  opportunity_strategy
  │
  ▼ Delivery Intelligence
  7.  delivery_pattern_intelligence   (+ intelligence retrieval)
        │
        ├─ fan-out (parallel, independent after pattern + opportunity) ─
        │     methodology_planner (+ methodology retrieval)
        │     budget_planner      (+ pricing retrieval)
        │     risk_planner        (+ risk playbook retrieval)
        │     qa_planner          (+ QA standards retrieval)
        │     communication_planner (+ comm playbook retrieval)
        │     training_planner    (+ training playbook retrieval)
        │
        └─ fan-in → sequential dependents
              work_breakdown_planner   (needs methodology)
              timeline_planner         (needs WBS + scope + timeline intel)
              resource_planner         (needs WBS + methodology + timeline)
  │
  ▼ Writing Intelligence
 17.  dynamic_section_planner
 18.  section_strategy_planner
 19.  retrieval_planner
      (Writing agents stay sequential: outline → strategy → retrieval plan.
       Do not parallelize these three — each consumes the prior.)
  │
  ▼ Assembly
 20.  assemble_execution_plan   (merge branches + refresh proposalMemory)
 21.  validate_plan
 22.  derive_legacy_compat
  │
  END
```

**Parallelism rule:** Fan-out only where agents share the same upstream inputs and do not depend on each other. Prefer sequential when rate-limit risk is high; if Fireworks 429s reappear, collapse Delivery fan-out back to sequential without changing agent contracts.

**Rate-limit note:** Sections 1–3 taught us parallel LLM calls can fail the run. Delivery fan-out uses a shared asyncio semaphore (max 2 concurrent LLM calls) even when LangGraph edges are parallel.

### Agent contract

Every agent:

1. Receives the growing `ProposalExecutionPlan` (or relevant upstream branches) + RFP context where needed.
2. Emits structured JSON only.
3. Updates one branch of the plan.
4. Appends at least one `decisionLog` entry: `{ agent, decision, reason, confidence }`.
5. On failure: logs warning, returns safe empty/default artifact, does **not** raise into the pipeline.

### RFP Understanding Agent (special)

- Reads the **entire RFP** again (not the Sections 1–3 summary).
- No Supermemory.
- No methodology, budget, or prose.
- Output: normalized opportunity understanding JSON only.

---

## Persistence

### Primary

Persist full `ProposalExecutionPlan` on `ProposalResearchCache.proposalExecutionPlan`.

Also persist individual layer artifacts for debugging/observability (same object nested under the plan is sufficient; optional flat mirrors on research cache if useful for logging).

### Legacy compatibility derivation (after Validation)

| Legacy field | Derived from |
|--------------|--------------|
| `rfpSections` | `writing.proposalOutline` + `writing.sectionPlans` (flat list for old consumers) |
| `sectionQueries` | `writing.retrievalPlan` queries by `sectionId` |
| `evidenceCorpus` | **Always empty after Phase 2** |
| `proofPoints` | Planned needs from section plans / success criteria (no retrieved excerpts) |
| `writingAvoidances` / `lossLessons` | Keep existing `build_loss_lessons_for_rfp` call after the intelligence graph (unchanged). It is complementary intel, not writing evidence, and continues to populate these fields for Phase 3 prompts. |

`rfpSections` is a **legacy compatibility field**. Source of truth is `writing.proposalOutline`.

### Checkpoint / readiness

Phase 2 is complete when:

```text
proposalExecutionPlan.validation.readinessStatus == "ready"
```

**Not** when `evidenceCorpus.length > 0`.

Update:

- `backend/app/services/proposal_pipeline_checkpoint.py`
- `frontend/src/lib/proposal-pipeline-checkpoint.ts`
- `frontend/src/lib/proposal-api.ts` Phase 2 completeness helpers

Optional UI label change: "Phase 2 research" → "Phase 2 intelligence" (can defer to a small follow-up).

---

## Phase 3 Bridge (minimal — no specialized writer rewrite yet)

Do **not** replace Phase 3 writers in this implementation.

Temporary bridge:

1. `run_phase3_drafting()` requires `proposalExecutionPlan` with `validation.readinessStatus == "ready"`.
2. Section list comes from `writing.proposalOutline` / `sectionPlans` (fall back to legacy `rfpSections` if plan missing — migration safety).
3. **Before each section draft:** JIT-retrieve using that section's `retrievalPlan` entry (queries + expectedSources + constraints).
4. Append retrieved items into `evidenceCorpus` incrementally.
5. Inject into the writer prompt:
   - `proposalMemory` facts (normalized cache — prefer this for shared facts)
   - Section strategy (purpose, keyMessages, writerInstructions, successDefinition, wordBudget, tone)
   - Opportunity strategy themes (winningTheme, whyUs — brief)
   - Delivery plan excerpts relevant to the section (methodology/timeline/budget as applicable)
   - Retrieved evidence for that section only
6. Writers still produce prose; they no longer invent methodology/timeline/budget — they **explain** the plan.

Specialized section writers remain a **future** Phase 3 redesign.

---

## File Structure

```text
backend/app/services/proposal_intelligence/
  ├── __init__.py
  ├── graph.py                      # LangGraph definition + run_intelligence_graph()
  ├── schemas.py                    # ProposalExecutionPlan + all sub-models
  ├── retrieval.py                  # Bucket-scoped intelligence Supermemory helpers
  ├── assembler.py                  # Assemble plan + derive legacy fields
  ├── log.py                        # → backend/logs/langgraph_intelligence.txt
  └── agents/
        ├── rfp_understanding.py
        ├── compliance_mapping.py
        ├── scope_analysis.py
        ├── evaluation_criteria.py
        ├── success_criteria.py
        ├── opportunity_strategy.py
        ├── delivery_pattern.py
        ├── methodology_planner.py
        ├── work_breakdown_planner.py
        ├── timeline_planner.py
        ├── budget_planner.py
        ├── resource_planner.py
        ├── risk_planner.py
        ├── qa_planner.py
        ├── communication_planner.py
        ├── training_planner.py
        ├── dynamic_section_planner.py
        ├── section_strategy_planner.py
        ├── retrieval_planner.py
        └── validation.py
```

### Touch points (existing)

| File | Change |
|------|--------|
| `backend/app/models/proposal.py` | Add `ProposalExecutionPlan` models; add field on `ProposalResearchCache` |
| `backend/app/services/proposal_generator.py` | `run_phase2_retrieval` → call intelligence graph; Phase 3 readiness gate |
| `backend/app/services/proposal_drafting_graph.py` | Consume plan + JIT retrieval bridge |
| `backend/app/services/proposal_pipeline_checkpoint.py` | Phase 2 complete = plan ready |
| `frontend/src/types/proposal.ts` | Types for plan (or keep opaque + readiness flag) |
| `frontend/src/lib/proposal-pipeline-checkpoint.ts` | Update Phase 2 done predicate |
| `frontend/src/lib/proposal-api.ts` | Update Phase 2 completeness messages |
| `backend/app/services/proposal_retrieval_graph.py` | Deprecated / unused after cutover (keep file until stable, then remove) |

---

## Error Handling

| Failure | Behavior |
|---------|----------|
| Single agent LLM JSON failure | Empty/default artifact + decisionLog warning; continue |
| Intelligence retrieval empty | Planner proceeds with RFP-only reasoning; log warning |
| Validation finds blockers | `readinessStatus = "blocked"`; Phase 3 refuses to start with clear error |
| Validation finds warnings only | `readinessStatus = "ready"` with warnings persisted |
| Entire layer crash | Catch at layer boundary; mark that `layerStatus` failed; still attempt assembly with partial plan |

Phase 2 must not throw "All configured LLM providers failed" for a non-critical planner. Critical path: RFP Understanding + Validation. If Understanding fails completely, Phase 2 may fail with a clear error (no plan without understanding).

---

## Testing Strategy

| Layer | Tests |
|-------|-------|
| Schemas | Pydantic round-trip; required fields; nested outline hierarchy |
| RFP Understanding | Fixture RFP → structured understanding; no prose keys |
| Compliance / Scope / Evaluation / Success / Strategy | Consume understanding fixture → expected shape |
| Delivery planners | Mock intelligence retrieval; assert deliveryModel vs methodology separation; pricingStrategy vs pricingModel |
| Writing planners | Nested outline; sectionPlans include retrievalGoal / writerInstructions / successDefinition; retrievalPlan has no excerpt fields |
| Assembler | Legacy `rfpSections` / `sectionQueries` derivation; `evidenceCorpus == []` |
| Validation | Blockers when outline empty; ready when minimum branches populated |
| Graph order | Nodes run in strict sequential order (same pattern as Sections 1–3 order test) |
| Phase 3 bridge | Mock retrievalPlan → JIT search called with planned queries only |
| Checkpoint | Phase 2 complete without evidence corpus |

---

## Out of Scope (this implementation)

- Specialized per-section writers (Executive Summary Writer, Methodology Writer, etc.)
- Editorial Validation Agent rewrite for Phase 3 sections
- Compliance Verification Agent as a new Phase 4 rewrite
- UI for browsing the full Proposal Execution Plan (optional later)
- Removing `proposal_retrieval_graph.py` until the new graph is proven in production
- Auto-fix loops over the execution plan
- **Reviewer Persona Planner** (reserved future agent — maps evaluator roles to section audiences/tones; schema may reserve `writing.reviewerPersonas: null` for forward compatibility)

---

## Future: Reviewer Persona Planner (reserved)

Not required for this implementation. When added, it runs after Opportunity Strategy (or early in Writing Intelligence) and produces:

```text
reviewerPersonas: [
  { role: "Procurement", concerns: [...], preferredTone: "executive" },
  { role: "IT Director", concerns: [...], preferredTone: "technical" },
  ...
]
```

Section Strategy then sets per-section `audience` from the matching persona. Reserve the field on `writing` as optional/`null` so the schema does not need a breaking change later.

---

## Success Criteria

Phase 2 implementation is successful when:

1. Running Phase 2 persists a complete `ProposalExecutionPlan` with opportunity, delivery, writing, decisionLog, and validation.
2. `evidenceCorpus` is empty after Phase 2.
3. `writing.retrievalPlan` specifies required assets and queries for every outline section.
4. Every planner artifact includes a confidence score; Validation surfaces low-confidence items.
5. `proposalMemory` holds normalized reusable facts for writers.
6. Phase 3 can start from the plan, JIT-retrieve per section, and draft without re-parsing the raw RFP as its primary input.
7. Existing UI Phase 2 / Phase 3 buttons still work via the same endpoints.
8. Agent failures do not wipe the whole run when a non-critical planner fails.
9. Logs land in `backend/logs/langgraph_intelligence.txt` for debugging.

---

## Implementation Order (for writing-plans)

1. Schemas + `ProposalResearchCache` field
2. Logging + intelligence retrieval helpers
3. Opportunity Intelligence agents (1–6)
4. Delivery Intelligence agents (7–16) with retrieval
5. Writing Intelligence agents (17–19)
6. Assembler + Validation + legacy derivation
7. LangGraph wiring + `run_phase2_retrieval` cutover
8. Checkpoint / frontend readiness predicates
9. Phase 3 bridge (consume plan + JIT retrieval)
10. Tests + deprecate old retrieval graph path
