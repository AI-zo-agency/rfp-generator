"""Tests for T5.3 orphan commission / derived-without-base invariants."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_validation import (
    collect_orphan_commission_violations,
)
from app.services.proposal_consistency import scan_manuscript_consistency
from tests.fixtures.manuscripts.loader import load_fixture


class OrphanCommissionInvariantTests(unittest.TestCase):
    def test_cvvb_v2_fixture_flags_orphan_commission_in_manuscript(self) -> None:
        draft, research, rfp, expected = load_fixture(
            "cvvb_v2_truncation_orphan_commission"
        )
        self.assertIn("orphan_commission", expected["critical"])
        issues = scan_manuscript_consistency(draft=draft, research=research, rfp=rfp)
        messages = " ".join(i.message for i in issues).lower()
        self.assertIn("orphan", messages)
        self.assertIn("commission", messages)

    def test_commission_with_matching_base_in_text_ok(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalSection
        from app.models.rfp import RfpRecord

        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="s1",
                    title="Budget",
                    content=(
                        "Media buy base is $112,500.00. "
                        "Commission on media placements is $16,875.00 (15% of planned buys)."
                    ),
                )
            ],
        )
        rfp = RfpRecord(
            id="r1",
            title="t",
            client="c",
            dueDate="2026-01-01",
            receivedDate="2026-01-01",
            lastActivity="2026-01-01",
            lastActivityNote="t",
        )
        issues = scan_manuscript_consistency(draft=draft, research=None, rfp=rfp)
        orphan = [i for i in issues if "orphan commission" in i.message.lower()]
        self.assertEqual(orphan, [])

    def test_budget_commission_line_without_passthrough_base(self) -> None:
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    category="Fees",
                    description="Agency commission on media (15%)",
                    extended=16_875.0,
                    lineItemType="agency_fee",
                )
            ],
            agencyRevenueEstimate=16_875.0,
            agencyFeeSubtotal=16_875.0,
            clientMediaPassthrough=0,
        )
        violations = collect_orphan_commission_violations(budget)
        self.assertTrue(violations)
        self.assertTrue(any("commission" in v.lower() for v in violations))

    def test_budget_commission_with_passthrough_ok(self) -> None:
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            lineItems=[
                BudgetLineItem(
                    id="M1",
                    category="Media",
                    description="Client media pass-through placements",
                    extended=112_500.0,
                    lineItemType="client_passthrough",
                ),
                BudgetLineItem(
                    id="L1",
                    category="Fees",
                    description="Agency commission on media (15%)",
                    extended=16_875.0,
                    lineItemType="agency_fee",
                ),
            ],
            agencyRevenueEstimate=16_875.0,
            agencyFeeSubtotal=16_875.0,
            clientMediaPassthrough=112_500.0,
        )
        self.assertEqual(collect_orphan_commission_violations(budget), [])


if __name__ == "__main__":
    unittest.main()
