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

    def test_policy_limit_amounts_are_not_unauthorized_bid_currency(self) -> None:
        b = _budget(
            agencyRevenueEstimate=116_471,
            totalClientInvoicing=116_471,
            clientMediaPassthrough=None,
            lineItems=[
                BudgetLineItem(
                    id="a",
                    category="Fees",
                    description="Agency fee",
                    extended=116_471,
                    lineItemType="agency_fee",
                ),
            ],
        )
        self.assertFalse(
            introduces_unauthorized_dollars(
                "Commercial General Liability: $1,000,000 per occurrence / $2,000,000 aggregate.",
                b,
            )
        )
        issues = scan_manuscript_consistency(
            draft=ProposalDraft(
                rfpId="r1",
                updatedAt="2026-01-01T00:00:00Z",
                sections=[
                    ProposalSection(
                        id="ins",
                        title="Insurance",
                        content=(
                            "CGL coverage limit $1,000,000 per occurrence and "
                            "$2,000,000 aggregate."
                        ),
                        status="generated",
                    )
                ],
            ),
            research=ProposalResearchCache(
                rfpId="r1",
                updatedAt="2026-01-01T00:00:00Z",
                budget=b,
            ),
            rfp=_rfp(),
        )
        free = [i for i in issues if "free_currency" in (i.message or "")]
        self.assertEqual(free, [])

    def test_tuition_compact_and_allocation_not_free_currency(self) -> None:
        b = _budget(
            agencyRevenueEstimate=85_529,
            totalClientInvoicing=85_529,
            clientMediaPassthrough=None,
            lineItems=[
                BudgetLineItem(
                    id="a",
                    category="Fees",
                    description="Agency fee",
                    extended=85_529,
                    lineItemType="agency_fee",
                )
            ],
        )
        issues = scan_manuscript_consistency(
            draft=ProposalDraft(
                rfpId="r1",
                updatedAt="t",
                sections=[
                    ProposalSection(
                        id="s1",
                        title="Creative",
                        content=(
                            "Proof point: tuition under $12K/year for in-state students. "
                            "Must remain within the $120,000 allocation MSU Denver has set."
                        ),
                        status="generated",
                    )
                ],
            ),
            research=ProposalResearchCache(
                rfpId="r1",
                updatedAt="2026-01-01T00:00:00Z",
                budget=b,
            ),
            rfp=_rfp(),
        )
        free = [i for i in issues if "free_currency" in (i.message or "")]
        self.assertEqual(free, [])

    def test_stray_fee_amount_is_pass_a_candidate_not_sync_free_currency(self) -> None:
        """Sync scan no longer emits regex free_currency; Pass A candidates still see it."""
        from app.services.proposal_money_intelligence import collect_currency_candidates

        b = _budget(
            agencyRevenueEstimate=116_471,
            totalClientInvoicing=116_471,
            clientMediaPassthrough=None,
            lineItems=[
                BudgetLineItem(
                    id="a",
                    category="Fees",
                    description="Agency fee",
                    extended=116_471,
                    lineItemType="agency_fee",
                ),
            ],
        )
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="2026-01-01T00:00:00Z",
            sections=[
                ProposalSection(
                    id="pm",
                    title="Project Management",
                    content="Optional travel day rate of $150 for island work.",
                    status="generated",
                )
            ],
        )
        issues = scan_manuscript_consistency(
            draft=draft,
            research=ProposalResearchCache(
                rfpId="r1",
                updatedAt="2026-01-01T00:00:00Z",
                budget=b,
            ),
            rfp=_rfp(),
        )
        self.assertFalse(any("free_currency" in (i.message or "") for i in issues))
        candidates = collect_currency_candidates(draft, b)
        self.assertTrue(
            any(abs(float(c["amountValue"]) - 150) < 0.01 for c in candidates),
            candidates,
        )


if __name__ == "__main__":
    unittest.main()
