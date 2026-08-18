"""Batched Phase 2 intelligence — same schemas, fewer LLM hops.

The old graph ran ~18 specialist agents, each re-sending overlapping RFP excerpts.
These passes keep the specialist rules and Pydantic validation, but ask one model
call per *layer* (opportunity → strategy/delivery → execution → outline → writing).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.services import llm
from app.services.llm import LlmError
from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.agents.rfp_understanding import (
    AGENT as UNDERSTANDING_AGENT,
    UNDERSTANDING_FORBIDDEN_KEYS,
)
from app.services.proposal_intelligence.agents.section_strategy_planner import (
    apply_section_strategy_from_raw,
)
from app.services.proposal_intelligence.agents.retrieval_planner import (
    apply_retrieval_plan_from_raw,
)
from app.services.proposal_intelligence.agents.winning_pattern_intelligence import (
    _assignments_from_raw,
    _merge_patterns_into_section_plans,
    _pattern_confidence,
    _query_for_plan,
)
from app.services.proposal_intelligence.plan_ops import (
    IntelligenceError,
    append_decision,
    merge_memory,
    set_provider,
)
from app.services.proposal_intelligence.retrieval import retrieve_intelligence
from app.services.proposal_intelligence.schemas import (
    BudgetPlan,
    CommunicationPlan,
    ComplianceMatrix,
    DeliveryModel,
    DeliveryPattern,
    EvaluationAnalysis,
    MethodologyPlan,
    OpportunityStrategy,
    OpportunityUnderstanding,
    ProposalExecutionPlan,
    QaPlan,
    ResourcePlan,
    RiskPlan,
    ScopeAnalysis,
    SuccessCriteriaResult,
    TimelinePlan,
    TrainingPlan,
    WorkBreakdown,
)

logger = logging.getLogger(__name__)

_PLAYBOOK_REASON = "Planned via playbooks intelligence"
_STANDARDS_REASON = "Planned via standards intelligence"

_OPPORTUNITY_SYSTEM = """You are zö agency's Opportunity Intelligence agent.
Read the RFP once. Extract structured opportunity intel. Do NOT write proposal prose.
Do NOT invent methodology, budget tables, or section drafts.
Never include keys: content, proposalText, executiveSummary, marketingCopy, draft.

Return JSON ONLY:
{
  "understanding": {
    "client": "string",
    "industry": "string",
    "orgType": "Municipality|County|State|Nonprofit|Corporate|Other",
    "projectType": "string",
    "services": ["string"],
    "businessGoals": ["string"],
    "painPoints": ["string"],
    "desiredOutcomes": ["string"],
    "complexity": "low|medium|high",
    "budgetIntel": {
      "ceiling": "string or null",
      "pricingModelHint": "string or null",
      "contractType": "string or null",
      "notes": "string"
    },
    "timelineIntel": {
      "projectStart": "string or null",
      "completion": "string or null",
      "goLive": "string or null",
      "milestones": ["string"],
      "notes": "string"
    },
    "confidence": 0.0,
    "memoryFacts": {
      "clientName": "string",
      "organizationType": "string"
    }
  },
  "compliance": {
    "items": [
      {
        "id": "comp-1",
        "requirement": "string",
        "mandatory": true,
        "sourceRef": "string",
        "targetSection": "string",
        "evidenceNeeded": "string",
        "status": "open",
        "owner": "string"
      }
    ],
    "confidence": 0.0
  },
  "scope": {
    "mandatory": ["string"],
    "optional": ["string"],
    "futurePhases": ["string"],
    "outOfScope": ["string"],
    "dependencies": ["string"],
    "confidence": 0.0
  },
  "evaluation": {
    "criteria": [{"name": "string", "weight": 25, "priorityRank": 1}],
    "emphasis": ["Methodology", "Experience"],
    "writingStyle": "executive|technical|mixed",
    "confidence": 0.0
  },
  "successCriteria": {
    "items": [{"criterion": "string", "why": "string", "recurringTheme": true}],
    "confidence": 0.0
  }
}

