"""Task 17 — the Scan-RFP button must check the persisted budget and repair
it deterministically, never silently, and never by inventing a dollar amount.

Root gap (verified at HEAD 9861115): the real Scan-RFP button always calls
``run_fulfill_rfp_gaps(rfp_id, mode="verify_scrub_only")``
(frontend/src/lib/proposal-api.ts hardcodes this), which routes to
``run_verify_scrub_only_scan`` — and that function's own module docstring
says "Does NOT add closing tabs, structure, budget, or KPI passes — VERIFY
scrub only." ``run_fulfill_budget_scan`` (the only budget-aware pass in this
codebase) only runs on ``mode="full"``, which the button never sends. So none
of: prose arithmetic (fee+direct+passthrough==total), the underbid floor
against 00_Guide_Pricing, RFP-forbidden travel/reimbursable lines, or
line-item classification (the $3,500 defect) ever ran on a Scan-RFP click.

These tests drive ``check_and_repair_budget_for_scan`` directly — the new
entry point wired into ``run_verify_scrub_only_scan``
(app/services/proposal_verify_optional_scrub.py). It reuses the existing
deterministic machinery (run_budget_editor_pass / reconcile_proposal_budget /
validate_budget_canonical / collect_underbid_violations /
collect_rfp_constraint_violations / collect_prose_arithmetic_violations) —
nothing here reimplements a second validator.

Zero LLM calls anywhere in this file — check_and_repair_budget_for_scan never
calls ``llm``.
"""

from __future__ import annotations

import itertools
import unittest

from app.models.pricing_rate_card import PricingRate, PricingRateCard
from app.models.proposal import (
    BudgetLineItem,
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.services.proposal_scan_budget_check import check_and_repair_budget_for_scan

_ids = itertools.count()


def _line(description: str, extended: float, **kw) -> BudgetLineItem:
    kw.setdefault("id", f"L{next(_ids):03d}")
    kw.setdefault("category", kw.pop("category", "Digital Marketing"))
    return BudgetLineItem(description=description, extended=extended, **kw)


def _budget(*items: BudgetLineItem, **kw) -> ProposalBudget:
    return ProposalBudget(
        rfpId="rfp-budget-check-test",
        updatedAt="2026-08-05T00:00:00+00:00",
        lineItems=list(items),
        **kw,
    )


def _draft(sections: list[ProposalSection] | None = None) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-budget-check-test",
        sections=sections or [ProposalSection(id="s1", title="Approach", content="Our approach.")],
        updatedAt="2026-08-05T00:00:00+00:00",
    )


def _research(budget: ProposalBudget | None, **kw) -> ProposalResearchCache:
    return ProposalResearchCache(
        rfpId="rfp-budget-check-test",
        updatedAt="2026-08-05T00:00:00+00:00",
        budget=budget,
        **kw,
    )


def _rate(rate_id: str, service: str, low: float, high: float) -> PricingRate:
    return PricingRate(
        rate_id=rate_id,
        service=service,
        tier="Average",
        unit="fixed",
        amount=round((low + high) / 2.0, 2),
        amount_low=low,
        amount_high=high,
        menu_id="",
        source_doc="00_Guide_Pricing",
        confidence=0.95,
        notes="",
    )


GUIDE_CARD = PricingRateCard(
    rates=[
        _rate("guide-1.1", "Stakeholder Interviews (Discovery & Research)", 6000, 8000),
        _rate("guide-2.1", "Strategic Plan Document Production", 6000, 9000),
        _rate("guide-3.1", "Implementation Roadmap", 12000, 18000),
    ]
)


class NoBudgetYetTests(unittest.TestCase):
    def test_no_persisted_budget_is_not_an_error(self) -> None:
        result = check_and_repair_budget_for_scan(
            rfp_id="rfp-budget-check-test",
            draft=_draft(),
            research=_research(None),
            rfp_text="",
        )
        self.assertEqual(result.status, "none")
        self.assertFalse(result.changed)
        self.assertEqual(result.escalation_notes, [])

    def test_no_research_cache_at_all_is_not_an_error(self) -> None:
        result = check_and_repair_budget_for_scan(
            rfp_id="rfp-budget-check-test",
            draft=_draft(),
            research=None,
            rfp_text="",
        )
        self.assertEqual(result.status, "none")
        self.assertFalse(result.changed)


