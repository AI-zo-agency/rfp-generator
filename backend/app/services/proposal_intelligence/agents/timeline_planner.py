"""Timeline Planner."""

from __future__ import annotations

import logging

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan, TimelinePlan

logger = logging.getLogger(__name__)
AGENT = "timeline_planner"

_SYSTEM = """Timeline Planner. Sequence phases and milestones using project constraints.
Return JSON only:
{
  "milestones": [{"name": "string", "offset": "Week 2", "dependsOn": ["string"]}],
  "goLive": "string",
  "reviewCycles": "string",
  "confidence": 0.0
}
No proposal prose.
"""


async def run_timeline_planner(
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
                    f"Timeline intel:\n{plan.opportunity.understanding.timeline_intel.model_dump_json()}\n"
                    f"Methodology:\n{plan.delivery.methodology.model_dump_json()}\n"
                    f"WBS:\n{plan.delivery.work_breakdown.model_dump_json()}\n"
                    f"Delivery model:\n{plan.delivery.delivery_model.model_dump_json()}"
                ),
            },
        ],
        max_tokens=2048,
        agent_name=AGENT,
    )
    try:
        timeline = TimelinePlan.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", AGENT, exc)
        timeline = TimelinePlan(confidence=0.2)
    timeline.confidence = clamp_confidence(timeline.confidence)
    plan.delivery.timeline = timeline
    plan = set_provider(plan, provider)
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Milestones: {len(timeline.milestones)}",
        reason=f"Go-live: {timeline.go_live or 'unset'}",
        confidence=timeline.confidence,
    )
    return plan