Compliance: include the FULL submission checklist — documents to submit, forms to return,
vendor qualification narratives, addenda acknowledgement, financial stability, awards,
references, pricing attachment format.
Evaluation: include weighting when the RFP states it.
Success: mark recurringTheme true for themes that should echo across the proposal.
Scope: separate mandatory vs optional vs out-of-scope. No proposal prose.
"""

_STRATEGY_DELIVERY_SYSTEM = """You are zö agency's Strategy + Delivery Intelligence agent.
Decide how to win AND how work is delivered. Do NOT write proposal sections.

Return JSON ONLY:
{
  "strategy": {
    "winningTheme": "string",
    "coreMessage": "string",
    "differentiators": ["string"],
    "trustBuilders": ["string"],
    "riskMitigation": ["string"],
    "proofStrategy": "string",
    "tone": "string",
    "keyMessages": ["string"],
    "primaryEvaluatorConcerns": ["string"],
    "competitivePosition": "string",
    "whyUs": "string",
    "executiveNarrative": "string — strategic arc only, not full exec summary prose",
    "confidence": 0.0
  },
  "deliveryPattern": {
    "patternsObserved": ["string"],
    "sourceWonProposals": ["filename or id"],
    "staffingShape": "string",
    "phaseShape": "string",
    "confidence": 0.0
  },
  "deliveryModel": {
    "type": "Agile|Waterfall|Hybrid",
    "governance": "string",
    "cadence": "string",
    "clientEngagement": "string",
    "reviewModel": "string",
    "decisionMaking": "string",
    "confidence": 0.0
  },
  "methodology": {
    "phases": [{"name": "Discovery", "activities": ["string"], "governance": "string"}],
    "confidence": 0.0
  },
  "budget": {
    "pricingStrategy": "string",
    "pricingModel": "Fixed Fee|T&M|Hybrid|Other",
    "pricingTier": "string",
    "contractType": "string",
    "ceiling": "string",
    "constraints": ["string"],
    "costWeight": 20,
    "pricingValidation": "string",
    "roleEffort": [{"role": "string", "hours": 10, "notes": "string"}],
    "confidence": 0.0
  },
  "risk": {
    "risks": [{"risk": "", "likelihood": "", "impact": "", "mitigation": ""}],
    "confidence": 0.0
  },
  "qa": {
    "approach": "string",
    "gates": ["string"],
    "confidence": 0.0
  },
  "communication": {
    "cadence": "string",
    "channels": ["string"],
    "reportingPlan": "string",
    "confidence": 0.0
  },
  "training": {
    "trainingPlan": "string",
    "transitionPlan": "string",
    "confidence": 0.0
  }
}

