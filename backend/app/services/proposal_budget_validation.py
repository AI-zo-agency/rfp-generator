"""Validate and reconcile Stage 3 budget math — no RFP-specific hardcoding."""

from __future__ import annotations

import re
from typing import Any

from app.models.proposal import ProposalBudget, RfpSectionMap

_LUMP_SUM_RE = re.compile(
    r"\b(lump\s*sum|total\s*(?:contract|project)\s*(?:price|cost|amount)|not[\s-]*to[\s-]*exceed|nte)\b",
    re.I,
)
_HOURLY_RE = re.compile(r"\b(hourly\s*rate|rate\s*per\s*hour|loaded\s*rate)\b", re.I)


def sum_line_items_extended(budget: ProposalBudget) -> float:
    total = 0.0
    for item in budget.line_items:
        if isinstance(item.extended, (int, float)):
            total += float(item.extended)
    return total


def rfp_requires_lump_sum_and_hourly(
    rfp_sections: list[RfpSectionMap] | None,
    rfp_context: str = "",
) -> bool:
    """True when RFP text asks for both lump sum and hourly pricing."""
    blobs: list[str] = [rfp_context[:40_000]]
    for section in rfp_sections or []:
        blobs.append(section.title or "")
        blobs.extend(section.requirements or [])
    text = "\n".join(blobs)
    return bool(_LUMP_SUM_RE.search(text) and _HOURLY_RE.search(text))


def reconcile_proposal_budget(
    budget: ProposalBudget,
    *,
    rfp_sections: list[RfpSectionMap] | None = None,
    rfp_context: str = "",
) -> ProposalBudget:
    """Align agency revenue with line items; flag missing lump sum when RFP requires it."""
    flags = list(budget.pricing_flags)
    subtotal = sum_line_items_extended(budget)
    direct = float(budget.direct_expenses_total or 0)
    computed = subtotal + direct

    estimate = budget.agency_revenue_estimate
    if computed > 0:
        if estimate is None or abs(float(estimate) - computed) > max(1.0, computed * 0.01):
            if estimate is not None:
                flags.append(
                    "[PRICING FLAG: Agency revenue estimate reconciled to match line items "
                    f"(${estimate:,.0f} → ${computed:,.0f}) — verify before submission]"
                )
            budget = budget.model_copy(update={"agency_revenue_estimate": computed})

    if rfp_requires_lump_sum_and_hourly(rfp_sections, rfp_context):
        if budget.lump_sum_total is None and computed > 0:
            budget = budget.model_copy(update={"lump_sum_total": computed})
            flags.append(
                "[PRICING FLAG: RFP requires lump sum + hourly — lump sum set to line-item total; "
                "confirm NTE wording with Sonja]"
            )
        elif budget.lump_sum_total is None:
            flags.append(
                "[PRICING FLAG: RFP requires both lump sum and hourly rates — add lumpSumTotal]"
            )

    tier = (budget.pricing_tier or "").strip()
    if tier and budget.qualifying_language:
        ql = budget.qualifying_language
        other_tiers = {"Low", "Average", "High"} - {tier}
        for other in other_tiers:
            if re.search(rf"\b{other}\s+tier\b", ql, re.I):
                flags.append(
                    f"[PRICING FLAG: qualifyingLanguage mentions {other} tier but pricingTier is "
                    f"{tier} — reconcile to one tier only]"
                )

    if flags != budget.pricing_flags:
        budget = budget.model_copy(update={"pricing_flags": flags})
    return budget


def parse_budget_extras(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract optional lump-sum / direct-expense fields from LLM JSON."""
    extras: dict[str, Any] = {}
    for key, alias in (
        ("lumpSumTotal", "lump_sum_total"),
        ("directExpensesTotal", "direct_expenses_total"),
    ):
        val = raw.get(key) if key in raw else raw.get(alias)
        if isinstance(val, (int, float)) and float(val) >= 0:
            extras[alias] = float(val)
    return extras
