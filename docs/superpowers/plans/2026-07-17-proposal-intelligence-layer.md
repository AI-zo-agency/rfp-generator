# Phase 2 Proposal Intelligence Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace current Phase 2 bulk retrieval with a Proposal Intelligence Layer that persists a canonical `ProposalExecutionPlan` (opportunity + delivery + writing + proposalMemory + decisionLog + validation), with intelligence-only retrieval and an Evidence/`retrievalPlan` for Phase 3 JIT writing retrieval.

**Architecture:** New package `backend/app/services/proposal_intelligence/` mirrors `company_qualification/`. LangGraph runs Opportunity sequentially, Delivery with fan-out (semaphore-capped) then fan-in dependents, Writing sequentially, then assemble/validate/derive legacy fields. Existing `POST .../phase-2-retrieval` and `run_phase2_retrieval()` stay as entrypoints. `evidenceCorpus` remains empty after Phase 2.

**Tech Stack:** Python 3.11+, Pydantic v2, LangGraph, `llm.chat_json`, Supermemory, existing `ProposalResearchCache` persistence via Supabase

## Global Constraints

- Phase 2 never writes proposal prose.
- Phase 2 never retrieves writing evidence (case studies, testimonials, references, bios for drafting, portfolio, marketing copy).
- Every agent emits structured JSON only; every artifact includes `confidence: float` in `[0.0, 1.0]`.
- Every agent appends at least one `decisionLog` entry `{ agent, decision, reason, confidence }`.
- Non-critical agent failures degrade to empty defaults — never crash the pipeline (except RFP Understanding total failure).
- `ProposalExecutionPlan` is the only Phase 3 contract; `rfpSections` is legacy-derived.
- Spec: `docs/superpowers/specs/2026-07-17-proposal-intelligence-layer-design.md`
- Follow patterns in `backend/app/services/company_qualification/` (schemas, agents, graceful LLM failure).
- Tests: `unittest` style like `backend/test_company_qualification.py`; run with `cd backend && python3 -m pytest <file> -v` or `python3 -m unittest <module>`.
- Do not commit unless the user asks (user rule overrides plan commit steps — skip commit steps or pause for approval).

---

## File Structure

```text
backend/app/services/proposal_intelligence/
  ├── __init__.py
  ├── schemas.py                 # All Pydantic models for ProposalExecutionPlan
  ├── log.py                     # langgraph_intelligence.txt logger
  ├── retrieval.py               # Intelligence-only Supermemory buckets
  ├── memory.py                  # proposalMemory upsert helpers
  ├── agent_base.py              # safe_chat_json, decision entry, confidence clamp
  ├── assembler.py               # merge + legacy derivation
  ├── graph.py                   # LangGraph + run_intelligence_graph()
  └── agents/
        ├── __init__.py
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

backend/app/models/proposal.py   # Add ProposalExecutionPlan field on research cache
backend/app/services/proposal_generator.py
backend/app/services/proposal_drafting_graph.py
backend/app/services/proposal_pipeline_checkpoint.py
frontend/src/types/proposal.ts
frontend/src/lib/proposal-pipeline-checkpoint.ts
frontend/src/lib/proposal-api.ts

backend/test_proposal_intelligence_schemas.py
backend/test_proposal_intelligence_graph.py
backend/test_proposal_intelligence_bridge.py
```

---

### Task 1: Schemas + Research Cache Field

**Files:**
- Create: `backend/app/services/proposal_intelligence/__init__.py`
- Create: `backend/app/services/proposal_intelligence/schemas.py`
- Create: `backend/app/services/proposal_intelligence/agents/__init__.py`
- Modify: `backend/app/models/proposal.py` (add import + field on `ProposalResearchCache`)
- Test: `backend/test_proposal_intelligence_schemas.py`

**Interfaces:**
- Consumes: nothing
- Produces: `ProposalExecutionPlan` and all nested models; `ProposalResearchCache.proposal_execution_plan` alias `proposalExecutionPlan`

- [ ] **Step 1: Write the failing test**

