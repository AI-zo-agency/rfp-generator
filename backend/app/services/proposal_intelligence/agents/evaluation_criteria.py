"""Evaluation Criteria Agent."""

from __future__ import annotations

import logging

from app.services.proposal_evaluation_coverage import (
    backfill_evaluation_response_limits,
    sanitize_evaluation_criteria_names,
)
from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.schemas import EvaluationAnalysis, ProposalExecutionPlan

logger = logging.getLogger(__name__)
AGENT = "evaluation_criteria"

_SYSTEM = """You are the Evaluation Criteria Agent. Transcribe how the proposal will be scored.
This is the scoreboard that drives the proposal outline — extract it completely.

Return JSON only — no proposal prose. Emit the scalar fields FIRST so a
truncated response still carries them:
{
  "scoredResponseForm": true,
  "totalPoints": 1000,
  "responseCharLimit": 4000,
  "writingStyle": "executive|technical|mixed",
  "emphasis": ["Methodology", "Experience"],
  "confidence": 0.0,
  "criteria": [
    {
      "name": "Strategic Planning",
      "itemCode": "SECTION III",
      "weight": 160,
      "priorityRank": 1,
      "responseCharLimit": 4000,
      "items": [
        {"itemCode": "III.1", "ask": "verbatim scored ask", "weight": 40, "responseCharLimit": 4000}
      ]
    }
  ]
}

Rules:
- One "criteria" entry per SCORED PARENT SECTION, using the buyer's own heading and that
  section's TOTAL points. Put the buyer's label in itemCode ("SECTION III", "Tab 4", "B.2").
- Every numbered sub-ask goes in "items" with its itemCode, its points, and "ask" = the
  RFP's OWN wording of what to describe (verbatim, not a paraphrase).
- Set scoredResponseForm true when the RFP publishes an evaluation-criteria response form
  whose sections ARE the required proposal sections. Set totalPoints to the stated maximum.
- responseCharLimit: the per-response field cap the RFP states, at the level it states it.
  Omit when the RFP states no character cap.
- List EVERY scored section including pricing/economy. Never merge two scored sections into
  one entry, never drop one for looking administrative, never invent a weight.
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
        # A published criteria form can carry 25+ numbered asks quoted verbatim.
        # 2048 was sized for the old flat {name, weight} shape and truncated
        # this RFP's response mid-array — the failure mode that loses scored
        # sections in the first place.
        max_tokens=8192,
        agent_name=AGENT,
    )
    try:
        evaluation = EvaluationAnalysis.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", AGENT, exc)
        evaluation = EvaluationAnalysis(confidence=0.2)
    evaluation.confidence = clamp_confidence(evaluation.confidence)
    backfill_evaluation_response_limits(evaluation, rfp_context)
    sanitize_evaluation_criteria_names(evaluation)
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
