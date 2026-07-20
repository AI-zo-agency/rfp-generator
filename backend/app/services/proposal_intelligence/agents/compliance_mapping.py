"""Compliance Mapping Agent."""

from __future__ import annotations

import logging

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.schemas import ComplianceMatrix, ProposalExecutionPlan

logger = logging.getLogger(__name__)
AGENT = "compliance_mapping"

_SYSTEM = """You are the Compliance Mapping Agent. Consume RFP understanding JSON.
Build a compliance matrix from the FULL submission checklist (documents to be submitted,
forms to return, vendor qualification narratives, addenda acknowledgement).
Return JSON only — no proposal prose:
{
  "items": [
    {
      "id": "comp-1",
      "requirement": "string",
      "mandatory": true,
      "sourceRef": "string",
      "targetSection": "string",
      "evidenceNeeded": "string",
      "status": "open",
      "owner": "string"
    }
  ],
  "confidence": 0.0
}
Include: addenda ack, financial stability, awards, each named compliance form, references, pricing attachment format.
"""


async def run_compliance_mapping(
    *,
    plan: ProposalExecutionPlan,
    rfp_context: str,
    rfp_meta: dict[str, str],
) -> ProposalExecutionPlan:
    understanding = plan.opportunity.understanding.model_dump(by_alias=True)
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Understanding:\n{understanding}\n\n"
                    f"RFP excerpt (for requirement sourcing):\n{rfp_context[:40000]}"
                ),
            },
        ],
        max_tokens=3072,
        agent_name=AGENT,
    )
    try:
        matrix = ComplianceMatrix.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", AGENT, exc)
        matrix = ComplianceMatrix(confidence=0.2)
    matrix.confidence = clamp_confidence(matrix.confidence)
    plan.opportunity.compliance = matrix
    plan = set_provider(plan, provider)
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Mapped {len(matrix.items)} compliance items",
        reason="Built compliance matrix from RFP understanding",
        confidence=matrix.confidence,
    )
    return plan