```python
# backend/test_proposal_intelligence_schemas.py
import unittest
from app.services.proposal_intelligence.schemas import (
    ProposalExecutionPlan,
    ProposalMemory,
    PlanValidation,
    CONFIDENCE_WARN_THRESHOLD,
)
from app.models.proposal import ProposalResearchCache


class ProposalIntelligenceSchemaTests(unittest.TestCase):
    def test_empty_plan_round_trip(self) -> None:
        plan = ProposalExecutionPlan(rfpId="rfp-1")
        dumped = plan.model_dump(by_alias=True)
        again = ProposalExecutionPlan.model_validate(dumped)
        self.assertEqual(again.metadata.rfp_id, "rfp-1")
        self.assertEqual(again.proposal_memory.facts, {})
        self.assertIsNone(again.writing.reviewer_personas)
        self.assertEqual(again.evidence_corpus_rule, "phase3_only")

    def test_research_cache_accepts_execution_plan(self) -> None:
        plan = ProposalExecutionPlan(rfpId="rfp-1")
        cache = ProposalResearchCache(
            rfpId="rfp-1",
            updatedAt="2026-07-17T00:00:00Z",
            proposalExecutionPlan=plan.model_dump(by_alias=True),
            evidenceCorpus=[],
        )
        self.assertEqual(cache.evidence_corpus, [])
        self.assertIsNotNone(cache.proposal_execution_plan)

    def test_confidence_threshold_constant(self) -> None:
        self.assertEqual(CONFIDENCE_WARN_THRESHOLD, 0.70)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m unittest test_proposal_intelligence_schemas -v`  
Expected: FAIL — module `proposal_intelligence` not found

- [ ] **Step 3: Implement schemas**

Create `schemas.py` with (use `ConfigDict(populate_by_name=True)` and camelCase aliases throughout):

```python
CONFIDENCE_WARN_THRESHOLD = 0.70
PLAN_VERSION = "1.0"

class DecisionLogEntry(BaseModel):
    agent: str
    decision: str
    reason: str
    confidence: float = 0.0

class ProposalMemory(BaseModel):
    facts: dict[str, str] = Field(default_factory=dict)
    updated_by: list[str] = Field(default_factory=list, alias="updatedBy")
    confidence: float = 1.0

class PlanValidation(BaseModel):
    readiness_status: Literal["ready", "blocked", "partial"] = Field(
        default="partial", alias="readinessStatus"
    )
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    consistency_checks: list[str] = Field(default_factory=list, alias="consistencyChecks")
    low_confidence_artifacts: list[str] = Field(
        default_factory=list, alias="lowConfidenceArtifacts"
    )

# ... OpportunityUnderstanding, ComplianceMatrix, ScopeAnalysis, EvaluationAnalysis,
# SuccessCriteriaResult, OpportunityStrategy, DeliveryModel, DeliveryPattern,
# MethodologyPlan, WorkBreakdown, TimelinePlan, BudgetPlan, ResourcePlan,
# RiskPlan, QaPlan, CommunicationPlan, TrainingPlan,
# OutlineSection, ProposalOutline, SectionPlan, SectionPlans, RetrievalEntry,
# RetrievalPlan, WritingIntelligence, OpportunityIntelligence, DeliveryIntelligence,
# PlanMetadata, ProposalExecutionPlan
```

Required on `ProposalExecutionPlan`:
- `metadata`, `opportunity`, `delivery`, `writing`, `proposal_memory`, `decision_log`, `validation`
- `writing.reviewer_personas: None` default (reserved)
- Helper property or constant documenting Phase 2 never fills evidence: `evidence_corpus_rule = "phase3_only"` as a ClassVar or literal field default for tests

On `ProposalResearchCache` add:

```python
proposal_execution_plan: ProposalExecutionPlan | None = Field(
    default=None, alias="proposalExecutionPlan"
)
```

Import `ProposalExecutionPlan` from `app.services.proposal_intelligence.schemas` **or** define a thin re-export in `models/proposal.py` to avoid circular imports — prefer defining the full plan schemas in `proposal_intelligence/schemas.py` and importing into `models/proposal.py`. If circular import appears, store as `dict[str, Any] | None` on the cache and validate at read time in the graph runner.

