"""Resource Planner."""

from __future__ import annotations

import logging

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan, ResourcePlan

logger = logging.getLogger(__name__)
AGENT = "resource_planner"

_SYSTEM = """Resource Planner. Allocate roles and effort by phase.
Return JSON only:
{
  "allocations": [{"role": "string", "allocationPct": 50, "phase": "string"}],
  "confidence": 0.0
}
No proposal prose. Do not invent named people — roles only.
"""


async def run_resource_planner(
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
                    f"WBS:\n{plan.delivery.work_breakdown.model_dump_json()}\n"
                    f"Methodology:\n{plan.delivery.methodology.model_dump_json()}\n"
                    f"Timeline:\n{plan.delivery.timeline.model_dump_json()}\n"
                    f"Budget roleEffort:\n{plan.delivery.budget.model_dump_json()}"
                ),
            },
        ],
        max_tokens=1536,
        agent_name=AGENT,
    )
    try:
        resources = ResourcePlan.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", AGENT, exc)
        resources = ResourcePlan(confidence=0.2)
    resources.confidence = clamp_confidence(resources.confidence)
    plan.delivery.resources = resources
    plan.metadata.layer_status.delivery = "complete"
    plan = set_provider(plan, provider)
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Role allocations: {len(resources.allocations)}",
        reason="Allocated roles across phases from WBS/timeline",
        confidence=resources.confidence,
    )
    return plan
