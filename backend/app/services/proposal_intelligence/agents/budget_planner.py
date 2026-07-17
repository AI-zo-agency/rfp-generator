"""Budget Planner — pricing strategy vs pricing model."""

from __future__ import annotations

import json
import logging

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, merge_memory, set_provider
from app.services.proposal_intelligence.retrieval import retrieve_intelligence
from app.services.proposal_intelligence.schemas import BudgetPlan, ProposalExecutionPlan

logger = logging.getLogger(__name__)
AGENT = "budget_planner"

_SYSTEM = """You are the Budget Planner for proposal intelligence.
pricingStrategy ≠ pricingModel (e.g. Strategy=Compete aggressively, Model=Fixed Fee).
Return planning JSON only — no fee tables for submission, no proposal prose:
{
  "pricingStrategy": "string",
  "pricingModel": "Fixed Fee|T&M|Hybrid|Other",
  "pricingTier": "string",
  "contractType": "string",
  "ceiling": "string",
  "constraints": ["string"],
  "costWeight": 20,
  "pricingValidation": "string",
  "roleEffort": [{"role": "string", "hours": 10, "notes": "string"}],
  "confidence": 0.0
}
Use pricing intelligence excerpts and RFP budget intel. Never invent exact dollar awards.
"""


async def run_budget_planner(
    *,
    plan: ProposalExecutionPlan,
    rfp_meta: dict[str, str] | None = None,
) -> ProposalExecutionPlan:
    hits = await retrieve_intelligence(
        "pricing",
        query="zö agency pricing guide rate card cost model",
        limit=5,
    )
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Budget intel:\n{plan.opportunity.understanding.budget_intel.model_dump_json()}\n"
                    f"Evaluation:\n{plan.opportunity.evaluation.model_dump_json()}\n"
                    f"Scope:\n{plan.opportunity.scope.model_dump_json()}\n"
                    f"Pricing knowledge:\n{json.dumps(hits, indent=2)[:12000]}"
                ),
            },
        ],
        max_tokens=2048,
        agent_name=AGENT,
    )
    try:
        budget = BudgetPlan.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", AGENT, exc)
        budget = BudgetPlan(confidence=0.2)
    budget.confidence = clamp_confidence(budget.confidence)
    plan.delivery.budget = budget
    plan = set_provider(plan, provider)
    if budget.pricing_model:
        plan = merge_memory(plan, AGENT, {"pricingModel": budget.pricing_model})
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Pricing model: {budget.pricing_model or 'unset'}",
        reason=f"Strategy: {budget.pricing_strategy or 'unset'}",
        confidence=budget.confidence,
    )
    return plan