**Preferred:** keep typed field; `models/proposal.py` imports from `proposal_intelligence.schemas`.

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd backend && python3 -m unittest test_proposal_intelligence_schemas -v`

- [ ] **Step 5: Commit only if user asks**

---

### Task 2: Agent Base, Memory, Log, Intelligence Retrieval

**Files:**
- Create: `backend/app/services/proposal_intelligence/agent_base.py`
- Create: `backend/app/services/proposal_intelligence/memory.py`
- Create: `backend/app/services/proposal_intelligence/log.py`
- Create: `backend/app/services/proposal_intelligence/retrieval.py`
- Test: extend `backend/test_proposal_intelligence_schemas.py` or add `backend/test_proposal_intelligence_retrieval.py`

**Interfaces:**
- Consumes: `llm.chat_json`, `llm.LlmError`, `supermemory.search_documents`, `KNOWLEDGE_BASE_SEARCH_FILTERS`
- Produces:
  - `async def safe_chat_json(messages, *, max_tokens, temperature, agent_name) -> tuple[dict, str]`
  - `def clamp_confidence(value: Any) -> float`
  - `def decision(agent, decision, reason, confidence) -> dict`
  - `def upsert_memory(memory: ProposalMemory, agent: str, facts: dict[str, str]) -> ProposalMemory`
  - `async def retrieve_intelligence(bucket: Literal[...], *, query: str, limit: int = 6) -> list[dict]`
  - `log_intel_event(event: str, **fields)` → `backend/logs/langgraph_intelligence.txt`

- [ ] **Step 1: Write failing tests**

```python
from app.services.proposal_intelligence.memory import upsert_memory
from app.services.proposal_intelligence.retrieval import (
    INTELLIGENCE_BUCKETS,
    is_writing_evidence_source,
)

def test_upsert_memory_merges_facts():
    mem = ProposalMemory()
    mem = upsert_memory(mem, "rfp_understanding", {"clientName": "City of X", "cms": "Drupal"})
    self.assertEqual(mem.facts["clientName"], "City of X")
    self.assertIn("rfp_understanding", mem.updated_by)

def test_writing_evidence_sources_blocked():
    self.assertTrue(is_writing_evidence_source("03_CS_City_Website.pdf"))
    self.assertTrue(is_writing_evidence_source("04_Bio_Sonja.pdf"))
    self.assertFalse(is_writing_evidence_source("00_Guide_Pricing.pdf"))
    self.assertFalse(is_writing_evidence_source("playbook_qa.pdf"))
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement**

`retrieval.py` buckets:

| Bucket key | Query hints / filename filters | Reject |
|------------|--------------------------------|--------|
| `won_patterns` | `06_WON`, won proposal | reject if content looks like marketing copy for reuse — return pattern excerpts only; filter out `03_CS` |
| `methodology` | methodology, delivery process | reject bios/case studies |
| `pricing` | `00_Guide_Pricing`, `07_FIN`, rate card | reject proposal pricing narrative files if distinguishable |
| `playbooks` | risk, QA, communication, training playbook | — |
| `standards` | accessibility, security, QA standard | — |

`is_writing_evidence_source(name)` returns True for `03_CS`, `04_Bio`, testimonial, reference contact sheets, portfolio samples, exec summary marketing.

`safe_chat_json`: try `llm.chat_json`; on `LlmError` or Exception return `({}, "none")` and log warning — callers validate with Pydantic defaults.

`log.py`: mirror `sections_agent_log.get_langgraph_log_path` pattern → `backend/logs/langgraph_intelligence.txt`.

- [ ] **Step 4: Run tests — PASS**

---

### Task 3: Opportunity Intelligence Agents (1–6)

**Files:**
- Create each under `backend/app/services/proposal_intelligence/agents/`
- Test: `backend/test_proposal_intelligence_opportunity.py`

**Interfaces:**
- Each: `async def run_<name>(*, plan: ProposalExecutionPlan, rfp_context: str, rfp_meta: dict) -> ProposalExecutionPlan`
- Mutates and returns updated plan (opportunity branch + decisionLog + proposalMemory facts)

- [ ] **Step 1: Failing test for RFP Understanding shape**

```python
class OpportunityAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_understanding_has_no_prose_fields(self):
        from app.services.proposal_intelligence.agents.rfp_understanding import (
            UNDERSTANDING_FORBIDDEN_KEYS,
        )
        self.assertIn("content", UNDERSTANDING_FORBIDDEN_KEYS)
        self.assertIn("proposalText", UNDERSTANDING_FORBIDDEN_KEYS)
```

