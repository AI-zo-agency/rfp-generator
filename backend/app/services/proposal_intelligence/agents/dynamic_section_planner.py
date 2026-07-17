"""Dynamic Section Planner — nested proposal outline."""

from __future__ import annotations

import logging

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan, ProposalOutline

logger = logging.getLogger(__name__)
AGENT = "dynamic_section_planner"

_SYSTEM = """Dynamic Section Planner. Decide which proposal sections must be generated.
Support nested hierarchy (4, 4.1, 4.2). Return JSON only:
{
  "sections": [
    {
      "id": "rfp-sec-1",
      "title": "Executive Summary",
      "order": 1,
      "required": true,
      "conditionalReason": "",
      "parentId": null,
      "children": [],
      "dependencies": []
    }
  ],
  "confidence": 0.0
}
Include mandatory sections and conditional ones justified by RFP.
Do not write section prose.
"""


async def run_dynamic_section_planner(
    *,
    plan: ProposalExecutionPlan,
    rfp_context: str,
    rfp_meta: dict[str, str] | None = None,
) -> ProposalExecutionPlan:
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Understanding:\n{plan.opportunity.understanding.model_dump_json()}\n"
                    f"Compliance item count: {len(plan.opportunity.compliance.items)}\n"
                    f"Evaluation:\n{plan.opportunity.evaluation.model_dump_json()}\n"
                    f"Scope:\n{plan.opportunity.scope.model_dump_json()}\n"
                    f"RFP excerpt (structure/TOC):\n{rfp_context[:50000]}"
                ),
            },
        ],
        max_tokens=3072,
        agent_name=AGENT,
    )
    try:
        outline = ProposalOutline.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", AGENT, exc)
        outline = ProposalOutline(confidence=0.2)
    if not outline.sections:
        outline = ProposalOutline(
            sections=[
                {"id": "rfp-sec-1", "title": "Executive Summary", "order": 1, "required": True},
                {"id": "rfp-sec-2", "title": "Understanding of Requirements", "order": 2, "required": True},
                {"id": "rfp-sec-3", "title": "Methodology", "order": 3, "required": True},
                {"id": "rfp-sec-4", "title": "Timeline", "order": 4, "required": True},
                {"id": "rfp-sec-5", "title": "Budget", "order": 5, "required": True},
            ],
            confidence=0.35,
        )
    outline.confidence = clamp_confidence(outline.confidence)
    plan.writing.proposal_outline = outline
    plan = set_provider(plan, provider)
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Outline sections: {len(outline.sections)}",
        reason="Dynamic section plan from RFP structure + evaluation",
        confidence=outline.confidence,
    )
    return plan
