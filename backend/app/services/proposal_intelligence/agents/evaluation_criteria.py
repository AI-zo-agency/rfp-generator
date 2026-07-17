"""Evaluation Criteria Agent."""

from __future__ import annotations

import logging

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.schemas import EvaluationAnalysis, ProposalExecutionPlan

logger = logging.getLogger(__name__)
AGENT = "evaluation_criteria"

_SYSTEM = """You are the Evaluation Criteria Agent. Analyze how the proposal will be scored.
Return JSON only — no proposal prose:
{
  "criteria": [{"name": "string", "weight": 25, "priorityRank": 1}],
  "emphasis": ["Methodology", "Experience"],
  "writingStyle": "executive|technical|mixed",
  "confidence": 0.0
}
Include weighting when available in the RFP.
"""


async def run_evaluation_criteria(
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
                    f"Understanding:\n{plan.opportunity.understanding.model_dump_json()}\n\n"
                    f"RFP excerpt:\n{rfp_context[:40000]}"
                ),
            },
        ],
        max_tokens=2048,
        agent_name=AGENT,
    )
    try:
        evaluation = EvaluationAnalysis.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", AGENT, exc)
        evaluation = EvaluationAnalysis(confidence=0.2)
    evaluation.confidence = clamp_confidence(evaluation.confidence)
    plan.opportunity.evaluation = evaluation
    plan = set_provider(plan, provider)
    top = evaluation.emphasis[0] if evaluation.emphasis else "unspecified"
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Primary evaluation emphasis: {top}",
        reason=f"{len(evaluation.criteria)} scored criteria extracted",
        confidence=evaluation.confidence,
    )
    return plan