- [ ] **Step 2: Implement `rfp_understanding.py` (critical path)**

System prompt must require JSON matching `OpportunityUnderstanding` and explicitly forbid proposal prose. On total LLM failure: raise a domain error `IntelligenceError("RFP Understanding failed")` — this is the only agent allowed to fail hard.

Extract into `proposalMemory` at least: `clientName`, `organizationType`, `projectType`, and any of `cms`, `hosting`, `accessibilityStandard`, `contractLength` when present.

- [ ] **Step 3: Implement remaining Opportunity agents**

| Function | Input | Writes |
|----------|-------|--------|
| `run_compliance_mapping` | understanding | `opportunity.compliance` |
| `run_scope_analysis` | understanding | `opportunity.scope` |
| `run_evaluation_criteria` | understanding | `opportunity.evaluation` |
| `run_success_criteria` | understanding + evaluation | `opportunity.success_criteria` |
| `run_opportunity_strategy` | all opportunity so far | `opportunity.strategy` (include primaryEvaluatorConcerns, competitivePosition, whyUs, executiveNarrative) |

Each uses `safe_chat_json` except understanding (hard fail). Each sets `confidence`. Each appends `decisionLog`.

Prompts: "Return JSON only. Do not write proposal prose. Do not retrieve or invent KB case studies."

- [ ] **Step 4: Unit-test Pydantic validation of fixture JSON for each agent output shape (no live LLM)**

Use `model_validate` on sample dicts in tests.

- [ ] **Step 5: Run tests — PASS**

---

### Task 4: Delivery Intelligence Agents

**Files:**
- Create delivery agent modules listed in File Structure
- Test: `backend/test_proposal_intelligence_delivery.py`

**Interfaces:**
- `run_delivery_pattern(*, plan, rfp_meta) -> ProposalExecutionPlan` — calls `retrieve_intelligence("won_patterns", ...)`
- Parallel-safe agents (no cross-deps): methodology, budget, risk, qa, communication, training
- Sequential after fan-in: work_breakdown (needs methodology), timeline (needs WBS), resource (needs WBS + timeline)

- [ ] **Step 1: Test deliveryModel vs methodology separation**

```python
def test_delivery_model_is_how_not_phases():
    from app.services.proposal_intelligence.schemas import DeliveryModel, MethodologyPlan
    model = DeliveryModel(type="Agile", cadence="2-week sprints", confidence=0.9)
    method = MethodologyPlan(
        phases=[{"name": "Discovery", "activities": ["kickoff"], "governance": ""}],
        confidence=0.9,
    )
    self.assertNotIn("Discovery", (model.type or ""))
    self.assertEqual(method.phases[0].name, "Discovery")

def test_pricing_strategy_vs_model():
    from app.services.proposal_intelligence.schemas import BudgetPlan
    b = BudgetPlan(pricingStrategy="Compete aggressively", pricingModel="Fixed Fee", confidence=0.8)
    self.assertNotEqual(b.pricing_strategy, b.pricing_model)
```

- [ ] **Step 2: Implement delivery_pattern** — retrieve won patterns; extract patterns only; set `delivery.delivery_pattern` and seed `delivery.delivery_model` if obvious; upsert memory `deliveryApproach`.

- [ ] **Step 3: Implement methodology_planner** — retrieve methodology bucket; write phases; upsert memory.

- [ ] **Step 4: Implement budget_planner** — retrieve pricing bucket; set pricingStrategy + pricingModel separately; upsert `pricingModel` into memory.

- [ ] **Step 5: Implement risk, qa, communication, training planners** — each with matching playbook/standards retrieval.

- [ ] **Step 6: Implement work_breakdown, timeline, resource** — no retrieval; consume prior delivery branches.

- [ ] **Step 7: Run tests — PASS**

---

### Task 5: Writing Intelligence Agents

**Files:**
- `dynamic_section_planner.py`, `section_strategy_planner.py`, `retrieval_planner.py`
- Test: `backend/test_proposal_intelligence_writing.py`

