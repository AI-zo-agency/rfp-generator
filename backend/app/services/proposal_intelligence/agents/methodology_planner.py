"""Methodology Planner — delivery phases (what), not delivery model (how)."""

from __future__ import annotations

import json
import logging

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.retrieval import retrieve_intelligence
from app.services.proposal_intelligence.schemas import MethodologyPlan, ProposalExecutionPlan

logger = logging.getLogger(__name__)
AGENT = "methodology_planner"

_SYSTEM = """You are the Methodology Planner. Design delivery WORKSTREAMS / PHASES (what work happens).
Do not redefine deliveryModel (Agile/cadence) — that already exists.
Return JSON only:
{
  "phases": [
    {"name": "Workstream or phase name from THIS RFP", "activities": ["string"], "governance": "string"}
  ],
  "confidence": 0.0
}
Rules:
- Name phases for THIS RFP's actual work (sponsorship, social, festival ops, brand,
  paid media, website build, etc.) — never default to website UX/Dev phases unless
  the scope is clearly a site redesign/build.
- Prefer 4–6 substantive workstreams the Budget and Timeline can reuse verbatim.
- No proposal prose.
"""


async def run_methodology_planner(
    *,
    plan: ProposalExecutionPlan,
    rfp_meta: dict[str, str] | None = None,
) -> ProposalExecutionPlan:
    from app.services.proposal_delivery_package import methodology_retrieval_query

    u = plan.opportunity.understanding
    meta = rfp_meta or {}
    hits = await retrieve_intelligence(
        "methodology",
        query=methodology_retrieval_query(
            project_type=u.project_type,
            industry=u.industry,
            org_type=u.org_type,
            sector=str(meta.get("sector") or ""),
        ),
        limit=5,
    )
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Scope:\n{plan.opportunity.scope.model_dump_json()}\n"
                    f"Delivery model:\n{plan.delivery.delivery_model.model_dump_json()}\n"
                    f"Methodology intel:\n{json.dumps(hits, indent=2)[:12000]}"
                ),
            },
        ],
        max_tokens=2048,
        agent_name=AGENT,
    )
    try:
        method = MethodologyPlan.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", AGENT, exc)
        method = MethodologyPlan(confidence=0.2)
    method.confidence = clamp_confidence(method.confidence)
    plan.delivery.methodology = method
    plan = set_provider(plan, provider)
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Methodology phases: {len(method.phases)}",
        reason=", ".join(p.name for p in method.phases[:6]) or "default empty",
        confidence=method.confidence,
    )
    return plan
