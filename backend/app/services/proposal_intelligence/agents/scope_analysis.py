"""Scope Analysis Agent."""

from __future__ import annotations

import logging

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan, ScopeAnalysis

logger = logging.getLogger(__name__)
AGENT = "scope_analysis"

_SYSTEM = """You are the Scope Analysis Agent. Determine actual work to deliver.
Return JSON only — no proposal prose:
{
  "mandatory": ["string"],
  "optional": ["string"],
  "futurePhases": ["string"],
  "outOfScope": ["string"],
  "dependencies": ["string"],
  "confidence": 0.0
}
"""


async def run_scope_analysis(
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
        scope = ScopeAnalysis.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", AGENT, exc)
        scope = ScopeAnalysis(confidence=0.2)
    scope.confidence = clamp_confidence(scope.confidence)
    plan.opportunity.scope = scope
    plan = set_provider(plan, provider)
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Scope: {len(scope.mandatory)} mandatory deliverables",
        reason="Separated mandatory/optional/out-of-scope work",
        confidence=scope.confidence,
    )
    return plan
