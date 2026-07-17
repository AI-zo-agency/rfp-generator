"""RFP Understanding Agent — normalize full RFP into structured opportunity (critical path)."""

from __future__ import annotations

import logging

from app.services import llm
from app.services.llm import LlmError
from app.services.proposal_intelligence.agent_base import clamp_confidence
from app.services.proposal_intelligence.plan_ops import (
    IntelligenceError,
    append_decision,
    merge_memory,
    set_provider,
)
from app.services.proposal_intelligence.schemas import (
    OpportunityUnderstanding,
    ProposalExecutionPlan,
)

logger = logging.getLogger(__name__)

AGENT = "rfp_understanding"

UNDERSTANDING_FORBIDDEN_KEYS = frozenset(
    {
        "content",
        "proposalText",
        "sectionProse",
        "executiveSummary",
        "marketingCopy",
        "draft",
    }
)

_SYSTEM = """You are the RFP Understanding Agent for zö agency proposal intelligence.
Read the entire RFP. Do NOT write proposal prose. Do NOT retrieve knowledge base content.
Do NOT invent methodology, budget tables, or section drafts.

Return JSON ONLY matching:
{
  "client": "string",
  "industry": "string",
  "orgType": "Municipality|County|State|Nonprofit|Corporate|Other",
  "projectType": "string",
  "services": ["string"],
  "businessGoals": ["string"],
  "painPoints": ["string"],
  "desiredOutcomes": ["string"],
  "complexity": "low|medium|high",
  "budgetIntel": {
    "ceiling": "string or null",
    "pricingModelHint": "string or null",
    "contractType": "string or null",
    "notes": "string"
  },
  "timelineIntel": {
    "projectStart": "string or null",
    "completion": "string or null",
    "goLive": "string or null",
    "milestones": ["string"],
    "notes": "string"
  },
  "confidence": 0.0,
  "memoryFacts": {
    "clientName": "string",
    "organizationType": "string",
    "cms": "optional",
    "hosting": "optional",
    "accessibilityStandard": "optional",
    "contractLength": "optional"
  }
}
Never include keys: content, proposalText, executiveSummary, marketingCopy, draft.
"""


async def run_rfp_understanding(
    *,
    plan: ProposalExecutionPlan,
    rfp_context: str,
    rfp_meta: dict[str, str],
) -> ProposalExecutionPlan:
    try:
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Title: {rfp_meta.get('title', '')}\n"
                        f"Client: {rfp_meta.get('client', '')}\n"
                        f"Sector: {rfp_meta.get('sector', '')}\n"
                        f"Location: {rfp_meta.get('location') or 'N/A'}\n\n"
                        f"Full RFP text:\n{rfp_context[:100000]}"
                    ),
                },
            ],
            max_tokens=4096,
            temperature=0.1,
        )
    except LlmError as exc:
        raise IntelligenceError(f"RFP Understanding failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise IntelligenceError(f"RFP Understanding failed: {exc}") from exc

    if not isinstance(raw, dict) or not raw:
        raise IntelligenceError("RFP Understanding returned empty JSON")

    for key in UNDERSTANDING_FORBIDDEN_KEYS:
        if key in raw:
            raw.pop(key, None)

    memory_facts = raw.pop("memoryFacts", None) or raw.pop("memory_facts", None) or {}
    try:
        understanding = OpportunityUnderstanding.model_validate(raw)
    except Exception as exc:
        logger.warning("OpportunityUnderstanding validation failed: %s", exc)
        understanding = OpportunityUnderstanding(
            client=str(rfp_meta.get("client") or ""),
            industry=str(rfp_meta.get("sector") or ""),
            projectType="unknown",
            confidence=0.3,
        )

    understanding.confidence = clamp_confidence(understanding.confidence)
    if not understanding.client:
        understanding.client = str(rfp_meta.get("client") or "")
    if not understanding.client or not understanding.project_type:
        raise IntelligenceError(
            "RFP Understanding missing required client or projectType"
        )

    plan.opportunity.understanding = understanding
    plan = set_provider(plan, provider)
    facts = {
        "clientName": understanding.client,
        "organizationType": understanding.org_type,
        "projectType": understanding.project_type,
        "industry": understanding.industry,
    }
    if isinstance(memory_facts, dict):
        for key, value in memory_facts.items():
            if value:
                facts[str(key)] = str(value)
    plan = merge_memory(plan, AGENT, facts)
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Normalized opportunity for {understanding.client}",
        reason=f"projectType={understanding.project_type}; complexity={understanding.complexity}",
        confidence=understanding.confidence,
    )
    return plan
