"""Work Breakdown Planner."""

from __future__ import annotations

import logging

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan, WorkBreakdown

logger = logging.getLogger(__name__)
AGENT = "work_breakdown_planner"

_SYSTEM = """Work Breakdown Planner. Decompose scope into work packages by methodology phase.
Return JSON only:
{
  "packages": [
    {"workPackage": "string", "phase": "Discovery", "deliverables": ["string"]}
  ],
  "confidence": 0.0
}
No proposal prose.
"""


async def run_work_breakdown_planner(
    *,
    plan: ProposalExecutionPlan,
    rfp_meta: dict[str, str] | None = None,
) -> ProposalExecutionPlan:
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Scope:\n{plan.opportunity.scope.model_dump_json()}\n"
                    f"Methodology:\n{plan.delivery.methodology.model_dump_json()}"
                ),
            },
        ],
        max_tokens=2048,
        agent_name=AGENT,
    )
    try:
        wbs = WorkBreakdown.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", AGENT, exc)
        wbs = WorkBreakdown(confidence=0.2)
    wbs.confidence = clamp_confidence(wbs.confidence)
    plan.delivery.work_breakdown = wbs
    plan = set_provider(plan, provider)
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Work packages: {len(wbs.packages)}",
        reason="Decomposed scope against methodology phases",
        confidence=wbs.confidence,
    )
    return plan