**Interfaces:**
- `run_dynamic_section_planner` → `writing.proposal_outline` with nested `parentId` / `children` / `order` / `required` / `conditionalReason` / `dependencies`
- `run_section_strategy_planner` → `writing.section_plans` including `retrievalGoal`, `writerInstructions`, `successDefinition`, optional `audience`
- `run_retrieval_planner` → `writing.retrieval_plan` with `requiredAssets`, `queries`, `priority`, `constraints`, `expectedSources`, `whyNeeded` — **no excerpt fields**

- [ ] **Step 1: Failing tests**

```python
def test_retrieval_plan_has_no_excerpt_field():
    from app.services.proposal_intelligence.schemas import RetrievalEntry
    fields = set(RetrievalEntry.model_fields)
    self.assertNotIn("excerpt", fields)
    self.assertNotIn("content", fields)

def test_outline_supports_nesting():
    from app.services.proposal_intelligence.schemas import OutlineSection, ProposalOutline
    outline = ProposalOutline(
        sections=[
            OutlineSection(id="4", title="Approach", order=1, required=True, children=["4.1"]),
            OutlineSection(id="4.1", title="Discovery", order=2, required=True, parentId="4"),
        ],
        confidence=0.9,
    )
    self.assertEqual(outline.sections[1].parent_id, "4")
```

- [ ] **Step 2: Implement the three agents sequentially (outline → strategy → retrieval plan)**

Retrieval planner prompt: "Plan retrieval only. Do not fetch documents. Do not include evidence excerpts."

- [ ] **Step 3: Run tests — PASS**

---

### Task 6: Assembler + Validation + Legacy Derivation

**Files:**
- Create: `backend/app/services/proposal_intelligence/assembler.py`
- Create: `backend/app/services/proposal_intelligence/agents/validation.py`
- Test: `backend/test_proposal_intelligence_assembler.py`

**Interfaces:**
- `def refresh_proposal_memory(plan: ProposalExecutionPlan) -> ProposalExecutionPlan` — consolidate known facts from opportunity/delivery into memory
- `def derive_legacy_fields(plan: ProposalExecutionPlan) -> dict` returns `{ rfpSections, sectionQueries, proofPoints }` with `evidenceCorpus` intentionally absent/empty
- `async def run_validate_plan(plan: ProposalExecutionPlan) -> ProposalExecutionPlan`

Validation rules:
- Blocker if `opportunity.understanding` missing client/projectType
- Blocker if `writing.proposal_outline.sections` empty
- Blocker if `writing.retrieval_plan.entries` empty when outline non-empty
- Warning + `lowConfidenceArtifacts` when confidence < 0.70
- `readinessStatus = "ready"` if no blockers

Legacy `rfpSections` derivation:
- Flatten outline sections
- Map sectionPlans keyMessages/evidenceNeeded → requirements
- evaluationWeight from matching evaluation criteria when possible
- zoMode heuristic: methodology/timeline/budget → `write`; team → `select`; company → `pull`
- retrievalFocus from retrievalPlan expectedSources

- [ ] **Step 1: Write tests for empty evidence + legacy derivation + low confidence warning**

- [ ] **Step 2: Implement**

- [ ] **Step 3: Run — PASS**

---

### Task 7: LangGraph Wiring + `run_intelligence_graph`

**Files:**
- Create: `backend/app/services/proposal_intelligence/graph.py`
- Test: `backend/test_proposal_intelligence_graph.py`

**Interfaces:**
- `async def run_intelligence_graph(*, rfp_id, rfp_title, rfp_client, rfp_sector, rfp_location, rfp_context) -> ProposalExecutionPlan`
- Graph state: TypedDict with `plan: dict`, `rfp_*` fields, `error: str | None`, `provider: str`

Graph edges:

```text
START → rfp_understanding → compliance → scope → evaluation → success → strategy
  → delivery_pattern
  → [parallel: methodology | budget | risk | qa | communication | training]
  → join_delivery_parallel
  → work_breakdown → timeline → resource
  → dynamic_section → section_strategy → retrieval_planner
  → assemble → validate → derive_legacy (store legacy on state)
  → END
```

Implementation notes:
- Use LangGraph parallel fan-out from `delivery_pattern` to the six independent planners, then a join node.
- Wrap each LLM-calling node with a module-level `asyncio.Semaphore(2)`.
- On understanding failure set `state["error"]` and route to END.
- Log every node enter/exit via `log_intel_event`.

