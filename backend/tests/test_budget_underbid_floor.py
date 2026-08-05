"""A proposal must not fall materially below zo's own published guide floor.

Observed: $3,500 total against a guide floor near $27,750 for the deliverables
the RFP required — roughly a 10x underbid, shipped with only an advisory flag
(is_manual_fill + [PRICING FLAG] from pricing_rate_binding._amount_in_band,
which is non-fatal by construction).

The stand-in classes originally sketched for this test (a bare ``_Band``/``_Card``
with ``label``/``low``/``high``) do not match the real KB-extracted model: the
real ``PricingRateCard`` holds a flat ``rates: list[PricingRate]`` (not
``bands``), and each ``PricingRate`` carries ``service`` (not ``label``) plus
``amount_low``/``amount_high`` (not ``low``/``high``) — see
app/models/pricing_rate_card.py and app/services/pricing_rate_card_builder.py.
A single guide service can also have multiple rows, one per Low/Average/High
tier. This test uses the real classes throughout.
"""

from __future__ import annotations

import unittest

from app.models.pricing_rate_card import PricingRate, PricingRateCard
from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_editor import run_budget_editor_pass
from app.services.proposal_budget_floor import collect_underbid_violations
from app.services.proposal_common import ProposalError


def _rate(rate_id: str, service: str, low: float, high: float, tier: str = "Average") -> PricingRate:
    return PricingRate(
        rate_id=rate_id,
        service=service,
        tier=tier,
        unit="fixed",
        amount=round((low + high) / 2.0, 2),
        amount_low=low,
        amount_high=high,
        menu_id="",
        source_doc="00_Guide_Pricing",
        confidence=0.95,
        notes="",
    )


# Verified live KB tier data (00_Guide_Pricing.docx via supermemory.search_hybrid).
CARD = PricingRateCard(
    rates=[
        _rate(
            "guide-1.1-average",
            "Stakeholder Interviews (Discovery & Research)",
            6000,
            8000,
        ),
        _rate(
            "guide-2.1-average",
            "Strategic Plan Document Production",
            6000,
            9000,
        ),
        _rate(
            "guide-3.1-average",
            "Implementation Roadmap",
            12000,
            18000,
        ),
        _rate(
            "guide-9.1-average",
            "Project Management & Administration (Short Projects)",
            7500,
            12000,
        ),
    ]
)


def _line(item_id: str, description: str, extended: float) -> BudgetLineItem:
    return BudgetLineItem(
        id=item_id,
        category="Digital Marketing",
        description=description,
        extended=extended,
    )


def _budget(*pairs: tuple[str, float]) -> ProposalBudget:
    return ProposalBudget(
        rfpId="rfp-underbid-floor-test",
        updatedAt="2026-08-05T00:00:00+00:00",
        lineItems=[
            _line(f"L{i + 1:02d}", description, extended)
            for i, (description, extended) in enumerate(pairs)
        ],
    )