class ClassificationDefectRepairedTests(unittest.TestCase):
    """The $3,500 defect: one travel line counted as fee AND travel AND total."""

    def test_misclassified_travel_line_is_repaired_by_the_scan(self) -> None:
        # Simulates a budget persisted before the classification fix existed:
        # agencyFeeSubtotal/agencyRevenueEstimate/lineItemSum all equal the
        # travel line's own dollar amount — fee == travel == total.
        broken = _budget(
            _line("Travel — on-site listening sessions", 3500.0, category="travel"),
            agencyFeeSubtotal=3500.0,
            agencyRevenueEstimate=3500.0,
            lineItemSum=3500.0,
            lumpSumTotal=3500.0,
        )
        result = check_and_repair_budget_for_scan(
            rfp_id="rfp-budget-check-test",
            draft=_draft(),
            research=_research(broken),
            rfp_text="",
        )
        self.assertEqual(result.status, "repaired")
        self.assertTrue(result.changed)
        self.assertTrue(result.repaired_notes)
        self.assertEqual(result.escalation_notes, [])

        repaired = result.research.budget
        self.assertEqual(repaired.agency_fee_subtotal, 0.0, "travel must not be an agency fee")
        self.assertEqual(repaired.agency_revenue_estimate, 3500.0)
        self.assertEqual(repaired.line_item_sum, 3500.0)

    def test_mixed_budget_reclassifies_travel_out_of_the_fee_subtotal(self) -> None:
        broken = _budget(
            _line("Strategy & creative foundation", 14000.0),
            _line("Travel — site visits", 3500.0, category="travel"),
            agencyFeeSubtotal=17500.0,  # wrong: travel folded into fee
        )
        result = check_and_repair_budget_for_scan(
            rfp_id="rfp-budget-check-test",
            draft=_draft(),
            research=_research(broken),
            rfp_text="",
        )
        self.assertEqual(result.status, "repaired")
        self.assertEqual(result.research.budget.agency_fee_subtotal, 14000.0)
        self.assertEqual(result.research.budget.agency_revenue_estimate, 17500.0)


class HealthyBudgetTests(unittest.TestCase):
    def test_a_healthy_budget_passes_with_no_changes_and_no_false_findings(self) -> None:
        # Already-reconciled shape — running reconcile again must be a no-op.
        clean = _budget(_line("Strategy & creative foundation", 14000.0))
        clean = _budget(
            *clean.line_items,
            agencyFeeSubtotal=14000.0,
            agencyRevenueEstimate=14000.0,
            lineItemSum=14000.0,
            lumpSumTotal=14000.0,
        )
        # Pre-reconcile once (as the real pipeline would have already done at
        # generation time) so this fixture genuinely represents "already
        # clean", not "happens not to trip the checks".
        from app.services.proposal_budget_validation import reconcile_proposal_budget

        clean = reconcile_proposal_budget(clean)

        result = check_and_repair_budget_for_scan(
            rfp_id="rfp-budget-check-test",
            draft=_draft(),
            research=_research(clean),
            rfp_text="",
        )
        self.assertEqual(result.status, "ok")
        self.assertFalse(result.changed)
        self.assertEqual(result.repaired_notes, [])
        self.assertEqual(result.escalation_notes, [])
        # Object must be untouched — same values, not just "no violations".
        self.assertEqual(result.research.budget.model_dump(), clean.model_dump())


