"""Section Strategy Planner — writer briefs per section."""

from __future__ import annotations

import logging

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan, SectionPlans

logger = logging.getLogger(__name__)
AGENT = "section_strategy_planner"

_SYSTEM = """Section Strategy Planner. For each outline section, define the writer brief.
Return JSON only:
{
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
      "wordBudget": 800,
      "tone": "executive",
      "register": "narrative",
      "audience": "string"
    }
  ],
  "confidence": 0.0
}
No proposal prose — strategy only.
"""


async def run_section_strategy_planner(
    *,
    plan: ProposalExecutionPlan,
    rfp_meta: dict[str, str] | None = None,
) -> ProposalExecutionPlan:
    _ = rfp_meta
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Outline:\n{plan.writing.proposal_outline.model_dump_json()}\n"
                    f"Strategy:\n{plan.opportunity.strategy.model_dump_json()}\n"
                    f"Evaluation:\n{plan.opportunity.evaluation.model_dump_json()}\n"
                    f"Success:\n{plan.opportunity.success_criteria.model_dump_json()}\n"
                    f"Delivery methodology:\n{plan.delivery.methodology.model_dump_json()}"
                ),
            },
        ],
        max_tokens=4096,
        agent_name=AGENT,
    )
    try:
        plans = SectionPlans.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", AGENT, exc)
        plans = SectionPlans(confidence=0.2)
    if not plans.plans:
        plans = SectionPlans(
            plans=[
                {
                    "sectionId": s.id,
                    "title": s.title,
                    "purpose": f"Address {s.title}",
                    "successDefinition": f"Evaluator understands {s.title}",
                    "retrievalGoal": "Relevant KB evidence for this section",
                    "writerInstructions": "Follow Proposal Execution Plan; do not invent facts.",
                    "wordBudget": 800,
                    "tone": plan.opportunity.strategy.tone or "executive",
                    "register": "narrative",
                }
                for s in plan.writing.proposal_outline.sections
            ],
            confidence=0.35,
        )
    existing_patterns = {
        p.section_id: p.winning_pattern for p in plan.writing.section_plans.plans
    }
    for section_plan in plans.plans:
        existing = existing_patterns.get(section_plan.section_id)
        if existing and not section_plan.winning_pattern.confidence:
            section_plan.winning_pattern = existing
    plans.confidence = clamp_confidence(plans.confidence)
    plan.writing.section_plans = plans
    plan = set_provider(plan, provider)
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Section strategies: {len(plans.plans)}",
        reason="Writer briefs with purpose/success/retrievalGoal",
        confidence=plans.confidence,
    )
    return plan