- [ ] **Step 1: Test node order / join exists**

```python
def test_graph_has_expected_nodes():
    from app.services.proposal_intelligence.graph import _build_graph
    g = _build_graph()
    # Inspect compiled graph nodes if API allows; else test run with mocked agents
```

Prefer mocking agents with `unittest.mock.patch` to assert call order: understanding before strategy; methodology before work_breakdown; dynamic_section before retrieval_planner.

- [ ] **Step 2: Implement graph**

- [ ] **Step 3: Run — PASS**

---

### Task 8: Cutover `run_phase2_retrieval`

**Files:**
- Modify: `backend/app/services/proposal_generator.py` (`run_phase2_retrieval`)
- Keep: `proposal_retrieval_graph.py` unused but present until stable

**Interfaces:**
- `run_phase2_retrieval(rfp_id) -> ProposalResearchCache` still
- Internally calls `run_intelligence_graph`, then `build_loss_lessons_for_rfp` (unchanged), derives legacy fields, saves cache with `evidenceCorpus=[]`, `proposalExecutionPlan=plan`, `rfpSections=derived`, `sectionQueries=derived`, `proofPoints=derived`, `retrievalRounds=0`

- [ ] **Step 1: Update docstring and implementation**

Replace `run_retrieval_graph(...)` block with:

```python
from app.services.proposal_intelligence.graph import run_intelligence_graph
from app.services.proposal_intelligence.assembler import derive_legacy_fields

plan = await run_intelligence_graph(...)
if plan.validation.readiness_status == "blocked":
    raise ProposalError(
        "Phase 2 intelligence blocked: " + "; ".join(plan.validation.blockers),
        status_code=422,
    )
legacy = derive_legacy_fields(plan)
# loss lessons unchanged...
research = ProposalResearchCache(
    rfpId=rfp.id,
    rfpSections=legacy["rfpSections"],
    evidenceCorpus=[],  # HARD RULE
    sectionQueries=legacy["sectionQueries"],
    proofPoints=legacy["proofPoints"],
    retrievalRounds=0,
    proposalExecutionPlan=plan,
    ...
)
```

- [ ] **Step 2: Manual smoke** — start backend, run Phase 2 on a known RFP; confirm research JSON has `proposalExecutionPlan` and empty `evidenceCorpus`.

- [ ] **Step 3: Commit only if user asks**

---

### Task 9: Checkpoint + Frontend Readiness

**Files:**
- Modify: `backend/app/services/proposal_pipeline_checkpoint.py`
- Modify: `frontend/src/lib/proposal-pipeline-checkpoint.ts`
- Modify: `frontend/src/lib/proposal-api.ts`
- Modify: `frontend/src/types/proposal.ts` (add optional `proposalExecutionPlan?: unknown` or typed stub with `validation.readinessStatus`)

**Interfaces:**
- Phase 2 complete when:
  - `research.proposalExecutionPlan?.validation?.readinessStatus === "ready"`
  - OR (migration fallback) old rule: `evidenceCorpus.length && rfpSections.length` for caches created before this change

Backend checkpoint helper must match.

Update messages: "Phase 2 incomplete — no evidence corpus" → "Phase 2 incomplete — Proposal Execution Plan not ready".

- [ ] **Step 1: Update predicates + types**

- [ ] **Step 2: Grep for `evidenceCorpus` Phase 2 gates and update**

```bash
rg -n "evidenceCorpus|Phase 2 incomplete" frontend/src backend/app
```

- [ ] **Step 3: Typecheck frontend if available** (`cd frontend && npx tsc --noEmit` or project script)

---

### Task 10: Phase 3 Bridge — Consume Plan + JIT Retrieval

**Files:**
- Modify: `backend/app/services/proposal_drafting_graph.py`
- Modify: `backend/app/services/proposal_generator.py` (`run_phase3_drafting`)
- Create helper: `backend/app/services/proposal_intelligence/jit_retrieval.py`
- Test: `backend/test_proposal_intelligence_bridge.py`

