"""Success Criteria Agent."""

from __future__ import annotations

import logging

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan, SuccessCriteriaResult

logger = logging.getLogger(__name__)
AGENT = "success_criteria"

_SYSTEM = """You are the Success Criteria Agent. Extract what success looks like for the client.
Return JSON only — no proposal prose:
{
  "items": [
    {"criterion": "string", "why": "string", "recurringTheme": true}
  ],
  "confidence": 0.0
}
Mark recurringTheme true for themes that should echo across the proposal.
"""


async def run_success_criteria(
    *,
    plan: ProposalExecutionPlan,
    rfp_context: str,
    rfp_meta: dict[str, str],
) -> ProposalExecutionPlan:
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Understanding:\n{plan.opportunity.understanding.model_dump_json()}\n"
                    f"Evaluation:\n{plan.opportunity.evaluation.model_dump_json()}\n\n"
                    f"RFP excerpt:\n{rfp_context[:30000]}"
                ),
            },
        ],
        max_tokens=2048,
        agent_name=AGENT,
    )
    try:
        result = SuccessCriteriaResult.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", AGENT, exc)
        result = SuccessCriteriaResult(confidence=0.2)
    result.confidence = clamp_confidence(result.confidence)
    plan.opportunity.success_criteria = result
    plan = set_provider(plan, provider)
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Extracted {len(result.items)} success criteria",
        reason="Defined recurring proposal themes from client success definition",
        confidence=result.confidence,
    )
    return plan
