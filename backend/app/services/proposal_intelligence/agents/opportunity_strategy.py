"""Opportunity Strategy Agent — winning theme and positioning decisions only."""

from __future__ import annotations

import logging

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, merge_memory, set_provider
from app.services.proposal_intelligence.schemas import OpportunityStrategy, ProposalExecutionPlan

logger = logging.getLogger(__name__)
AGENT = "opportunity_strategy"

_SYSTEM = """You are the Opportunity Strategy Agent (proposal strategist).
Decide how to win. Do NOT write proposal sections. Return JSON only:
{
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
}
"""


async def run_opportunity_strategy(
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
                    f"Scope:\n{plan.opportunity.scope.model_dump_json()}\n"
                    f"Evaluation:\n{plan.opportunity.evaluation.model_dump_json()}\n"
                    f"Success:\n{plan.opportunity.success_criteria.model_dump_json()}"
                ),
            },
        ],
        max_tokens=3072,
        agent_name=AGENT,
    )
    try:
        strategy = OpportunityStrategy.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", AGENT, exc)
        strategy = OpportunityStrategy(confidence=0.2)
    strategy.confidence = clamp_confidence(strategy.confidence)
    plan.opportunity.strategy = strategy
    plan.metadata.layer_status.opportunity = "complete"
    plan = set_provider(plan, provider)
    if strategy.winning_theme:
        plan = merge_memory(plan, AGENT, {"winningTheme": strategy.winning_theme})
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Winning theme: {strategy.winning_theme or '(unset)'}",
        reason=strategy.why_us[:200] if strategy.why_us else "Strategy from opportunity intel",
        confidence=strategy.confidence,
    )
    return plan
