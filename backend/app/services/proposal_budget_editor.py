"""Budget editor — deterministic arithmetic pass before manuscript sync."""

from __future__ import annotations

import logging

from app.models.pricing_rate_card import PricingRateCard
from app.models.proposal import ProposalBudget, RfpSectionMap
from app.services.proposal_budget_floor import (
    collect_rfp_constraint_violations,
    collect_underbid_violations,
)
from app.services.proposal_budget_validation import (
    assert_budget_invariants,
    reconcile_proposal_budget,
    sum_line_items_extended,
)
from app.services.proposal_common import ProposalError

logger = logging.getLogger(__name__)


def run_budget_editor_pass(
    budget: ProposalBudget,
    *,
    rfp_sections: list[RfpSectionMap] | None = None,
    rfp_context: str = "",
    rate_card: PricingRateCard | None = None,
) -> ProposalBudget:
    """
    Finalize budget math: line-item sum is ground truth; propagate everywhere;
    never leave unresolved reconciliation flags. Fails loudly on invariant breach.

    ``rate_card`` (when the caller has one already built for this RFP) is used to
    check the proposed total against zo's own published pricing guide floor — a
    proposal materially below the guide's own minimum for the deliverables it
    covers halts here rather than shipping with only an advisory flag. Missing
    or empty rate card never halts (the guide failing to load is not a pricing
    defect).

    ``rfp_context`` is also checked against the RFP's own terms: a remote-only
    engagement clause with no on-site carve-out halts here if the budget still
    prices travel/reimbursables. Empty RFP text, or RFP text that does not
    clearly state remote-only work, never halts.
    """
    before_revenue = budget.agency_revenue_estimate
    before_lump = budget.lump_sum_total
    before_subtotal = sum_line_items_extended(budget)

    finalized = reconcile_proposal_budget(
        budget,
        rfp_sections=rfp_sections,
        rfp_context=rfp_context,
    )

    try:
        assert_budget_invariants(finalized)
    except ValueError:
        logger.warning(
            "Budget editor first pass failed invariants for %s — retrying reconcile",
            budget.rfp_id,
        )
        finalized = reconcile_proposal_budget(
            finalized,
            rfp_sections=rfp_sections,
            rfp_context=rfp_context,
        )
        try:
            assert_budget_invariants(finalized)
        except ValueError as exc:
            raise ProposalError(
                f"BUDGET EDITOR FAILED — pipeline halted: {exc}. "
                "Re-run Phase 3.5 budget generation or fix line items before proceeding.",
                status_code=422,
            ) from exc

    underbid_violations = collect_underbid_violations(finalized, rate_card)
    if underbid_violations:
        raise ProposalError(
            "BUDGET EDITOR FAILED — pipeline halted: "
            + "; ".join(underbid_violations)
            + " Re-run Phase 3.5 budget generation or raise line-item pricing to match "
            "the 00_Guide_Pricing floor before proceeding.",
            status_code=422,
        )

    rfp_constraint_violations = collect_rfp_constraint_violations(finalized, rfp_context)
    if rfp_constraint_violations:
        raise ProposalError(
            "BUDGET EDITOR FAILED — pipeline halted: "
            + "; ".join(rfp_constraint_violations)
            + " Re-run Phase 3.5 budget generation or remove the line items the RFP's own "
            "terms forbid before proceeding.",
            status_code=422,
        )

    after_subtotal = sum_line_items_extended(finalized)
    after_revenue = finalized.agency_revenue_estimate

    if before_subtotal != after_subtotal or before_revenue != after_revenue or before_lump != finalized.lump_sum_total:
        logger.info(
            "Budget editor for %s: line items %s→%s, revenue %s→%s, lump %s→%s",
            budget.rfp_id,
            before_subtotal,
            after_subtotal,
            before_revenue,
            after_revenue,
            before_lump,
            finalized.lump_sum_total,
        )

    return finalized