class UnderbidFloorTests(unittest.TestCase):
    def test_the_observed_underbid_is_rejected(self) -> None:
        """Reproduces the real defect: ~$3,500 priced against a ~$24,000+ guide floor."""
        budget = _budget(
            ("Discovery & stakeholder interviews", 1000),
            ("Strategic plan document", 1000),
            ("Implementation roadmap", 1500),
        )
        violations = collect_underbid_violations(budget, CARD)
        self.assertTrue(violations)
        self.assertIn("24,000", violations[0].replace(" ", ""))

    def test_a_budget_inside_the_bands_passes(self) -> None:
        budget = _budget(
            ("Discovery & stakeholder interviews", 7000),
            ("Strategic plan document", 7000),
            ("Implementation roadmap", 15000),
        )
        self.assertEqual(collect_underbid_violations(budget, CARD), [])

    def test_a_deliberate_discount_inside_tolerance_passes(self) -> None:
        """60% of floor is allowed — a real discount, not a 10x error."""
        budget = _budget(("Implementation roadmap", 8000))  # floor 12000, ratio 0.67
        self.assertEqual(collect_underbid_violations(budget, CARD), [])

    def test_unmatched_line_items_do_not_create_a_floor(self) -> None:
        budget = _budget(("Bespoke accessibility remediation", 500))
        self.assertEqual(collect_underbid_violations(budget, CARD), [])

    def test_empty_rate_card_is_not_a_violation(self) -> None:
        empty_card = PricingRateCard(rates=[])
        self.assertEqual(
            collect_underbid_violations(_budget(("Discovery", 100)), empty_card), []
        )

    def test_missing_rate_card_is_not_a_violation(self) -> None:
        """The rate card could not be loaded — never halt because the guide is unavailable."""
        self.assertEqual(collect_underbid_violations(_budget(("Discovery", 100)), None), [])

    def test_travel_is_excluded_from_the_floor_comparison(self) -> None:
        """Direct expenses are billed at cost and have no guide band."""
        budget = _budget(
            ("Implementation roadmap", 15000),
            ("Travel — site visits", 200),
        )
        self.assertEqual(collect_underbid_violations(budget, CARD), [])

    def test_multiple_tiers_for_one_service_use_the_lowest_documented_floor(self) -> None:
        """A service with Low/Average/High rows must floor against the Low tier,
        not whichever tier happens to be matched first."""
        card = PricingRateCard(
            rates=[
                _rate("guide-3.1-low", "Implementation Roadmap", 9000, 12000, tier="Low"),
                _rate("guide-3.1-average", "Implementation Roadmap", 12000, 18000, tier="Average"),
                _rate("guide-3.1-high", "Implementation Roadmap", 18000, 24000, tier="High"),
            ]
        )
        # 60% of the true (Low-tier) floor of 9000 is 5400 — 6000 should clear it.
        budget = _budget(("Implementation roadmap", 6000))
        self.assertEqual(collect_underbid_violations(budget, card), [])
        # But materially below even the Low tier must still be rejected.
        budget = _budget(("Implementation roadmap", 3000))
        violations = collect_underbid_violations(budget, card)
        self.assertTrue(violations)
        self.assertIn("9,000", violations[0].replace(" ", ""))


class BudgetEditorPipelineUnderbidTests(unittest.TestCase):
    """Prove the check is actually wired into the pipeline, not just unit-tested in
    isolation — Task 1 and Task 2 both shipped green unit tests while the real
    pipeline still produced the defect. This drives the same entry point
    (run_budget_editor_pass) the real Stage 3 budget generator and the
    reconcile_cached_budget path call before a budget is considered final.
    """

    def test_run_budget_editor_pass_halts_on_the_observed_defect(self) -> None:
        """~$3,500 against a ~$27,750 guide floor must HALT the pipeline (422),
        not ship with an advisory [PRICING FLAG]."""
        budget = ProposalBudget(
            rfpId="rfp-observed-defect",
            updatedAt="2026-08-05T00:00:00+00:00",
            lineItems=[
                _line("L01", "Discovery & stakeholder interviews", 1000),
                _line("L02", "Strategic plan document production", 1000),
                _line("L03", "Implementation roadmap", 1000),
                _line("L04", "Project management & administration", 500),
            ],
        )
        with self.assertRaises(ProposalError) as ctx:
            run_budget_editor_pass(budget, rate_card=CARD)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("00_Guide_Pricing", str(ctx.exception))

    def test_run_budget_editor_pass_passes_a_correctly_priced_budget(self) -> None:
        """A budget priced inside the guide bands must reconcile normally — the
        floor check must not fire on correct fee-only budgets (Task 2's lesson)."""
        budget = ProposalBudget(
            rfpId="rfp-correctly-priced",
            updatedAt="2026-08-05T00:00:00+00:00",
            lineItems=[
                _line("L01", "Discovery & stakeholder interviews", 7000),
                _line("L02", "Strategic plan document production", 7000),
                _line("L03", "Implementation roadmap", 15000),
                _line("L04", "Project management & administration", 9000),
            ],
        )
        finalized = run_budget_editor_pass(budget, rate_card=CARD)
        self.assertGreater(float(finalized.agency_revenue_estimate or 0), 0)
        self.assertEqual(collect_underbid_violations(finalized, CARD), [])

    def test_run_budget_editor_pass_never_halts_without_a_rate_card(self) -> None:
        """No rate_card passed (guide unavailable) — the pipeline must not halt
        on a check it had no data to run."""
        budget = ProposalBudget(
            rfpId="rfp-no-guide",
            updatedAt="2026-08-05T00:00:00+00:00",
            lineItems=[_line("L01", "Discovery & stakeholder interviews", 100)],
        )
        finalized = run_budget_editor_pass(budget)  # rate_card defaults to None
        self.assertGreater(float(finalized.agency_revenue_estimate or 0), 0)


if __name__ == "__main__":
    unittest.main()
