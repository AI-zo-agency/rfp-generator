"""Principle-based RFP Cost instrument judge — LLM meaning, not synonym regex.

Used after pricing so manuscript Cost matches THIS RFP's scored instrument even
when the pricing model defaults to phased out of habit.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.proposal_intelligence.agent_base import safe_chat_json
from app.services.proposal_rfp_excerpt import closing_package_excerpt

logger = logging.getLogger(__name__)

AGENT = "rfp_budget_format_judge"

BudgetFormatChoice = Literal[
    "phased",
    "personnel_loading",
    "blended_rate_form",
    "service_menu",
]

_SYSTEM = """You judge which Cost / Pricing INSTRUMENT THIS RFP requires vendors to submit.

Pick exactly one budgetFormat:
- personnel_loading — role-by-role / labor-category hourly rate table (often Year-2/Year-3 % increases)
- blended_rate_form — single hourly + monthly + annual (or official Pricing Proposal Form with those three)
- service_menu — menu of fixed service packages / à-la-carte line prices
- phased — phase/deliverable fee schedule or retainer narrative (default ONLY when no form instrument)

Rules:
- Match THIS RFP's scored Cost Proposal / Pricing form / Quotation form — not marketing habit.
- If the RFP asks for hourly rates BY ROLE or labor category → personnel_loading.
- If the RFP asks for one blended hourly/monthly/annual block → blended_rate_form.
- Mention of "budget" alone is not enough for personnel_loading.
- Do NOT invent requirements.

Return JSON only:
{
  "budgetFormat": "personnel_loading|blended_rate_form|service_menu|phased",
  "reason": "one short sentence grounded in RFP wording",
  "confidence": 0.0,
  "rfpQuote": "short verbatim quote or empty"
}
"""


class BudgetFormatJudgment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    budget_format: BudgetFormatChoice = Field(alias="budgetFormat")
    reason: str = ""
    confidence: float = 0.0
    rfp_quote: str = Field(default="", alias="rfpQuote")


def rfp_indicates_fixed_pricing_table(rfp_text: str) -> bool:
    """True when RFP requires a fixed Pricing Table / attachment — not hourly labor rows.

    Uses explicit buyer wording only (fixed + table/attachment), not platform synonym tables.
    """
    blob = (rfp_text or "").casefold()
    if not blob.strip():
        return False
    fixed_markers = (
        "pricing shall remain fixed",
        "price(s) quoted shall include all labor",
        "fixed pricing for all labor",
    )
    table_markers = (
        "pricing table",
        "cost proposal attachment",
        "separate cost proposal attachment",
        "vendor questionnaire section",
    )
    has_fixed = any(m in blob for m in fixed_markers)
    has_table = any(m in blob for m in table_markers)
    return has_fixed and has_table


def _normalize_format(raw: Any) -> BudgetFormatChoice:
    text = str(raw or "").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "personnel_loading": "personnel_loading",
        "personnelloading": "personnel_loading",
        "hourly": "personnel_loading",
        "hourly_rates": "personnel_loading",
        "labor_category": "personnel_loading",
        "blended_rate_form": "blended_rate_form",
        "blended": "blended_rate_form",
        "pricing_proposal_form": "blended_rate_form",
        "service_menu": "service_menu",
        "phased": "phased",
        "phase": "phased",
        "retainer": "phased",
    }
    return aliases.get(text, "phased")  # type: ignore[return-value]


async def judge_rfp_budget_format(rfp_text: str) -> BudgetFormatJudgment:
    """LLM judgment of THIS RFP's required Cost instrument."""
    body = (rfp_text or "").strip()
    if not body:
        return BudgetFormatJudgment(
            budgetFormat="phased",
            reason="No RFP text — default phased.",
            confidence=0.0,
        )
    if rfp_indicates_fixed_pricing_table(body):
        return BudgetFormatJudgment(
            budgetFormat="phased",
            reason=(
                "RFP requires fixed Pricing Table / Cost Proposal attachment — "
                "not a scored hourly labor-category instrument."
            ),
            confidence=0.95,
            rfpQuote="Pricing shall remain fixed",
        )
    excerpt = closing_package_excerpt(body, max_chars=18_000)
    # Prefer cost/pricing windows when present in the closing excerpt; else head+tail.
    user_blob = excerpt or body[:20_000]
    raw, _provider = await safe_chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    "Judge the required Cost / Pricing instrument for THIS RFP.\n\n"
                    f"RFP excerpt:\n{user_blob}"
                ),
            },
        ],
        max_tokens=512,
        temperature=0.05,
        agent_name=AGENT,
    )
    if not isinstance(raw, dict) or not raw:
        return BudgetFormatJudgment(
            budgetFormat="phased",
            reason="Judge unavailable — leave pricing agent format.",
            confidence=0.0,
        )
    conf = 0.0
    try:
        conf = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    return BudgetFormatJudgment(
        budgetFormat=_normalize_format(raw.get("budgetFormat")),
        reason=str(raw.get("reason") or "")[:400],
        confidence=max(0.0, min(1.0, conf)),
        rfpQuote=str(raw.get("rfpQuote") or raw.get("rfp_quote") or "")[:400],
    )


def align_budget_format_to_judgment(
    budget_format: str | None,
    judgment: BudgetFormatJudgment,
    *,
    min_confidence: float = 0.55,
) -> tuple[str, bool]:
    """Return (format, changed). Only override when judge is confident and differs."""
    current = (budget_format or "phased").casefold()
    judged = judgment.budget_format
    if judgment.confidence < min_confidence:
        return current, False
    if judged == current:
        return current, False
    # Never downgrade a confident form instrument to phased on weak disagreement —
    # judge wins when confident.
    return judged, True
