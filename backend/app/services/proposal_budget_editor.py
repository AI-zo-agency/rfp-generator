"""Budget editor — deterministic arithmetic pass before manuscript sync."""

from __future__ import annotations

import logging

from app.models.proposal import ProposalBudget, RfpSectionMap
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
) -> ProposalBudget:
    """
    Finalize budget math: line-item sum is ground truth; propagate everywhere;
    never leave unresolved reconciliation flags. Fails loudly on invariant breach.
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
                f"Budget editor failed: {exc}. "
                "Re-run Phase 3.5 budget generation or reconcile manually.",
                status_code=422,
            ) from exc

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