class UnderbidReportedNotFixedTests(unittest.TestCase):
    def test_materially_below_guide_floor_is_reported_for_a_human(self) -> None:
        underbid = _budget(
            _line("Discovery & stakeholder interviews", 1000.0),
            _line("Strategic plan document", 1000.0),
            _line("Implementation roadmap", 1500.0),
        )
        result = check_and_repair_budget_for_scan(
            rfp_id="rfp-budget-check-test",
            draft=_draft(),
            research=_research(underbid, pricingRateCard=GUIDE_CARD.model_dump(by_alias=True)),
            rfp_text="",
        )
        self.assertIn(result.status, ("needs_human", "repaired_needs_human"))
        self.assertTrue(result.escalation_notes)
        self.assertTrue(
            any("00_Guide_Pricing" in n or "floor" in n.lower() for n in result.escalation_notes)
        )
        # Never fabricated: the priced dollar amounts must be untouched.
        final_line_sum = sum(float(li.extended or 0) for li in result.research.budget.line_items)
        self.assertEqual(final_line_sum, 3500.0, "must not invent dollars to clear the floor")

    def test_run_budget_editor_pass_raising_422_does_not_abort_the_scan(self) -> None:
        """The exact underbid case, asserted from the angle of 'no exception
        propagates' — run_budget_editor_pass raises ProposalError(422)
        internally; the caller (Scan-RFP) must never see it."""
        underbid = _budget(
            _line("Discovery & stakeholder interviews", 1000.0),
            _line("Strategic plan document", 1000.0),
            _line("Implementation roadmap", 1500.0),
        )
        try:
            result = check_and_repair_budget_for_scan(
                rfp_id="rfp-budget-check-test",
                draft=_draft(),
                research=_research(underbid, pricingRateCard=GUIDE_CARD.model_dump(by_alias=True)),
                rfp_text="",
            )
        except Exception as exc:  # noqa: BLE001
            self.fail(f"check_and_repair_budget_for_scan must never raise, got: {exc!r}")
        self.assertIsNotNone(result)


class RfpForbiddenTravelReportedTests(unittest.TestCase):
    REMOTE_RFP = (
        "2.7 Location of Work. All work under this agreement shall be performed remotely. "
        "No on-site presence is anticipated unless requested by the buyer."
    )

    def test_travel_line_in_a_remote_only_rfp_is_reported(self) -> None:
        budget = _budget(
            _line("Strategy & creative foundation", 14000.0),
            _line("Travel — site visits", 2500.0, category="travel"),
        )
        result = check_and_repair_budget_for_scan(
            rfp_id="rfp-budget-check-test",
            draft=_draft(),
            research=_research(budget),
            rfp_text=self.REMOTE_RFP,
        )
        self.assertIn(result.status, ("needs_human", "repaired_needs_human"))
        self.assertTrue(any("remote" in n.lower() for n in result.escalation_notes))
        # The travel line itself must still be present, dollar-for-dollar —
        # this is a human's call (remove the line, or cite the on-site
        # authorization clause), never an automatic deletion.
        travel_items = [
            li for li in result.research.budget.line_items if "travel" in (li.description or "").lower()
        ]
        self.assertEqual(len(travel_items), 1)
        self.assertEqual(float(travel_items[0].extended or 0), 2500.0)

    def test_run_budget_editor_pass_raising_422_for_forbidden_travel_does_not_abort(self) -> None:
        budget = _budget(_line("Travel — site visits", 2500.0, category="travel"))
        try:
            result = check_and_repair_budget_for_scan(
                rfp_id="rfp-budget-check-test",
                draft=_draft(),
                research=_research(budget),
                rfp_text=self.REMOTE_RFP,
            )
        except Exception as exc:  # noqa: BLE001
            self.fail(f"check_and_repair_budget_for_scan must never raise, got: {exc!r}")
        self.assertIsNotNone(result)


class UnrepairableInvariantEscalatesTests(unittest.TestCase):
    """A defect reconcile itself cannot fix (not arithmetic — provenance
    bookkeeping) must escalate, not silently ship a still-broken budget."""

    def test_missing_rate_provenance_after_binding_started_is_reported_and_not_persisted(
        self,
    ) -> None:
        budget = _budget(
            _line("Bound deliverable", 5000.0, isManualFill=True),
            _line("Unbound priced deliverable", 4000.0),  # no source_rate_id, not manual_fill
        )
        research = _research(budget)
        result = check_and_repair_budget_for_scan(
            rfp_id="rfp-budget-check-test",
            draft=_draft(),
            research=research,
            rfp_text="",
        )
        self.assertEqual(result.status, "needs_human")
        self.assertFalse(result.changed)
        self.assertTrue(result.escalation_notes)
        # Untouched: the original (broken) budget object is what's returned,
        # not a partially-patched one shipped as if it were fine.
        self.assertEqual(result.research.budget.model_dump(), budget.model_dump())