**Interfaces:**
- `async def retrieve_for_section(entry: RetrievalEntry, *, rfp_client: str) -> list[EvidenceItem]`
- Drafting state gains `execution_plan: dict | None`
- Before `_draft_batch_once` for a section: if plan present, JIT retrieve using that section's retrievalPlan entry; merge into local evidence list; optionally append to research corpus via callback
- User prompt gains blocks:
  - `Proposal Memory:\n{json facts}`
  - `Section Strategy:\n{purpose, keyMessages, writerInstructions, successDefinition, wordBudget, tone}`
  - Brief strategy/delivery excerpts when section title matches methodology/timeline/budget/risk/etc.

`run_phase3_drafting` gate:

```python
plan = research.proposal_execution_plan
if plan is not None and plan.validation.readiness_status != "ready":
    raise ProposalError("Phase 2 Proposal Execution Plan is not ready.", status_code=400)
if plan is None and not research.rfp_sections:
    raise ProposalError("Phase 2 research required...", status_code=400)
```

- [ ] **Step 1: Test JIT helper builds queries from RetrievalEntry and filters writing sources correctly (mock supermemory)**

- [ ] **Step 2: Implement jit_retrieval + drafting bridge**

- [ ] **Step 3: Ensure drafting still works with legacy research (no plan) for migration**

- [ ] **Step 4: Run bridge tests — PASS**

---

### Task 11: End-to-End Guardrails + Logging Verification

**Files:**
- Extend graph tests
- Ensure `backend/.gitignore` already has `logs/`

- [ ] **Step 1: Test that assembler never populates evidence corpus in Phase 2 path**

```python
def test_phase2_research_evidence_always_empty():
    # derive_legacy_fields must not return evidence items
    legacy = derive_legacy_fields(sample_ready_plan())
    self.assertNotIn("evidenceCorpus", legacy)
```

- [ ] **Step 2: Test validation surfaces low confidence**

```python
def test_low_budget_confidence_warns():
    plan = sample_ready_plan()
    plan.delivery.budget.confidence = 0.4
    plan = run_validate_plan_sync(plan)  # or asyncio.run
    self.assertTrue(any("budget" in x.lower() for x in plan.validation.low_confidence_artifacts))
    self.assertEqual(plan.validation.readiness_status, "ready")  # warning only
```

- [ ] **Step 3: Run full unittest suite for proposal_intelligence_*** 

```bash
cd backend && python3 -m unittest \
  test_proposal_intelligence_schemas \
  test_proposal_intelligence_opportunity \
  test_proposal_intelligence_delivery \
  test_proposal_intelligence_writing \
  test_proposal_intelligence_assembler \
  test_proposal_intelligence_graph \
  test_proposal_intelligence_bridge \
  -v
```

Expected: all PASS

---

## Spec Coverage Checklist

| Spec requirement | Task |
|------------------|------|
| ProposalExecutionPlan canonical object | 1 |
| Three layers Opportunity / Delivery / Writing | 3–5, 7 |
| proposalMemory | 1, 2, 6, 10 |
| confidence on artifacts + lowConfidenceArtifacts | 1, 6 |
| reviewerPersonas reserved null | 1 |
| Intelligence-only retrieval buckets | 2, 4 |
| No writing evidence in Phase 2 | 2, 8, 11 |
| evidenceCorpus empty after Phase 2 | 8, 11 |
| Parallel delivery fan-out + semaphore | 7 |
| Writing agents sequential | 5, 7 |
| retrievalPlan (not evidence) | 5 |
| Nested proposalOutline | 5 |
| decisionLog | 2, 3–5 |
| validation readiness gate | 6, 8, 9 |
| Legacy rfpSections / sectionQueries derivation | 6, 8 |
| Keep /phase-2-retrieval endpoint | 8 |
| Phase 3 JIT retrieval bridge | 10 |
| Checkpoint / frontend predicates | 9 |
| langgraph_intelligence.txt logs | 2, 7 |
| Reviewer Persona Planner future only | Out of scope (schema reserved) |

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-17-proposal-intelligence-layer.md`.

Spec updated with your four refinements (Proposal Memory, confidence, parallel fan-out, Reviewer Persona reserved).

**Two execution options:**

1. **Subagent-Driven (recommended)** — Fresh subagent per task, review between tasks, fast iteration  
2. **Inline Execution** — Execute tasks in this session with checkpoints for review  

Which approach?
