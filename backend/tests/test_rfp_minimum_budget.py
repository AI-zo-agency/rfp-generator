"""RFP-stated minimum budget extraction and shortfall detection.

Incident this guards: a Kitsap County RFP stated a $500,000 minimum budgeted
amount; the pipeline proposed $278,400 and every gate passed, because the whole
money model was ceiling-only (rfp_budget_cap / NTE) with no floor concept.
"""

from __future__ import annotations

from app.models.proposal import ProposalBudget
from app.services.evidence_trust.rfp_money_constraints import (
    CONSTRAINT_HARD_FEE_NTE,
    CONSTRAINT_MINIMUM_BUDGET,
    apply_constraints_to_budget_fields,
    collect_under_minimum_flags,
    extract_rfp_money_constraints,
    primary_minimum_budget,
)


def _budget(**kw) -> ProposalBudget:
    kw.setdefault("rfpId", "r1")
    kw.setdefault("updatedAt", "2026-09-02T00:00:00Z")
    return ProposalBudget(**kw)


def _minimums(text: str) -> list[float]:
    return [
        c.amount
        for c in extract_rfp_money_constraints(text)
        if c.kind == CONSTRAINT_MINIMUM_BUDGET
    ]


class TestMinimumExtraction:
    def test_explicit_minimum_budgeted_amount(self):
        text = "The minimum budgeted amount for this contract is $500,000."
        assert _minimums(text) == [500_000.0]

    def test_budget_range_low_end_is_the_floor(self):
        text = "Proposals should reflect a project budget range of $500,000 to $750,000."
        assert 500_000.0 in _minimums(text)

    def test_between_phrasing(self):
        text = "The County anticipates a budget between $250,000 and $400,000 for this award."
        assert 250_000.0 in _minimums(text)

    def test_no_less_than_phrasing(self):
        text = "The total contract award amount shall be no less than $1.2 million."
        assert 1_200_000.0 in _minimums(text)

    def test_has_budgeted_a_minimum(self):
        text = "The County has budgeted a minimum of $500,000 for this project."
        assert 500_000.0 in _minimums(text)


class TestMinimumFalsePositives:
    """'minimum' is overwhelmingly an insurance/bonding/eligibility word in RFPs.

    A false floor is worse than a missed one: it drives the repair loop to
    inflate a correctly-priced bid.
    """

    def test_insurance_minimum_is_not_a_budget_floor(self):
        text = (
            "Contractor shall maintain commercial general liability insurance "
            "with a minimum limit of $1,000,000 per occurrence."
        )
        assert _minimums(text) == []

    def test_payment_bond_minimum_is_not_a_budget_floor(self):
        text = "Bidder must furnish a payment bond in the minimum amount of $100,000."
        assert _minimums(text) == []

    def test_annual_revenue_eligibility_is_not_a_budget_floor(self):
        text = "Offerors must demonstrate minimum annual revenue of $2,000,000."
        assert _minimums(text) == []

    def test_liquidated_damages_is_not_a_budget_floor(self):
        text = "Liquidated damages of no less than $5,000 per day shall apply."
        assert _minimums(text) == []

    def test_minimum_with_no_dollar_figure_yields_nothing(self):
        text = "Offerors shall have a minimum of five years of relevant experience."
        assert _minimums(text) == []


class TestMinimumVersusCeiling:
    def test_minimum_contract_value_is_not_also_read_as_an_nte(self):
        """'contract value' sits in the hard-NTE context pattern, so without a
        guard the same dollar became both the floor and the ceiling."""
        text = "The minimum contract value for this engagement is $500,000."
        kinds = {c.kind for c in extract_rfp_money_constraints(text)}
        assert CONSTRAINT_MINIMUM_BUDGET in kinds
        assert CONSTRAINT_HARD_FEE_NTE not in kinds

    def test_explicit_nte_still_wins_when_both_words_appear(self):
        text = (
            "Total compensation shall not exceed $2,950,000. "
            "Offerors must supply a minimum of three references."
        )
        constraints = extract_rfp_money_constraints(text)
        assert any(
            c.kind == CONSTRAINT_HARD_FEE_NTE and c.amount == 2_950_000.0
            for c in constraints
        )

    def test_floor_and_ceiling_can_coexist_from_a_range(self):
        text = (
            "The estimated project budget ranges from $500,000 to $750,000. "
            "Compensation shall not exceed $750,000."
        )
        constraints = extract_rfp_money_constraints(text)
        assert primary_minimum_budget(constraints) is not None
        assert primary_minimum_budget(constraints).amount == 500_000.0


class TestApplyToBudget:
    def test_floor_lands_on_the_budget_model(self):
        constraints = extract_rfp_money_constraints(
            "The minimum budgeted amount for this contract is $500,000."
        )
        budget = _budget()
        updated = apply_constraints_to_budget_fields(budget, constraints)
        assert updated.rfp_budget_floor == 500_000.0
        assert "minimum_budget=500,000.00" in updated.rfp_money_constraint_notes

    def test_no_floor_leaves_field_none(self):
        constraints = extract_rfp_money_constraints("Compensation shall not exceed $10,000.")
        updated = apply_constraints_to_budget_fields(_budget(), constraints)
        assert updated.rfp_budget_floor is None


class TestShortfallFlags:
    def test_kitsap_shortfall_is_flagged(self):
        budget = _budget(
            rfpBudgetFloor=500_000.0,
            agencyRevenueEstimate=278_400.0,
        )
        flags = collect_under_minimum_flags(budget)
        assert flags
        assert "278,400" in flags[0]
        assert "500,000" in flags[0]

    def test_total_at_the_floor_is_clean(self):
        budget = _budget(rfpBudgetFloor=500_000.0, agencyRevenueEstimate=500_000.0
        )
        assert collect_under_minimum_flags(budget) == []

    def test_total_above_the_floor_is_clean(self):
        budget = _budget(rfpBudgetFloor=500_000.0, agencyRevenueEstimate=640_000.0
        )
        assert collect_under_minimum_flags(budget) == []

    def test_no_floor_never_flags(self):
        budget = _budget(agencyRevenueEstimate=1.0)
        assert collect_under_minimum_flags(budget) == []

    def test_trivial_rounding_shortfall_is_tolerated(self):
        """A floor is a budget signal, not an arithmetic identity — do not run
        the repair loop over a sub-1% gap."""
        budget = _budget(rfpBudgetFloor=500_000.0, agencyRevenueEstimate=499_600.0
        )
        assert collect_under_minimum_flags(budget) == []

    def test_uses_total_client_invoicing_when_richer(self):
        budget = _budget(
            rfpBudgetFloor=500_000.0,
            agencyFeeSubtotal=200_000.0,
            clientMediaPassthrough=350_000.0,
            totalClientInvoicing=550_000.0,
        )
        assert collect_under_minimum_flags(budget) == []

