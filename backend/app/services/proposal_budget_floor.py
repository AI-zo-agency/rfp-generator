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
guide band and are excluded entirely — see
``proposal_budget_validation.infer_line_item_type``.

Line items that cannot be confidently matched to a guide service add nothing to
the floor (never a false floor for unrecognised/bespoke work) but their dollars
DO still count toward the proposed total. Both halves matter: dropping them from
the floor prevents inventing a requirement, and keeping them in the total
prevents a large bespoke line from reading as an underbid of the small matched
remainder.

This check raises 422 and aborts the whole proposal, so a false positive is as
damaging as a miss. Every ambiguity here is therefore resolved toward NOT
firing: unmatched work, an unloadable guide, and a below-threshold fuzzy match
all yield no violation.
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
    """Match a line item description to a guide service by symmetric token overlap.

    None when no service clears the match threshold — an unrecognised /
    bespoke deliverable must never manufacture a floor.

    Scoring is Jaccard (``intersection / union``), NOT ``intersection / len(label)``.
    Normalizing by label length alone meant any short guide label was trivially
    cleared by any line item that happened to contain its words: the 2-token
    label "Website Design" scored 1.0 against "Design review of existing website
    content", inventing a $20,000 floor for a $500 line. Because a violation here
    raises 422 and aborts the whole proposal, a false match is as damaging as a
    missed one — unlike pricing_rate_binding._find_rate, whose false match only
    sets is_manual_fill and emits an advisory flag. Jaccard penalizes both a label
    that is too short relative to the line item and a line item that describes far
    more than the label does; a minimum-overlap-count rule would not have caught
    the "Website Design" case at all (its overlap is already 2). Failing to match
    is the safe direction: it contributes nothing and can only miss an underbid,
    never manufacture a halt.
    """
    want = _tokens(description)
    if not want:
        return None
    best_label, best_score = None, 0.0
    for label in groups:
        have = _tokens(label)
        if not have:
            continue
        score = len(want & have) / len(want | have)
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

    Only agency_fee line items are considered — travel/reimbursables and client
    media pass-through have no guide band (proposal_budget_validation.
    infer_line_item_type). Of those, every priced dollar counts toward the
    proposed total, while the floor sums each distinct matched guide service
    exactly once. If the rate card is empty or unavailable, this never halts the
    pipeline: an unloadable guide is not a pricing defect.
    """
    rates = bindable_rates(rate_card)
    if not rates:
        return []
    groups = _group_rates_by_service(rates)
    if not groups:
        return []

    # Floor is keyed by distinct guide SERVICE, not by line item. budgetFormat
    # defaults to "phased" (proposal_pricing_service.py), so splitting a single
    # guide deliverable across "… — phase 1" / "… — phase 2" rows is the normal
    # shape of a generated budget. Accumulating per line item counted the same
    # deliverable's floor twice and halted correctly-priced budgets with a 422.
    floors_by_service: dict[str, float] = {}
    # Numerator is ALL priced agency fees, not just the matched ones. Dropping an
    # unmatched item's dollars while keeping other items' floors made a $31,000
    # proposal (nearly 3x the matched floor) read as a $1,000 underbid.
    priced = 0.0
    for item in budget.line_items:
        if infer_line_item_type(item) != "agency_fee":
            continue  # travel and media are billed at cost — no guide band
        priced += float(item.extended or 0)
        label = _best_service_label(item.description or "", groups)
        if label is None:
            continue  # unmatched — adds no floor, but its dollars still count above
        floors_by_service.setdefault(label, _service_floor(groups[label]))

    floor = round(sum(floors_by_service.values()), 2)
    priced = round(priced, 2)
    if floor <= 0:
        return []
    if priced >= floor * tolerance:
        return []

    return [
        f"Proposed agency fees ${priced:,.2f} are below {int(tolerance * 100)}% of the "
        f"00_Guide_Pricing floor ${floor:,.2f} for the matched deliverables "
        f"({', '.join(sorted(floors_by_service))}). Confirm this is a deliberate "
        "discount before submitting, or raise the priced fees to match the guide."
    ]


# --- RFP constraint check -------------------------------------------------
#
# Observed defect: an MSU Denver RFP stated at Section 2.7 "All work under
# this agreement shall be performed remotely... No on-site presence is
# anticipated unless requested by MSU Denver." The generated budget included
# a $2,500 travel line anyway. Nothing compared line items against the RFP's
# own terms.
#
# Only direct_expense line items (travel, reimbursables — see
# proposal_budget_validation.infer_line_item_type) are candidates: agency fee
# and client pass-through lines are never billed-at-cost travel and are never
# flagged by this check.
#
# This raises the same 422 as collect_underbid_violations, so a false
# positive halts a whole proposal run just as a miss would let a bad line
# ship. _REMOTE_ONLY_RE is deliberately narrow (specific "performed remotely"
# / "no on-site presence" phrasings) so an incidental "remote" mention
# ("remote desktop support", "remote sensing data", "the remote possibility
# that...") never trips it. _ONSITE_REQUIRED_RE is deliberately broad — any
# on-site obligation nearby a requirement verb (required/mandatory/expected/
# shall/must/necessary), in either word order, within the same sentence —
# so a hybrid engagement ("primarily remote, with occasional on-site
# meetings") or a remote clause carved out for on-site kickoff/quarterly
# visits never fires. Sentence-bounded via ``[^.]`` so a requirement verb in
# one sentence can't combine with "on-site" in an unrelated later sentence.
_REMOTE_ONLY_RE = re.compile(
    r"(?i)\b(?:all\s+work[^.]{0,60}performed\s+remotely|"
    r"work\s+shall\s+be\s+performed\s+remotely|"
    r"no\s+on-?site\s+presence\s+is\s+anticipated|"
    r"fully\s+remote\s+engagement)\b"
)
_ONSITE_REQUIRED_RE = re.compile(
    r"(?i)\bon[\s-]?site\b[^.]{0,60}\b(?:required|mandatory|expected|shall|must|necessary)\b|"
    r"\b(?:required|mandatory|expected|shall|must)\b[^.]{0,60}\bon[\s-]?site\b"
)


def collect_rfp_constraint_violations(budget: ProposalBudget, rfp_text: str) -> list[str]:
    """Reject direct-expense line items (travel, reimbursables) the RFP's own terms forbid.

    Empty/missing RFP text, or RFP text that does not clearly state a
    remote-only engagement, never halts. An RFP that also calls out an
    on-site obligation (kickoff, quarterly visits, hybrid work) never halts
    either — only an unqualified remote-only clause with no on-site carve-out
    does. Agency fee and client pass-through lines are never candidates; only
    ``direct_expense`` lines (travel/reimbursables) can be flagged.
    """
    text = rfp_text or ""
    if not text.strip():
        return []
    if not _REMOTE_ONLY_RE.search(text) or _ONSITE_REQUIRED_RE.search(text):
        return []

    offenders = [
        (item.description or "line item", float(item.extended or 0))
        for item in budget.line_items
        if infer_line_item_type(item) == "direct_expense"
    ]
    if not offenders:
        return []

    listed = ", ".join(f"{d} (${x:,.2f})" for d, x in offenders)
    return [
        "The RFP states all work shall be performed remotely, but the budget prices "
        f"travel/reimbursables: {listed}. Remove these lines or cite the clause that "
        "authorises on-site work."
    ]
