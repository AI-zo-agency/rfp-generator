"""Internal fee justification memo from Stage 3 budget."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.models.proposal import FeeJustificationMemo, ProposalBudget
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.llm import LlmError
from app.services.proposal_langchain import _provider_name

logger = logging.getLogger(__name__)

FEE_MEMO_PROMPT = """Write an INTERNAL fee justification memo for zö agency leadership (not for RFP submission).

Include:
1. Pricing posture — best value positioning; if budget cap known, target ~5–8% under cap (never look underscoped)
2. Role-hour defense — table of named roles, hours, rates, extended totals tied to deliverables
3. Risk flags — what could cause scope creep or evaluator pushback
4. Tier rationale — why Low/Average/High was selected

Return ONLY JSON:
{
  "markdown": "full memo in Markdown",
  "pricingPosture": "one sentence",
  "targetVsCap": "e.g. 7% under $500k cap or 'cap unknown'",
  "roleHoursSummary": ["Brand Strategist: 40h @ $X = $Y — message architecture"],
  "internalNotes": ["bullet for Sonja/Curt/Ella"]
}"""


async def generate_fee_justification_memo(
    *,
    rfp: RfpRecord,
    budget: ProposalBudget,
    stage_one_excerpt: str = "",
) -> FeeJustificationMemo | None:
    if not llm.is_configured():
        return None

    line_items = [
        {
            "description": item.description,
            "role": item.role_title,
            "person": item.named_person,
            "quantity": item.quantity,
            "rate": item.rate,
            "extended": item.extended,
            "unit": item.unit,
        }
        for item in budget.line_items[:30]
    ]

    try:
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": FEE_MEMO_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Client: {rfp.client}\n"
                        f"RFP: {rfp.title}\n"
                        f"Budget cap: {budget.rfp_budget_cap or 'unknown'}\n"
                        f"Pricing tier: {budget.pricing_tier}\n"
                        f"Fee structure: {budget.fee_structure}\n"
                        f"Agency revenue estimate: {budget.agency_revenue_estimate}\n"
                        f"Format: {budget.budget_format}\n\n"
                        f"Line items:\n{line_items}\n\n"
                        f"Verified rates:\n"
                        f"{[r.model_dump(by_alias=True) for r in budget.verified_rates]}\n\n"
                        f"Scope summary:\n{budget.scope_summary[:3000]}\n\n"
                        f"Stage 1 excerpt:\n{stage_one_excerpt[:4000]}"
                    ),
                },
            ],
            max_tokens=4096,
            temperature=0.2,
        )
    except LlmError as exc:
        logger.warning("Fee justification memo failed: %s", exc)
        return _fallback_memo(rfp=rfp, budget=budget)

    now = datetime.now(timezone.utc).isoformat()
    return FeeJustificationMemo(
        markdown=str(raw.get("markdown") or "").strip() or _fallback_memo(rfp, budget).markdown,
        pricingPosture=str(raw.get("pricingPosture") or raw.get("pricing_posture") or ""),
        targetVsCap=str(raw.get("targetVsCap") or raw.get("target_vs_cap") or ""),
        roleHoursSummary=[
            str(x)
            for x in (raw.get("roleHoursSummary") or raw.get("role_hours_summary") or [])
            if str(x).strip()
        ],
        internalNotes=[
            str(x) for x in (raw.get("internalNotes") or raw.get("internal_notes") or []) if str(x).strip()
        ],
        generatedAt=now,
        provider=provider,
    )


def _fallback_memo(*, rfp: RfpRecord, budget: ProposalBudget) -> FeeJustificationMemo:
    cap = budget.rfp_budget_cap
    total = budget.agency_revenue_estimate
    posture = "Best value — defend scope depth, not lowest price."
    if cap and total:
        pct = round((1 - total / cap) * 100, 1) if cap > 0 else 0
        target = f"{pct}% under stated cap (${cap:,.0f})" if pct > 0 else f"At cap ${cap:,.0f}"
    else:
        target = "Budget cap unknown — confirm before submission."

    role_lines = []
    for item in budget.line_items:
        if item.unit == "hours" and item.quantity and item.rate:
            role_lines.append(
                f"{item.role_title or item.description}: {item.quantity}h @ ${item.rate:,.0f}"
            )

    md = f"""# Internal Fee Justification — {rfp.client}

## Pricing posture
{posture}

## Target vs cap
{target}

## Tier
{budget.pricing_tier or 'Average'} — {budget.fee_structure or 'per Pricing Guide'}

## Role hours
{chr(10).join(f'- {line}' for line in role_lines) or '- (Generate line items with hours for role defense)'}

## Flags
{chr(10).join(f'- {f}' for f in budget.pricing_flags) or '- None'}
"""
    return FeeJustificationMemo(
        markdown=md,
        pricingPosture=posture,
        targetVsCap=target,
        roleHoursSummary=role_lines,
        internalNotes=["Review against 05_PricingGuide before submission."],
        generatedAt=datetime.now(timezone.utc).isoformat(),
        provider=_provider_name(),
    )