class ProseArithmeticTests(unittest.TestCase):
    def test_prose_mismatch_in_the_rendered_section_triggers_a_resync(self) -> None:
        # Canonical object is fine, but the manuscript's own rendered text
        # (e.g. stale LLM-authored prose from an earlier pass) does not add
        # up: fee == travel == total, the same triplet defect at the prose
        # layer instead of the object layer.
        from app.services.proposal_budget_validation import reconcile_proposal_budget

        clean = reconcile_proposal_budget(_budget(_line("Strategy & creative foundation", 60000.0)))
        stale_section = ProposalSection(
            id="section-budget-pricing",
            title="Budget & Pricing",
            content=(
                "## Proposed Investment\n\n"
                "**Professional fees: $3,500**\n"
                "**Direct travel / reimbursables: $3,500**\n"
                "**Total proposed investment: $3,500**\n"
            ),
        )
        result = check_and_repair_budget_for_scan(
            rfp_id="rfp-budget-check-test",
            draft=_draft([stale_section]),
            research=_research(clean),
            rfp_text="",
        )
        self.assertTrue(result.changed)
        budget_section = next(s for s in result.draft.sections if s.id == "section-budget-pricing")
        self.assertIn("$60,000", budget_section.content)
        self.assertNotIn("$3,500", budget_section.content)


class IdempotenceTests(unittest.TestCase):
    def test_scanning_the_same_repaired_budget_twice_is_a_no_op_the_second_time(self) -> None:
        broken = _budget(
            _line("Travel — on-site listening sessions", 3500.0, category="travel"),
            agencyFeeSubtotal=3500.0,
            agencyRevenueEstimate=3500.0,
            lineItemSum=3500.0,
        )
        first = check_and_repair_budget_for_scan(
            rfp_id="rfp-budget-check-test",
            draft=_draft(),
            research=_research(broken),
            rfp_text="",
        )
        self.assertEqual(first.status, "repaired")

        second = check_and_repair_budget_for_scan(
            rfp_id="rfp-budget-check-test",
            draft=first.draft,
            research=first.research,
            rfp_text="",
        )
        self.assertEqual(second.status, "ok")
        self.assertFalse(second.changed)
        self.assertEqual(second.research.budget.model_dump(), first.research.budget.model_dump())

    def test_scanning_an_unresolved_underbid_twice_reports_but_never_rewrites(self) -> None:
        underbid = _budget(
            _line("Discovery & stakeholder interviews", 1000.0),
            _line("Strategic plan document", 1000.0),
            _line("Implementation roadmap", 1500.0),
        )
        research = _research(underbid, pricingRateCard=GUIDE_CARD.model_dump(by_alias=True))
        first = check_and_repair_budget_for_scan(
            rfp_id="rfp-budget-check-test", draft=_draft(), research=research, rfp_text=""
        )
        second = check_and_repair_budget_for_scan(
            rfp_id="rfp-budget-check-test",
            draft=first.draft,
            research=first.research,
            rfp_text="",
        )
        # First pass may still repair arithmetic on top of escalating
        # ("repaired_needs_human"); once that arithmetic fix is persisted, a
        # second pass has nothing left to repair and must not re-claim a
        # repair it already made — only the still-unresolved pricing
        # judgement carries forward ("needs_human").
        self.assertIn(first.status, ("needs_human", "repaired_needs_human"))
        self.assertEqual(second.status, "needs_human")
        self.assertFalse(second.changed)
        self.assertEqual(
            first.research.budget.model_dump(), second.research.budget.model_dump()
        )


if __name__ == "__main__":
    unittest.main()
