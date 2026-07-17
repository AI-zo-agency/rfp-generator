"""Delivery Pattern Intelligence — patterns from won proposals only."""

from __future__ import annotations

import json
import logging

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, merge_memory, set_provider
from app.services.proposal_intelligence.retrieval import retrieve_intelligence
from app.services.proposal_intelligence.schemas import (
    DeliveryModel,
    DeliveryPattern,
    ProposalExecutionPlan,
)

logger = logging.getLogger(__name__)
AGENT = "delivery_pattern"

_SYSTEM = """You extract DELIVERY PATTERNS from won-proposal intelligence excerpts.
Never copy proposal marketing content. Return JSON only:
{
  "deliveryPattern": {
    "patternsObserved": ["string"],
    "sourceWonProposals": ["filename or id"],
    "staffingShape": "string",
    "phaseShape": "string",
    "confidence": 0.0
  },
  "deliveryModel": {
    "type": "Agile|Waterfall|Hybrid",
    "governance": "string",
    "cadence": "string",
    "clientEngagement": "string",
    "reviewModel": "string",
    "decisionMaking": "string",
    "confidence": 0.0
  }
}
deliveryModel = HOW work happens. Do not list Discovery/UX phases here.
"""


async def run_delivery_pattern(
    *,
    plan: ProposalExecutionPlan,
    rfp_meta: dict[str, str],
) -> ProposalExecutionPlan:
    u = plan.opportunity.understanding
    query = (
        f"{u.industry} {u.org_type} {u.project_type} {rfp_meta.get('sector', '')} "
        f"won proposal delivery pattern"
    )
    hits = await retrieve_intelligence("won_patterns", query=query, limit=6)
    excerpts = [
        {"source": h.get("source"), "excerpt": h.get("excerpt", "")[:1200]} for h in hits
    ]
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Opportunity:\n{u.model_dump_json()}\n\n"
                    f"Won-proposal pattern excerpts (patterns only):\n"
                    f"{json.dumps(excerpts, indent=2)}"
                ),
            },
        ],
        max_tokens=2048,
        agent_name=AGENT,
    )
    pattern_raw = (raw or {}).get("deliveryPattern") or raw or {}
    model_raw = (raw or {}).get("deliveryModel") or {}
    try:
        pattern = DeliveryPattern.model_validate(pattern_raw)
    except Exception:
        pattern = DeliveryPattern(confidence=0.2)
    try:
        model = DeliveryModel.model_validate(model_raw)
    except Exception:
        model = DeliveryModel(confidence=0.2)
    if hits:
        pattern.source_won_proposals = list(
            dict.fromkeys(
                list(pattern.source_won_proposals)
                + [str(h.get("source") or "") for h in hits if h.get("source")]
            )
        )
    pattern.confidence = clamp_confidence(pattern.confidence)
    model.confidence = clamp_confidence(model.confidence)
    plan.delivery.delivery_pattern = pattern
    plan.delivery.delivery_model = model
    plan.metadata.won_patterns_used = list(pattern.source_won_proposals)[:12]
    plan = set_provider(plan, provider)
    if model.type:
        plan = merge_memory(plan, AGENT, {"deliveryApproach": model.type})
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Delivery model: {model.type or 'unspecified'}",
        reason=f"Patterns from {len(hits)} won-proposal hits",
        confidence=min(pattern.confidence, model.confidence) if model.type else pattern.confidence,
    )
    return plan
