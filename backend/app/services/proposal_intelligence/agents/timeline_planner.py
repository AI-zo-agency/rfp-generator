"""Timeline Planner."""

from __future__ import annotations

import logging

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan, TimelinePlan

logger = logging.getLogger(__name__)
AGENT = "timeline_planner"

_SYSTEM = """Timeline Planner. Sequence phases and milestones using project constraints.

Rules (any RFP — judge by meaning from timelineIntel + methodology, not keyword lists):
- If the SOW says the schedule is TBD / determined after selection / contractor proposes
  for buyer approval: offsets are PROPOSED only — never write as settled or binding.
- Prefer FIXED calendar / funding / performance end dates stated in THIS RFP over
  counting forward "Month N" from an undetermined award start. Back-calculate from
  that end when start is TBD.
- If only a multi-year term is stated with a known start, use that horizon honestly.
- When award start is TBD and a hard end date exists, put the end date in goLive /
  milestone offsets (or "by <date>") — do not invent a rigid Month-N grid past the
  funding or performance end.

Return JSON only:
{
  "milestones": [{"name": "string", "offset": "Week 2 | by <RFP end date> | proposed Month 6", "dependsOn": ["string"]}],
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
