"""RFP-stated minimum budget extraction and shortfall detection.

Incident this guards: a Kitsap County RFP stated a $500,000 minimum budgeted
amount; the pipeline proposed $278,400 and every gate passed, because the whole
money model was ceiling-only (rfp_budget_cap / NTE) with no floor concept.
"""

from __future__ import annotations

import contextlib
import unittest
from unittest.mock import patch

from app.models.proposal import ProposalBudget
from app.services.evidence_trust.rfp_money_constraints import (
    CONSTRAINT_HARD_FEE_NTE,
    CONSTRAINT_MINIMUM_BUDGET,
    apply_constraints_to_budget_fields,
    collect_under_minimum_flags,
    extract_rfp_money_constraints,
    primary_minimum_budget,
)
from app.services.proposal_pricing_service import repair_budget_to_rfp_minimum


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


class TestMinimumRepairLoop(unittest.IsolatedAsyncioTestCase):
    """The repair is bounded and can only improve or no-op.

    Per product decision, a stated minimum never halts the build: the agent
    retries a few times and then flags loudly.
    """

    @staticmethod
    def _priced(total: float) -> ProposalBudget:
        return _budget(
            rfpBudgetFloor=500_000.0,
            agencyRevenueEstimate=total,
            lineItems=[
                {
                    "id": "li-1",
                    "category": "labor",
                    "description": "Campaign strategy",
                    "quantity": 1,
                    "rate": total,
                    "extended": total,
                }
            ],
        )

    @contextlib.contextmanager
    def _stub_llm(self, totals: list[float]):
        """Stub the LLM to return a budget worth each total in `totals`, in order."""
        import app.services.proposal_pricing_service as svc

        calls = {"n": 0}

        async def fake_chat_json(messages, **kw):
            value = totals[min(calls["n"], len(totals) - 1)]
            calls["n"] += 1
            return (
                {
                    "lineItems": [
                        {
                            "id": "li-1",
                            "category": "labor",
                            "description": "Campaign strategy, expanded per RFP 3.2",
                            "quantity": 1,
                            "rate": value,
                            "extended": value,
                        }
                    ],
                    "scopeAdjustments": ["Deepened content cadence per RFP 3.2"],
                },
                "stub",
            )

        def fake_editor(budget, **kw):
            total = sum(float(li.extended or 0) for li in budget.line_items or [])
            return budget.model_copy(
                update={"agency_revenue_estimate": total, "line_item_sum": total}
            )

        with (
            patch.object(svc.llm, "is_configured", return_value=True),
            patch.object(svc.llm, "chat_json", fake_chat_json),
            patch.object(svc, "run_budget_editor_pass", fake_editor),
        ):
            yield calls

    async def test_reaches_the_floor_and_stops_early(self):
        with self._stub_llm([520_000.0]) as calls:
            repaired, _logs = await repair_budget_to_rfp_minimum(
                self._priced(278_400.0), rfp_id="r1"
            )
        assert repaired.agency_revenue_estimate == 520_000.0
        assert calls["n"] == 1, "should stop as soon as the floor is met"
        assert collect_under_minimum_flags(repaired) == []
        assert not [f for f in repaired.pricing_flags if "UNDERBID" in f]

    async def test_gives_up_after_three_attempts_and_flags(self):
        with self._stub_llm([300_000.0, 350_000.0, 400_000.0]) as calls:
            repaired, logs = await repair_budget_to_rfp_minimum(
                self._priced(278_400.0), rfp_id="r1"
            )
        assert calls["n"] == 3
        # Best-effort progress is kept, not thrown away.
        assert repaired.agency_revenue_estimate == 400_000.0
        assert any("UNDERBID" in f for f in repaired.pricing_flags)
        assert any("exhausted" in line for line in logs)

    async def test_attempt_that_breaches_the_cap_is_discarded(self):
        budget = self._priced(278_400.0).model_copy(update={"rfp_budget_cap": 600_000.0})
        with self._stub_llm([900_000.0]):
            repaired, logs = await repair_budget_to_rfp_minimum(budget, rfp_id="r1")
        assert repaired.agency_revenue_estimate == 278_400.0, "cap breach must not land"
        assert any("breaches RFP cap" in line for line in logs)

    async def test_floor_above_cap_flags_instead_of_repairing(self):
        budget = self._priced(278_400.0).model_copy(update={"rfp_budget_cap": 300_000.0})
        with self._stub_llm([900_000.0]) as calls:
            repaired, _ = await repair_budget_to_rfp_minimum(budget, rfp_id="r1")
        assert calls["n"] == 0, "contradictory constraints must not drive a repair"
        assert any("constraints conflict" in f for f in repaired.pricing_flags)

    async def test_no_floor_is_a_no_op(self):
        budget = _budget(agencyRevenueEstimate=278_400.0)
        with self._stub_llm([900_000.0]) as calls:
            repaired, logs = await repair_budget_to_rfp_minimum(budget, rfp_id="r1")
        assert calls["n"] == 0
        assert logs == []
        assert repaired is budget

    async def test_already_at_the_floor_is_a_no_op(self):
        with self._stub_llm([900_000.0]) as calls:
            _repaired, logs = await repair_budget_to_rfp_minimum(
                self._priced(500_000.0), rfp_id="r1"
            )
        assert calls["n"] == 0
        assert logs == []
