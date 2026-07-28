"""Cross-section consistency + deterministic budget claim checks."""

from __future__ import annotations

import unittest

from app.models.proposal import (
    BudgetLineItem,
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.models.rfp import RfpRecord
from app.services.proposal_budget_sync import collect_deterministic_budget_mismatches
from app.services.proposal_consistency import (
    allowed_budget_amounts,
    introduces_unauthorized_dollars,
    scan_manuscript_consistency,
)


def _budget(**kwargs: object) -> ProposalBudget:
    defaults = dict(
        rfpId="r1",
        pricingTier="Average",
        updatedAt="2026-01-01T00:00:00Z",
        lineItems=[],
        agencyRevenueEstimate=50_000,
        agencyFeeSubtotal=50_000,
        clientMediaPassthrough=200_000,
        totalClientInvoicing=250_000,
    )
    defaults.update(kwargs)
    return ProposalBudget(**defaults)  # type: ignore[arg-type]


def _rfp() -> RfpRecord:
    return RfpRecord(
        id="r1",
        title="Test RFP",
        client="Acme County",
        dueDate="2026-12-01",
        receivedDate="2026-01-01",
        lastActivity="2026-01-01T00:00:00Z",
        lastActivityNote="test",
    )


class ConsistencyTests(unittest.TestCase):
    def test_allowed_amounts_include_agency_and_passthrough(self) -> None:
        b = _budget(
            lineItems=[
                BudgetLineItem(
                    id="a",
                    category="Fees",
                    description="Agency fee",
                    extended=50_000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="b",
                    category="Media",
                    description="Media",
                    extended=200_000,
                    lineItemType="client_passthrough",
                ),
            ]
        )
        allowed = allowed_budget_amounts(b)
        self.assertIn(50_000.0, allowed)
        self.assertIn(200_000.0, allowed)
        self.assertIn(250_000.0, allowed)

    def test_deterministic_mismatch_catches_agency_vs_total_swap(self) -> None:
        b = _budget(
            lineItems=[
                BudgetLineItem(
                    id="a",
                    category="Fees",
                    description="Agency fee",
                    extended=50_000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="b",
                    category="Media",
                    description="Media",
                    extended=200_000,
                    lineItemType="client_passthrough",
                ),
            ]
        )
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="2026-01-01T00:00:00Z",
            sections=[
                ProposalSection(
                    id="s1",
                    title="Approach",
                    content="Total Year 1 agency fee: $250,000. Total Year 1 client invoicing: $50,000.",
                    status="generated",
                )
            ],
        )
        mismatches = collect_deterministic_budget_mismatches(draft, b)
        fields = {m.claimed_field for m in mismatches}
        self.assertIn("agency_fee", fields)
        self.assertIn("total_invoicing", fields)

    def test_scan_flags_conflicting_team_sizes(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="2026-01-01T00:00:00Z",
            sections=[
                ProposalSection(
                    id="t1",
                    title="Team",
                    content="We staff a team of 4 for this engagement.",
                    status="generated",
                ),
                ProposalSection(
                    id="t2",
                    title="Approach",
                    content="Our 8-person team leads discovery and delivery.",
                    status="generated",
                ),
            ],
        )
        issues = scan_manuscript_consistency(
            draft=draft,
            research=ProposalResearchCache(
                rfpId="r1",
                updatedAt="2026-01-01T00:00:00Z",
            ),
            rfp=_rfp(),
        )
        self.assertTrue(
            any("team-size" in (i.message or "").casefold() for i in issues),
            msg=[i.message for i in issues],
        )

    def test_unauthorized_dollar_detection(self) -> None:
        b = _budget(
            agencyRevenueEstimate=50_000,
            totalClientInvoicing=50_000,
            clientMediaPassthrough=None,
            lineItems=[
                BudgetLineItem(
                    id="a",
                    category="Fees",
                    description="Agency fee",
                    extended=50_000,
                    lineItemType="agency_fee",
                ),
            ],
        )
        self.assertTrue(
            introduces_unauthorized_dollars(
                "Investment is $999,999 for the full program.",
                b,
            )
        )
        self.assertFalse(
            introduces_unauthorized_dollars(
                "Investment is $50,000 for the full program.",
                b,
            )
        )


if __name__ == "__main__":
    unittest.main()
