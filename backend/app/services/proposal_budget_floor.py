"""Total-level underbid detection against zo's published pricing guide.

The only existing guide comparison is per-line and advisory
(``app.services.pricing_rate_binding._amount_in_band``): an out-of-band amount
sets ``is_manual_fill`` and emits a ``[PRICING FLAG]``, which then ships to the
client instead of stopping the proposal. That is how a $3,500 total shipped
against a ~$27,750 00_Guide_Pricing floor for the required deliverables — a
~10x underbid with only an advisory note.

This module is the aggregate, blocking version: it sums the guide floor
(lowest documented tier) for every priced agency-fee line item that can be
confidently matched to a guide service, and refuses to let the proposed total
fall materially below that floor.

Direct expenses (travel, reimbursables) and client pass-through media have no
guide band and are excluded — see ``proposal_budget_validation.infer_line_item_type``.
Line items that cannot be confidently matched to a guide service contribute
nothing to the floor (never a false floor for unrecognised/bespoke work).
"""

from __future__ import annotations

import logging
import re

from app.models.pricing_rate_card import PricingRate, PricingRateCard
from app.models.proposal import ProposalBudget
from app.services.proposal_budget_validation import infer_line_item_type
from app.services.pricing_rate_card_builder import bindable_rates

logger = logging.getLogger(__name__)

UNDERBID_TOLERANCE = 0.6
_WORD_RE = re.compile(r"[a-z0-9]+")
_MATCH_SCORE_MIN = 0.5


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if len(w) > 3}


def _group_rates_by_service(rates: list[PricingRate]) -> dict[str, list[PricingRate]]:
    """Group guide rates by service label — a service can have Low/Average/High rows."""
    groups: dict[str, list[PricingRate]] = {}
    for rate in rates:
        label = (rate.service or "").strip()
        if not label:
            continue
        groups.setdefault(label, []).append(rate)
    return groups


def _best_service_label(description: str, groups: dict[str, list[PricingRate]]) -> str | None:
    """Match a line item description to a guide service by token overlap.

    None when no service clears the match threshold — an unrecognised /
    bespoke deliverable must never manufacture a floor.
    """
    want = _tokens(description)
    if not want:
        return None
    best_label, best_score = None, 0.0
    for label in groups:
        have = _tokens(label)
        if not have:
            continue
        score = len(want & have) / len(have)
        if score > best_score:
            best_label, best_score = label, score
    return best_label if best_score >= _MATCH_SCORE_MIN else None


def _service_floor(rates: list[PricingRate]) -> float:
    """Lowest documented tier for a service — the true guide floor regardless of tier chosen."""
    lows = [r.amount_low for r in rates if r.amount_low is not None]
    if lows:
        return min(lows)
    amounts = [r.amount for r in rates if r.amount is not None]
    return min(amounts) if amounts else 0.0


def collect_underbid_violations(
    budget: ProposalBudget,
    rate_card: PricingRateCard | None,
    *,
    tolerance: float = UNDERBID_TOLERANCE,
) -> list[str]:
    """Empty when priced agency fees are at or near the 00_Guide_Pricing floor.

    Only agency_fee line items are compared — travel/reimbursables and client
    media pass-through have no guide band (proposal_budget_validation.
    infer_line_item_type). If the rate card is empty or unavailable, this
    never halts the pipeline: an unloadable guide is not a pricing defect.
    """
    rates = bindable_rates(rate_card)
    if not rates:
        return []
    groups = _group_rates_by_service(rates)
    if not groups:
        return []

    floor = 0.0
    priced = 0.0
    matched_labels: list[str] = []
    for item in budget.line_items:
        if infer_line_item_type(item) != "agency_fee":
            continue  # travel and media have no guide band
        label = _best_service_label(item.description or "", groups)
        if label is None:
            continue  # unmatched — contributes nothing, never manufactures a floor
        floor += _service_floor(groups[label])
        priced += float(item.extended or 0)
        matched_labels.append(label)

    if floor <= 0:
        return []
    if priced >= floor * tolerance:
        return []

    return [
        f"Proposed fees ${priced:,.0f} are below {int(tolerance * 100)}% of the "
        f"00_Guide_Pricing floor ${floor:,.0f} for the matched deliverables "
        f"({', '.join(matched_labels)}). Confirm this is a deliberate discount "
        "before submitting, or raise the priced fees to match the guide."
    ]
