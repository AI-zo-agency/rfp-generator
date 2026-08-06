"""Task 17 — drive the REAL Scan-RFP entry point end to end against a real
(non-Supabase) database and prove the budget check actually reaches it.

Mirrors tests/test_scan_rfp_reconciler_wiring.py's pattern: nothing about the
budget machinery itself is mocked; only the database backend (sqlite instead
of Supabase) and the LLM (``llm.is_configured`` patched False — the pipeline
must stay reachable/zero-spend with no LLM configured; this file's own
assertions never depend on an LLM call happening).
"""

from __future__ import annotations

import itertools
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import config
from app.models.pricing_rate_card import PricingRate, PricingRateCard
from app.models.proposal import (
    BudgetLineItem,
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.models.rfp import RfpRecord
from app.services import proposal_repository as repo
from app.services.rfp_repository import upsert_rfp

_ids = itertools.count()


def _line(description: str, extended: float, **kw) -> BudgetLineItem:
    kw.setdefault("id", f"L{next(_ids):03d}")
    kw.setdefault("category", kw.pop("category", "Digital Marketing"))
    return BudgetLineItem(description=description, extended=extended, **kw)


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


def _rfp(rfp_id: str, **overrides) -> RfpRecord:
    fields = dict(
        id=rfp_id,
        title="Downtown Marketing Services",
        client="City of Rivergate",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-05",
        lastActivityNote="note",
        goNoGo="go",
        description="Background context for the engagement. " * 20,
    )
    fields.update(overrides)
    return RfpRecord(**fields)


class _RealDbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db = Path(self._tmpdir.name) / "scan-rfp-budget-wiring.db"
        self._patchers = [
            patch.object(config.settings, "database_path", self._db),
            patch.object(repo, "_use_supabase", return_value=False),
            patch("app.services.rfp_repository._use_supabase", return_value=False),
            patch("app.services.supabase_db.use_supabase_db", return_value=False),
            # Hermetic and zero-spend, same rationale as
            # test_scan_rfp_reconciler_wiring.py's module docstring.
            patch("app.services.llm.is_configured", return_value=False),
        ]
        for p in self._patchers:
            p.start()
        repo.init_proposal_db()

    async def asyncTearDown(self) -> None:
        for p in reversed(self._patchers):
            p.stop()
        self._tmpdir.cleanup()


class RealEntryPointRepairsTheBudgetTests(_RealDbTestCase):
    async def test_the_3500_classification_defect_is_repaired_through_the_real_button_path(
        self,
    ) -> None:
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-budget-wiring-classification"
        upsert_rfp(_rfp(rfp_id))
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(id="s1", title="Approach", content="Our approach is sound."),
                ProposalSection(
                    id="section-budget-pricing",
                    title="Budget & Pricing",
                    content="## Proposed Investment\n\n**Total proposed investment: $3,500**\n",
                ),
            ],
            updatedAt="2026-08-06T00:00:00Z",
        )
        await repo.asave_proposal_draft(draft)
        broken_budget = ProposalBudget(
            rfpId=rfp_id,
            updatedAt="2026-08-06T00:00:00Z",
            lineItems=[
                _line("Travel — on-site listening sessions", 3500.0, category="travel"),
            ],
            agencyFeeSubtotal=3500.0,
            agencyRevenueEstimate=3500.0,
            lineItemSum=3500.0,
        )
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                updatedAt="2026-08-06T00:00:00Z",
                budget=broken_budget,
            )
        )

        _review, research_after, draft_after, report = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )

        print("\n=== Task 17 — REAL verify_scrub_only entry point, budget repair ===")
        print(f"budgetStatus={report.get('budgetStatus')!r}")
        print(f"budgetChanged={report.get('budgetChanged')!r}")
        print(f"budgetRepairedNotes={report.get('budgetRepairedNotes')!r}")
        print(f"budgetEscalationNotes={report.get('budgetEscalationNotes')!r}")

        self.assertEqual(report.get("budgetStatus"), "repaired")
        self.assertTrue(report.get("budgetChanged"))
        self.assertEqual(research_after.budget.agency_fee_subtotal, 0.0)
        self.assertEqual(research_after.budget.agency_revenue_estimate, 3500.0)

        budget_section = next(
            s for s in draft_after.sections if s.id == "section-budget-pricing"
        )
        self.assertIn("$3,500", budget_section.content)
        self.assertNotIn("Professional fees: $3,500", budget_section.content)

    async def test_idempotent_through_the_real_entry_point(self) -> None:
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-budget-wiring-idempotent"
        upsert_rfp(_rfp(rfp_id))
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[ProposalSection(id="s1", title="Approach", content="Our approach is sound.")],
            updatedAt="2026-08-06T00:00:00Z",
        )
        await repo.asave_proposal_draft(draft)
        broken_budget = ProposalBudget(
            rfpId=rfp_id,
            updatedAt="2026-08-06T00:00:00Z",
            lineItems=[
                _line("Travel — on-site listening sessions", 3500.0, category="travel"),
            ],
            agencyFeeSubtotal=3500.0,
            agencyRevenueEstimate=3500.0,
            lineItemSum=3500.0,
        )
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id, updatedAt="2026-08-06T00:00:00Z", budget=broken_budget
            )
        )

        _r1, research1, _d1, report1 = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )
        _r2, research2, _d2, report2 = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )

        print("\n=== Task 17 — REAL verify_scrub_only entry point, budget idempotence ===")
        print(f"first:  budgetStatus={report1.get('budgetStatus')!r} changed={report1.get('budgetChanged')!r}")
        print(f"second: budgetStatus={report2.get('budgetStatus')!r} changed={report2.get('budgetChanged')!r}")

        self.assertEqual(report1.get("budgetStatus"), "repaired")
        self.assertEqual(report2.get("budgetStatus"), "ok")
        self.assertFalse(report2.get("budgetChanged"))
        self.assertEqual(
            research1.budget.model_dump(), research2.budget.model_dump()
        )

    async def test_underbid_is_escalated_not_silently_fixed_and_never_aborts(self) -> None:
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-budget-wiring-underbid"
        upsert_rfp(_rfp(rfp_id))
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[ProposalSection(id="s1", title="Approach", content="Our approach is sound.")],
            updatedAt="2026-08-06T00:00:00Z",
        )
        await repo.asave_proposal_draft(draft)
        underbid = ProposalBudget(
            rfpId=rfp_id,
            updatedAt="2026-08-06T00:00:00Z",
            lineItems=[
                _line("Discovery & stakeholder interviews", 1000.0),
                _line("Strategic plan document production", 1000.0),
                _line("Implementation roadmap", 1500.0),
            ],
        )
        # Task 17.7: research.pricing_rate_card must actually be available on
        # this path, or the underbid floor silently no-ops (the defect this
        # project already fixed once for run_fulfill_budget_scan).
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                updatedAt="2026-08-06T00:00:00Z",
                budget=underbid,
                pricingRateCard=GUIDE_CARD.model_dump(by_alias=True),
            )
        )

        _review, research_after, _draft_after, report = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )

        print("\n=== Task 17 — REAL verify_scrub_only entry point, underbid escalation ===")
        print(f"budgetStatus={report.get('budgetStatus')!r}")
        print(f"budgetEscalationNotes={report.get('budgetEscalationNotes')!r}")
        print(f"humanDecisionGaps={report.get('humanDecisionGaps')!r}")

        self.assertIn(report.get("budgetStatus"), ("needs_human", "repaired_needs_human"))
        self.assertTrue(report.get("budgetEscalationNotes"))
        self.assertTrue(
            any("budget:needs-review" in g for g in report.get("humanDecisionGaps", []))
        )
        # Never fabricated: priced dollars are exactly what was seeded.
        final_sum = sum(
            float(li.extended or 0) for li in research_after.budget.line_items
        )
        self.assertEqual(final_sum, 3500.0)

    async def test_no_budget_yet_never_errors_and_reports_no_false_failure(self) -> None:
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-budget-wiring-nobudget"
        upsert_rfp(_rfp(rfp_id))
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[ProposalSection(id="s1", title="Approach", content="Plain prose, no budget yet.")],
            updatedAt="2026-08-06T00:00:00Z",
        )
        await repo.asave_proposal_draft(draft)
        # No research cache saved at all.

        _review, _research, _draft_after, report = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )

        self.assertEqual(report.get("budgetStatus"), "none")
        self.assertFalse(report.get("budgetChanged"))
        self.assertEqual(report.get("budgetEscalationNotes"), [])


if __name__ == "__main__":
    unittest.main()