Rules:
- deliveryModel = HOW work happens (Agile/cadence). Do not list Discovery/UX phases there.
- methodology = WHAT work happens (phases). Typical: Discovery, UX, Design, Development, QA, Training, Launch.
- pricingStrategy ≠ pricingModel. Never invent exact dollar awards.
- deliveryPattern comes from won-proposal excerpts (patterns only — never copy marketing prose).
- No proposal prose.
"""

_EXECUTION_SYSTEM = """You are zö agency's Execution Planner.
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
No proposal prose. Do not invent named people — roles only.
Do NOT invent allocationPct / percent-time / FTE figures — leave allocationPct null
unless the RFP explicitly states a required %. Never copy static 10/35/25 grids.
"""

_WRITING_SYSTEM = """You are zö agency's Writing Intelligence agent.
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
  "patterns": [
    {
      "sectionId": "rfp-sec-1",
      "sourceWonProposals": ["filename or id"],
      "openingPattern": "string",
      "structureFlow": ["Challenge", "Approach", "Phases", "QA", "Outcomes"],
      "persuasionTechniques": ["string"],
      "commonDifferentiators": ["string"],
      "commonObjections": ["string"],
      "recommendedWordCount": 500,
      "recommendedVisuals": ["string"],
      "avoid": ["string"],
      "commonProofThemes": ["string"],
      "confidence": 0.0
    }
  ],
  "plans": [
    {
      "sectionId": "rfp-sec-1",
      "title": "string",
      "purpose": "string",
      "keyMessages": ["string"],
      "evaluationCriteria": ["string"],
      "evidenceNeeded": ["string"],
      "retrievalGoal": "string",
      "writerInstructions": "string",
      "successDefinition": "string",
      "wordBudget": 500,
      "tone": "executive",
      "register": "narrative",
      "audience": "string"
    }
  ],
  "entries": [
    {
      "sectionId": "rfp-sec-1",
      "requiredAssets": ["string"],
      "queries": ["Find zö agency case studies for … with measurable KPIs"],
      "priority": "required",
      "constraints": ["no marketing fluff"],
      "expectedSources": ["case_studies", "methodology"],
      "whyNeeded": "string"
    }
  ],
  "confidence": 0.0
}
expectedSources: won_proposals|case_studies|testimonials|references|methodology|
pricing|bios|company_facts|portfolio|images|diagrams|playbooks|standards
"""


def _as_dict(raw: dict[str, Any] | None, *keys: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    for key in keys:
        value = raw.get(key)
        if isinstance(value, dict):
            return value
    return {}


async def run_opportunity_extract(
    *,
    plan: ProposalExecutionPlan,
    rfp_context: str,
    rfp_meta: dict[str, str],
) -> ProposalExecutionPlan:
    """One call: understanding + compliance + scope + evaluation + success."""
    try:
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": _OPPORTUNITY_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Title: {rfp_meta.get('title', '')}\n"
                        f"Client: {rfp_meta.get('client', '')}\n"
                        f"Sector: {rfp_meta.get('sector', '')}\n"
                        f"Location: {rfp_meta.get('location') or 'N/A'}\n\n"
                        f"Full RFP text:\n{rfp_context[:100000]}"
                    ),
                },
            ],
            max_tokens=8192,
            temperature=0.1,
            node_name="opportunity_extract",
        )
    except LlmError as exc:
        raise IntelligenceError(f"Opportunity extract failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise IntelligenceError(f"Opportunity extract failed: {exc}") from exc

    if not isinstance(raw, dict) or not raw:
        raise IntelligenceError("Opportunity extract returned empty JSON")

    understanding_raw = _as_dict(raw, "understanding") or {
        k: v for k, v in raw.items() if k not in {
            "compliance", "scope", "evaluation", "successCriteria", "success_criteria"
        }
    }
    for key in UNDERSTANDING_FORBIDDEN_KEYS:
        understanding_raw.pop(key, None)
    memory_facts = (
        understanding_raw.pop("memoryFacts", None)
        or understanding_raw.pop("memory_facts", None)
        or {}
    )
    try:
        understanding = OpportunityUnderstanding.model_validate(understanding_raw)
    except Exception as exc:
        logger.warning("OpportunityUnderstanding validation failed: %s", exc)
        understanding = OpportunityUnderstanding(
            client=str(rfp_meta.get("client") or ""),
            industry=str(rfp_meta.get("sector") or ""),
            projectType="unknown",
            confidence=0.3,
        )
    understanding.confidence = clamp_confidence(understanding.confidence)
    if not understanding.client:
        understanding.client = str(rfp_meta.get("client") or "")
    if not understanding.client or not understanding.project_type:
        raise IntelligenceError(
            "Opportunity extract missing required client or projectType"
        )

    plan.opportunity.understanding = understanding
    plan = set_provider(plan, provider)
    facts = {
        "clientName": understanding.client,
        "organizationType": understanding.org_type,
        "projectType": understanding.project_type,
        "industry": understanding.industry,
    }
    if isinstance(memory_facts, dict):
        for key, value in memory_facts.items():
            if value:
                facts[str(key)] = str(value)
    plan = merge_memory(plan, UNDERSTANDING_AGENT, facts)
    plan = append_decision(
        plan,
        agent="opportunity_extract",
        decision_text=f"Normalized opportunity for {understanding.client}",
        reason=f"projectType={understanding.project_type}; complexity={understanding.complexity}",
        confidence=understanding.confidence,
    )

    plan = _apply_opportunity_slices(plan, raw, provider)
    return plan


def _apply_opportunity_slices(
    plan: ProposalExecutionPlan,
    raw: dict[str, Any],
    provider: str,
) -> ProposalExecutionPlan:
    slices: tuple[tuple[tuple[str, ...], type, Any, str, Any, Any], ...] = (
        (
            ("compliance",),
            ComplianceMatrix,
            lambda p, a: setattr(p.opportunity, "compliance", a),
            "compliance_mapping",
            lambda a: f"Mapped {len(a.items)} compliance items",
            "Built compliance matrix from RFP understanding",
        ),
        (
            ("scope",),
            ScopeAnalysis,
            lambda p, a: setattr(p.opportunity, "scope", a),
            "scope_analysis",
            lambda a: f"Scope: {len(a.mandatory)} mandatory deliverables",
            "Separated mandatory/optional/out-of-scope work",
        ),
        (
            ("evaluation",),
            EvaluationAnalysis,
            lambda p, a: setattr(p.opportunity, "evaluation", a),
            "evaluation_criteria",
            lambda a: (
                f"Primary evaluation emphasis: {a.emphasis[0] if a.emphasis else 'unspecified'}"
            ),
            lambda a: f"{len(a.criteria)} scored criteria extracted",
        ),
        (
            ("successCriteria", "success_criteria"),
            SuccessCriteriaResult,
            lambda p, a: setattr(p.opportunity, "success_criteria", a),
            "success_criteria",
            lambda a: f"Extracted {len(a.items)} success criteria",
            "Defined recurring proposal themes from client success definition",
        ),
    )
    for keys, model_cls, assign, agent, decision_text, reason in slices:
        plan = _apply_validated(
            plan,
            raw=_as_dict(raw, *keys),
            model_cls=model_cls,
            assign=assign,
            agent=agent,
            decision_text=decision_text,
            reason=reason,
            provider=provider,
        )
    return plan


async def run_strategy_delivery(
    *,
    plan: ProposalExecutionPlan,
    rfp_meta: dict[str, str],
) -> ProposalExecutionPlan:
    """One call: strategy + delivery pattern/model + methodology/budget/risk/qa/comms/training."""
    import asyncio

    u = plan.opportunity.understanding
    query_base = f"{u.industry} {u.org_type} {u.project_type} {rfp_meta.get('sector', '')}"
    won_hits, method_hits, price_hits, playbook_hits, qa_hits = await asyncio.gather(
        retrieve_intelligence(
            "won_patterns",
            query=f"{query_base} won proposal delivery pattern",
            limit=6,
        ),
        retrieve_intelligence(
            "methodology",
            query=f"{u.project_type} website methodology delivery phases",
            limit=5,
        ),
        retrieve_intelligence(
            "pricing",
            query="zö agency pricing guide rate card cost model",
            limit=5,
        ),
        retrieve_intelligence(
            "playbooks",
            query="project risk communication training playbook",
            limit=4,
        ),
        retrieve_intelligence(
            "standards",
            query="QA standards accessibility quality gates",
            limit=4,
        ),
    )
    won_excerpts = [
        {"source": h.get("source"), "excerpt": str(h.get("excerpt") or "")[:1200]}
        for h in won_hits
    ]
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": _STRATEGY_DELIVERY_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Understanding:\n{u.model_dump_json()}\n"
                    f"Scope:\n{plan.opportunity.scope.model_dump_json()}\n"
                    f"Evaluation:\n{plan.opportunity.evaluation.model_dump_json()}\n"
                    f"Success:\n{plan.opportunity.success_criteria.model_dump_json()}\n"
                    f"Budget intel:\n{u.budget_intel.model_dump_json()}\n\n"
                    f"Won-proposal pattern excerpts (patterns only):\n"
                    f"{json.dumps(won_excerpts, indent=2)[:12000]}\n\n"
                    f"Methodology intel:\n{json.dumps(method_hits, indent=2)[:8000]}\n\n"
                    f"Pricing knowledge:\n{json.dumps(price_hits, indent=2)[:8000]}\n\n"
                    f"Playbook/standards intel:\n"
                    f"{json.dumps(playbook_hits + qa_hits, indent=2)[:8000]}"
                ),
            },
        ],
        max_tokens=8192,
        agent_name="strategy_delivery",
    )

    plan = _apply_validated(
        plan,
        raw=_as_dict(raw, "strategy"),
        model_cls=OpportunityStrategy,
        assign=lambda p, a: setattr(p.opportunity, "strategy", a),
        agent="opportunity_strategy",
        decision_text=lambda a: f"Winning theme: {a.winning_theme or '(unset)'}",
        reason=lambda a: (a.why_us[:200] if a.why_us else "Strategy from opportunity intel"),
        provider=provider,
        after=lambda p, a: (
            merge_memory(p, "opportunity_strategy", {"winningTheme": a.winning_theme})
            if a.winning_theme
            else p
        ),
    )
    plan.metadata.layer_status.opportunity = "complete"

    pattern_raw = _as_dict(raw, "deliveryPattern", "delivery_pattern")
    model_raw = _as_dict(raw, "deliveryModel", "delivery_model")
    try:
        pattern = DeliveryPattern.model_validate(pattern_raw)
    except Exception:
        pattern = DeliveryPattern(confidence=0.2)
    try:
        model = DeliveryModel.model_validate(model_raw)
    except Exception:
        model = DeliveryModel(confidence=0.2)
    if won_hits:
        pattern.source_won_proposals = list(
            dict.fromkeys(
                list(pattern.source_won_proposals)
                + [str(h.get("source") or "") for h in won_hits if h.get("source")]
            )
        )
    pattern.confidence = clamp_confidence(pattern.confidence)
    model.confidence = clamp_confidence(model.confidence)
    plan.delivery.delivery_pattern = pattern
    plan.delivery.delivery_model = model
    plan.metadata.won_patterns_used = list(pattern.source_won_proposals)[:12]
    plan = set_provider(plan, provider)
    if model.type:
        plan = merge_memory(plan, "delivery_pattern", {"deliveryApproach": model.type})
    plan = append_decision(
        plan,
        agent="delivery_pattern",
        decision_text=f"Delivery model: {model.type or 'unspecified'}",
        reason=f"Patterns from {len(won_hits)} won-proposal hits",
        confidence=min(pattern.confidence, model.confidence) if model.type else pattern.confidence,
    )

    plan = _apply_validated(
        plan,
        raw=_as_dict(raw, "methodology"),
        model_cls=MethodologyPlan,
        assign=lambda p, a: setattr(p.delivery, "methodology", a),
        agent="methodology_planner",
        decision_text=lambda a: f"Methodology phases: {len(a.phases)}",
        reason=lambda a: ", ".join(ph.name for ph in a.phases[:6]) or "default empty",
        provider=provider,
    )
    plan = _apply_validated(
        plan,
        raw=_as_dict(raw, "budget"),
        model_cls=BudgetPlan,
        assign=lambda p, a: setattr(p.delivery, "budget", a),
        agent="budget_planner",
        decision_text=lambda a: f"Pricing model: {a.pricing_model or 'unset'}",
        reason=lambda a: f"Strategy: {a.pricing_strategy or 'unset'}",
        provider=provider,
        after=lambda p, a: (
            merge_memory(p, "budget_planner", {"pricingModel": a.pricing_model})
            if a.pricing_model
            else p
        ),
    )
    plan = _apply_validated(
        plan,
        raw=_as_dict(raw, "risk"),
        model_cls=RiskPlan,
        assign=lambda p, a: setattr(p.delivery, "risk", a),
        agent="risk_planner",
        decision_text=lambda a: f"Risks identified: {len(a.risks)}",
        reason=_PLAYBOOK_REASON,
        provider=provider,
    )
    plan = _apply_validated(
        plan,
        raw=_as_dict(raw, "qa"),
        model_cls=QaPlan,
        assign=lambda p, a: setattr(p.delivery, "qa", a),
        agent="qa_planner",
        decision_text=lambda a: f"QA gates: {len(a.gates)}",
        reason=_STANDARDS_REASON,
        provider=provider,
    )
    plan = _apply_validated(
        plan,
        raw=_as_dict(raw, "communication"),
        model_cls=CommunicationPlan,
        assign=lambda p, a: setattr(p.delivery, "communication", a),
        agent="communication_planner",
        decision_text=lambda a: f"Comm cadence: {a.cadence or 'unset'}",
        reason=_PLAYBOOK_REASON,
        provider=provider,
    )
    plan = _apply_validated(
        plan,
        raw=_as_dict(raw, "training"),
        model_cls=TrainingPlan,
        assign=lambda p, a: setattr(p.delivery, "training", a),
        agent="training_planner",
        decision_text=lambda a: "Training/transition planned",
        reason=_PLAYBOOK_REASON,
        provider=provider,
    )
    return plan


async def run_execution_plan(
    *,
    plan: ProposalExecutionPlan,
    rfp_meta: dict[str, str] | None = None,
) -> ProposalExecutionPlan:
    """One call: WBS + timeline + resources."""
    _ = rfp_meta
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": _EXECUTION_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Scope:\n{plan.opportunity.scope.model_dump_json()}\n"
                    f"Timeline intel:\n{plan.opportunity.understanding.timeline_intel.model_dump_json()}\n"
                    f"Methodology:\n{plan.delivery.methodology.model_dump_json()}\n"
                    f"Delivery model:\n{plan.delivery.delivery_model.model_dump_json()}\n"
                    f"Budget roleEffort:\n{plan.delivery.budget.model_dump_json()}"
                ),
            },
        ],
        max_tokens=4096,
        agent_name="execution_plan",
    )
    plan = _apply_validated(
        plan,
        raw=_as_dict(raw, "workBreakdown", "work_breakdown"),
        model_cls=WorkBreakdown,
        assign=lambda p, a: setattr(p.delivery, "work_breakdown", a),
        agent="work_breakdown_planner",
        decision_text=lambda a: f"Work packages: {len(a.packages)}",
        reason="Decomposed scope against methodology phases",
        provider=provider,
    )
    plan = _apply_validated(
        plan,
        raw=_as_dict(raw, "timeline"),
        model_cls=TimelinePlan,
        assign=lambda p, a: setattr(p.delivery, "timeline", a),
        agent="timeline_planner",
        decision_text=lambda a: f"Milestones: {len(a.milestones)}",
        reason=lambda a: f"Go-live: {a.go_live or 'unset'}",
        provider=provider,
    )
    plan = _apply_validated(
        plan,
        raw=_as_dict(raw, "resources"),
        model_cls=ResourcePlan,
        assign=lambda p, a: setattr(p.delivery, "resources", a),
        agent="resource_planner",
        decision_text=lambda a: f"Role allocations: {len(a.allocations)}",
        reason="Allocated roles across phases from WBS/timeline",
        provider=provider,
    )
    plan.metadata.layer_status.delivery = "complete"
    return plan


async def run_writing_briefs(
    *,
    plan: ProposalExecutionPlan,
    rfp_meta: dict[str, str] | None = None,
) -> ProposalExecutionPlan:
    """One call: winning patterns + writer briefs + retrieval queries."""
    outline_sections = plan.writing.proposal_outline.sections
    if not outline_sections:
        return plan

    from app.services.proposal_intelligence.agents.section_strategy_planner import (
        _parse_page_limit,
    )

    hits = await retrieve_intelligence("won_patterns", query=_query_for_plan(plan), limit=5)
    excerpts = [
        {"source": h.get("source"), "excerpt": str(h.get("excerpt") or "")[:1400]}
        for h in hits
    ]
    source_names = [
        str(h.get("source") or "").strip() for h in hits if str(h.get("source") or "").strip()
    ]
    page_limit = _parse_page_limit(rfp_meta)
    page_limit_line = (
        f"Proposal page limit from RFP: {page_limit} pages "
        f"(~{page_limit * 350} narrative words total including static Sections 1–3). "
        "Keep every wordBudget tight so the full package fits."
        if page_limit
        else "No reliable proposal page limit detected — still keep wordBudgets lean (250–500)."
    )
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": _WRITING_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"{page_limit_line}\n\n"
                    f"Opportunity:\n{plan.opportunity.understanding.model_dump_json()}\n"
                    f"Outline:\n{plan.writing.proposal_outline.model_dump_json()}\n"
                    f"Strategy:\n{plan.opportunity.strategy.model_dump_json()}\n"
                    f"Evaluation:\n{plan.opportunity.evaluation.model_dump_json()}\n"
                    f"Success:\n{plan.opportunity.success_criteria.model_dump_json()}\n"
                    f"Delivery methodology:\n{plan.delivery.methodology.model_dump_json()}\n"
                    f"Proof strategy:\n{plan.opportunity.strategy.proof_strategy}\n\n"
                    "Won proposal excerpts for pattern extraction only. "
                    "Do not return or paraphrase their text:\n"
                    f"{json.dumps(excerpts, indent=2)}"
                ),
            },
        ],
        max_tokens=8192,
        agent_name="writing_briefs",
    )
    if not isinstance(raw, dict):
        raw = {}

    assignments = _assignments_from_raw(raw, source_names)
    pattern_plans = _merge_patterns_into_section_plans(plan, assignments, source_names)
    confidence = _pattern_confidence(raw, pattern_plans)
    from app.services.proposal_intelligence.schemas import SectionPlans

    plan.writing.section_plans = SectionPlans(plans=pattern_plans, confidence=confidence)
    plan.metadata.won_patterns_used = list(
        dict.fromkeys(plan.metadata.won_patterns_used + source_names)
    )[:12]
    plan = append_decision(
        plan,
        agent="winning_pattern_intelligence",
        decision_text=f"Winning writing patterns: {len(assignments)} section(s)",
        reason=f"Pattern-only extraction from {len(hits)} won-proposal hit(s)",
        confidence=confidence,
    )

    plans_raw = raw.get("plans")
    plan = apply_section_strategy_from_raw(
        plan,
        {"plans": plans_raw, "confidence": raw.get("confidence")}
        if isinstance(plans_raw, list)
        else raw,
        provider=provider,
        rfp_meta=rfp_meta,
    )
    plan = apply_retrieval_plan_from_raw(
        plan,
        {"entries": raw.get("entries"), "confidence": raw.get("confidence")},
        provider=provider,
    )
    return plan


def _apply_validated(
    plan: ProposalExecutionPlan,
    *,
    raw: dict[str, Any],
    model_cls: type,
    assign: Any,
    agent: str,
    decision_text: Any,
    reason: Any,
    provider: str,
    after: Any = None,
) -> ProposalExecutionPlan:
    try:
        artifact = model_cls.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", agent, exc)
        artifact = model_cls(confidence=0.2)
    if hasattr(artifact, "confidence"):
        artifact.confidence = clamp_confidence(artifact.confidence)
    assign(plan, artifact)
    if after:
        plan = after(plan, artifact)
    plan = set_provider(plan, provider)
    text = decision_text(artifact) if callable(decision_text) else decision_text
    why = reason(artifact) if callable(reason) else reason
    return append_decision(
        plan,
        agent=agent,
        decision_text=text,
        reason=why,
        confidence=float(getattr(artifact, "confidence", 0.0) or 0.0),
    )
