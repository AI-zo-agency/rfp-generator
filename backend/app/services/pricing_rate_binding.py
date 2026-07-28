"""Bind budget line items to PricingRateCard rates (T5.2).

Fail closed: no confident match → is_manual_fill + pricing flag (never invent).
"""

from __future__ import annotations

import logging
import re

from app.models.pricing_rate_card import PricingRate, PricingRateCard
from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.pricing_rate_card_builder import bindable_rates

logger = logging.getLogger(__name__)

_MENU_ID_RE = re.compile(r"\b(\d+\.\d+)\b")
_AMOUNT_TOLERANCE = 0.15  # 15% band vs card midpoint / range


def _extract_menu_id(*parts: str) -> str:
    for part in parts:
        match = _MENU_ID_RE.search(part or "")
        if match:
            return match.group(1)
    return ""


def _amount_in_band(amount: float, rate: PricingRate) -> bool:
    low = rate.amount_low if rate.amount_low is not None else rate.amount
    high = rate.amount_high if rate.amount_high is not None else rate.amount
    mid = rate.amount
    if mid is None and low is None:
        return False
    if low is not None and high is not None and low <= amount <= high * 1.001:
        return True
    if mid is not None and mid > 0:
        return abs(amount - mid) / mid <= _AMOUNT_TOLERANCE
    return False


def _find_rate(
    card: PricingRateCard,
    *,
    menu_id: str,
    tier: str,
    description: str,
) -> PricingRate | None:
    candidates = bindable_rates(card)
    if not candidates:
        return None
    tier_norm = (tier or "Average").strip().lower()
    if menu_id:
        by_menu = [r for r in candidates if r.menu_id == menu_id]
        if by_menu:
            tier_hits = [r for r in by_menu if r.tier.lower() == tier_norm or tier_norm in ("avg", "")]
            return (tier_hits or by_menu)[0]
    desc = (description or "").casefold()
    for rate in candidates:
        service = (rate.service or "").casefold()
        if service and (service in desc or desc in service):
            return rate
    return None


def bind_budget_line_items_to_rate_card(
    budget: ProposalBudget,
    card: PricingRateCard | None,
) -> ProposalBudget:
    """Stamp source_rate_id or is_manual_fill on every priced line."""
    flags = list(budget.pricing_flags or [])
    grounding_by_id = {
        g.line_item_id: g for g in (budget.line_item_grounding or []) if g.line_item_id
    }
    updated: list[BudgetLineItem] = []
    bound = 0
    manual = 0

    for item in budget.line_items:
        amount = item.rate if item.rate is not None else item.extended
        # Pass-through media and zero lines: still require explicit provenance if extended > 0
        needs_bind = (
            (item.extended is not None and float(item.extended) > 0)
            or (item.rate is not None and float(item.rate) > 0)
        )
        if not needs_bind:
            updated.append(item)
            continue

        # Already manually marked
        if item.is_manual_fill and not item.source_rate_id:
            manual += 1
            updated.append(item)
            continue

        g = grounding_by_id.get(item.id)
        menu_id = _extract_menu_id(
            item.rate_source or "",
            item.description or "",
            (g.guide_sku if g else "") or "",
            (g.note if g else "") or "",
        )
        tier = budget.pricing_tier or (g.tier_chosen if g else "") or "Average"
        matched = _find_rate(
            card or PricingRateCard(),
            menu_id=menu_id,
            tier=str(tier),
            description=item.description or "",
        )

        if matched and amount is not None and _amount_in_band(float(amount), matched):
            bound += 1
            updated.append(
                item.model_copy(
                    update={
                        "source_rate_id": matched.rate_id,
                        "is_manual_fill": False,
                        "rate_source": item.rate_source
                        or f"{matched.menu_id} — {matched.source_doc} {matched.tier}".strip(" —"),
                    }
                )
            )
            continue

        # No confident bind — flag, do not invent.
        manual += 1
        reason = "no matching guide rate"
        if matched and amount is not None:
            reason = (
                f"amount {amount} outside guide band for {matched.rate_id} "
                f"({matched.amount_low}-{matched.amount_high})"
            )
        elif not card or not card.rates:
            reason = "empty pricing rate card (KB miss or unparsed guide)"
        flag = (
            f"[PRICING FLAG: line {item.id} unbound — {reason}; "
            f"is_manual_fill=True — do not invent rate]"
        )
        if flag not in flags:
            flags.append(flag)
        updated.append(
            item.model_copy(
                update={
                    "source_rate_id": None,
                    "is_manual_fill": True,
                }
            )
        )

    logger.info(
        "budget_rate_binding rfp_id=%s bound=%s manual_fill=%s flags_added=%s",
        budget.rfp_id,
        bound,
        manual,
        max(0, len(flags) - len(budget.pricing_flags or [])),
    )
    return budget.model_copy(update={"line_items": updated, "pricing_flags": flags})


def collect_unbound_line_item_violations(budget: ProposalBudget) -> list[str]:
    """Every priced line must have source_rate_id XOR is_manual_fill.

    If no line has provenance yet, the binding pass has not run — skip (legacy /
    pre-bind snapshots). After ``bind_budget_line_items_to_rate_card``, every
    priced line is stamped and gaps become errors.
    """
    any_provenance = any(
        bool((item.source_rate_id or "").strip()) or item.is_manual_fill
        for item in budget.line_items
    )
    if not any_provenance:
        return []

    errors: list[str] = []
    for item in budget.line_items:
        priced = (
            (item.extended is not None and float(item.extended) > 0)
            or (item.rate is not None and float(item.rate) > 0)
        )
        if not priced:
            continue
        has_source = bool((item.source_rate_id or "").strip())
        if has_source and item.is_manual_fill:
            errors.append(
                f"{item.id}: has both source_rate_id and is_manual_fill — must be XOR"
            )
        elif not has_source and not item.is_manual_fill:
            errors.append(
                f"{item.id}: priced line missing source_rate_id and is_manual_fill "
                f"({(item.description or '')[:60]})"
            )
    return errors
