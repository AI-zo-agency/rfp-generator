"""Client-facing budget render: one total, phase fees, no travel double-bill."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_content import (
    prepare_budget_for_client_display,
    render_budget_markdown,
)


def _budget(**kwargs: object) -> ProposalBudget:
    defaults = dict(
        rfpId="r1",
        pricingTier="Average",
        updatedAt="2026-01-01T00:00:00Z",
        lineItems=[],
    )
    defaults.update(kwargs)
    return ProposalBudget(**defaults)  # type: ignore[arg-type]


class ClientBudgetRenderTests(unittest.TestCase):
    def test_syncs_stale_scope_total_and_dedupes_travel(self) -> None:
        b = _budget(
            lumpSumTotal=100000,
            agencyRevenueEstimate=100000,
            directExpensesTotal=7500,
            scopeSummary="Engagement totaling $78,500 for KVCC.",
            optionTermNotes="Base-year agency revenue estimate: $100,000",
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    category="Discovery",
                    description="Phase 1 Discovery — Stakeholder interviews (RFP 6.2.1)",
                    quantity=1,
                    unit="project",
                    rate=50000,
                    extended=50000,
                ),
                BudgetLineItem(
                    id="L2",
                    category="Travel",
                    description="Travel — two Maine trips",
                    quantity=1,
                    unit="project",
                    rate=7500,
                    extended=7500,
                ),
                BudgetLineItem(
                    id="L3",
                    category="Strategy",
                    description="Phase 2 Strategy — messaging architecture",
                    namedPerson="Sonja Anderson",
                    roleTitle="Strategy Lead",
                    quantity=1,
                    unit="project",
                    rate=42500,
                    extended=42500,
                ),
            ],
        )
        cleaned = prepare_budget_for_client_display(b)
        self.assertEqual(float(cleaned.direct_expenses_total or 0), 0.0)
        self.assertAlmostEqual(float(cleaned.lump_sum_total or 0), 100000.0, places=2)
        self.assertIn("100,000", cleaned.scope_summary or "")
        self.assertNotIn("78,500", cleaned.scope_summary or "")

        md = render_budget_markdown(b)
        self.assertIn("Proposed Investment", md)
        self.assertIn("Fee Detail by Phase", md)
        self.assertIn("Stakeholder interviews", md)
        self.assertNotIn("78,500", md)
        self.assertNotIn("agency revenue estimate", md.casefold())
        # Deliverable kept — not collapsed to Role — Person
        self.assertIn("messaging architecture", md.casefold())
        # Travel appears in header + investment sentence + fee table — once per place, not double-billed
        self.assertIn("Direct travel / reimbursables: $7,500", md)
        self.assertNotIn("directExpensesTotal", md)
        self.assertIn("| Travel / Reimbursables | Travel — two Maine trips | $7,500 |", md)
        # No second travel row as Direct expenses when already a Travel line
        self.assertNotIn("| Direct expenses | Travel / reimbursables | $7,500 |", md)

    def test_separates_professional_fees_from_travel(self) -> None:
        b = _budget(
            pricingTier="Average",
            directExpensesTotal=7_500,
            scopeSummary=(
                "Four-phase plan for KVCC. Total proposed investment: $10,499.99 "
                "including $10,499.99 in direct travel expenses."
            ),
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    category="Discovery",
                    description="Phase 1 Discovery — Stakeholder interviews",
                    extended=42_000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="L2",
                    category="Strategy",
                    description="Phase 2 Strategy — Messaging",
                    extended=18_000,
                    lineItemType="agency_fee",
                ),
            ],
        )
        md = render_budget_markdown(b)
        self.assertIn("**Professional fees: $60,000**", md)
        self.assertIn("**Direct travel / reimbursables: $7,500**", md)
        self.assertIn("**Total proposed investment: $67,500**", md)
        self.assertNotIn("Total professional fees: $67,500", md)
        self.assertIn("$60,000 in professional fees plus $7,500 in direct travel", md)
        self.assertNotIn("including $67,500 in direct travel", md)

    def test_syncs_stale_consultant_fee_terms(self) -> None:
        b = _budget(
            pricingTier="Average",
            directExpensesTotal=0,
            qualifyingLanguage=(
                "Investment Framing\n"
                "The proposed investment of $240,000 in agency Consultant Fees "
                "represents our compensation for campaign strategy.\n"
            ),
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    category="Discovery",
                    description="Phase 1 Discovery",
                    extended=57_754.01,
                    lineItemType="agency_fee",
                ),
            ],
        )
        cleaned = prepare_budget_for_client_display(b)
        self.assertNotIn("$240,000", cleaned.qualifying_language or "")
        self.assertIn("57,754.01", cleaned.qualifying_language or "")
        md = render_budget_markdown(b)
        self.assertNotIn("$240,000", md)
        self.assertIn("57,754.01", md)

    def test_syncs_stale_terms_and_strips_garbled_scope(self) -> None:
        b = _budget(
            pricingTier="Average",
            directExpensesTotal=7_500,
            scopeSummary=(
                "Four-phase KVCC plan. Total professional fees: $116,368.43. "
                "Estimated reimbursable travel: $7,500. Total estimated investment: "
                "$116,368.43. 43 ($116,368."
            ),
            qualifyingLanguage=(
                "Investment Framing\n"
                "The proposed investment of $73,500 in professional fees, plus an "
                "estimated $7,500 in reimbursable travel, reflects the full scope.\n"
            ),
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    category="Discovery",
                    description="Phase 1 Discovery",
                    extended=100_000,
                    lineItemType="agency_fee",
                ),
            ],
        )
        cleaned = prepare_budget_for_client_display(b)
        self.assertNotIn("43 ($116", cleaned.scope_summary or "")
        self.assertIn("$100,000 in professional fees plus $7,500", cleaned.scope_summary or "")
        self.assertIn("$100,000 in professional fees", cleaned.qualifying_language or "")
        self.assertNotIn("$73,500", cleaned.qualifying_language or "")

    def test_passthrough_not_counted_as_professional_fees_or_agency_revenue(self) -> None:
        b = _budget(
            pricingTier="Average",
            directExpensesTotal=0,
            scopeSummary="Campaign totaling $50,000.",
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    category="Fees",
                    description="Agency management fee",
                    extended=50_000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="L2",
                    category="Media",
                    description="Client media buy — paid social",
                    extended=200_000,
                    lineItemType="client_passthrough",
                ),
            ],
        )
        cleaned = prepare_budget_for_client_display(b)
        self.assertAlmostEqual(float(cleaned.agency_revenue_estimate or 0), 50_000.0, places=2)
        self.assertAlmostEqual(float(cleaned.lump_sum_total or 0), 50_000.0, places=2)
        self.assertAlmostEqual(float(cleaned.client_media_passthrough or 0), 200_000.0, places=2)
        self.assertAlmostEqual(float(cleaned.total_client_invoicing or 0), 250_000.0, places=2)

        md = render_budget_markdown(b)
        self.assertIn("**Professional fees: $50,000**", md)
        self.assertIn("**Client media pass-through (net): $200,000**", md)
        self.assertIn("**Total proposed investment: $250,000**", md)
        self.assertNotIn("**Professional fees: $250,000**", md)

    def test_phase_column_uses_structured_category(self) -> None:
        b = _budget(
            lineItems=[
                BudgetLineItem(
                    id="P3-4",
                    category="Implementation & Launch",
                    description="PR & earned media strategy",
                    extended=3500,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="P4-1",
                    category="Strategic Deliverables",
                    description="Implementation roadmap bundle",
                    extended=15000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="P5-1",
                    category="Ongoing Brand Stewardship",
                    description="Monthly social media management",
                    extended=12000,
                    lineItemType="agency_fee",
                ),
            ],
        )
        md = render_budget_markdown(b)
        self.assertIn("| Implementation & Launch |", md)
        self.assertIn("| Strategic Deliverables |", md)
        self.assertIn("| Ongoing Brand Stewardship |", md)
        # Must not invent Phase-3 labels from "social media" wording.
        self.assertNotIn("Phase 3 — Tactical Plan", md)

    def test_multi_phase_scope_does_not_paste_grand_total_into_each_phase(self) -> None:
        b = _budget(
            lumpSumTotal=151000,
            agencyRevenueEstimate=151000,
            scopeSummary=(
                "Discovery & Brand Foundation ($30,000), Brand Strategy & Identity ($40,000), "
                "Campaign Development ($50,000), Implementation & Launch ($20,000), and "
                "Ongoing Brand Stewardship ($11,000)."
            ),
            lineItems=[
                BudgetLineItem(
                    id="1",
                    category="Discovery & Brand Foundation",
                    description="Discovery",
                    extended=30000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="2",
                    category="Brand Strategy & Identity",
                    description="Strategy",
                    extended=40000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="3",
                    category="Campaign Development",
                    description="Campaign",
                    extended=50000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="4",
                    category="Implementation & Launch",
                    description="Launch",
                    extended=20000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="5",
                    category="Ongoing Brand Stewardship",
                    description="Stewardship",
                    extended=11000,
                    lineItemType="agency_fee",
                ),
            ],
        )
        cleaned = prepare_budget_for_client_display(b)
        scope = cleaned.scope_summary or ""
        # Must not turn every phase into $151,000.
        self.assertIn("Discovery & Brand Foundation ($30,000)", scope)
        self.assertIn("Brand Strategy & Identity ($40,000)", scope)
        self.assertIn("Campaign Development ($50,000)", scope)
        self.assertNotIn("Discovery & Brand Foundation ($151,000)", scope)
        self.assertNotIn("Brand Strategy & Identity ($151,000)", scope)
        self.assertIn("151,000", scope)  # investment total sentence
        md = render_budget_markdown(b)
        self.assertNotIn("Discovery & Brand Foundation ($151,000)", md)
        self.assertIn("| Ongoing Brand Stewardship |", md)


class QualifyingLanguageFormatTests(unittest.TestCase):
    def test_investment_mix_paragraph_becomes_table(self) -> None:
        from app.services.proposal_budget_content import (
            format_qualifying_language_for_client,
        )

        wall = (
            "Investment Framing\n"
            "Year 1 is a project-based fee schedule totaling $73,621 "
            "($33,081 in professional fees plus $40,540 in client media "
            "pass-through). Placed Media Buys account for 55% ($40,540) "
            "invoiced at net cost. Media Commission is 8% ($6,081) covering "
            "strategy, negotiation, trafficking, and optimization. Formative "
            "Research and Strategic Planning is 16% ($11,500). Creative "
            "Development is 9% ($6,500). Project Management is 10% ($7,500). "
            "Evaluation is 2% ($1,500). Combined Media Planning, Placement, "
            "and Optimization is 63% ($46,621).\n"
        )
        out = format_qualifying_language_for_client(wall)
        self.assertIn("### Investment Framing", out)
        self.assertIn("| Component | Share | Amount |", out)
        self.assertIn("| Placed Media Buys | 55% | $40,540 |", out)
        self.assertIn("| Media Commission | 8% | $6,081 |", out)
        self.assertIn("| Evaluation | 2% | $1,500 |", out)
        self.assertNotIn(
            "Placed Media Buys account for 55% ($40,540) invoiced at net cost. "
            "Media Commission is 8%",
            out,
        )
        self.assertLess(max(len(p) for p in out.split("\n")), 220)

    def test_stacked_bullets_and_pct_first_mix_become_table(self) -> None:
        from app.services.proposal_budget_content import (
            format_qualifying_language_for_client,
        )

        messy = (
            "Investment Framing\n"
            "- - - zö agency works on a project-based fee schedule.\n"
            "- - - The Year 1 investment of $68,200 reflects the scope as "
            "understood at proposal stage, with 59% allocated to placed media "
            "($40,540 client pass-through at net + $6,081 agency commission), "
            "26% to formative research and strategic planning ($11,500), "
            "10% to creative development ($6,500), 3% to project management "
            "($2,079), and 2% to evaluation ($1,500).\n"
            "Scope Protection\n"
            "- - - Pricing reflects the scope as understood at proposal stage.\n"
        )
        once = format_qualifying_language_for_client(messy)
        twice = format_qualifying_language_for_client(once)
        self.assertNotIn("- -", once)
        self.assertEqual(once, twice)
        self.assertIn("| Component | Share | Amount |", once)
        self.assertIn("| formative research and strategic planning | 26% | $11,500 |", once)
        self.assertIn("| creative development | 10% | $6,500 |", once)
        self.assertIn("### Scope Protection", once)
        self.assertIn("- Pricing reflects the scope", once)

    def test_ledger_mix_table_replaces_invented_percentages(self) -> None:
        from app.services.proposal_budget_content import (
            format_qualifying_language_for_client,
        )

        wall = (
            "Investment Framing\n"
            "The Year 1 investment of $68,200 reflects the scope, with 3% to "
            "project management ($2,079) and 2% to evaluation ($1,500).\n"
        )
        items = [
            BudgetLineItem(
                id="pm",
                category="Account & Project Management",
                description="Phase 5 Project Management",
                extended=7500,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="eval",
                category="Measurement & Reporting",
                description="Phase 6 Campaign Evaluation",
                extended=1500,
                lineItemType="agency_fee",
            ),
        ]
        out = format_qualifying_language_for_client(
            wall, line_items=items, total=9000
        )
        self.assertIn("| Account & Project Management | 83% | $7,500 |", out)
        self.assertNotIn("$2,079", out)
        self.assertIn("$7,500", out)

    def test_render_budget_markdown_uses_terms_table(self) -> None:
        b = _budget(
            lumpSumTotal=33081,
            agencyRevenueEstimate=33081,
            clientMediaPassthrough=40540,
            qualifyingLanguage=(
                "Investment Framing\n"
                "Year 1 totals $73,621. Placed Media Buys account for 55% "
                "($40,540) invoiced at net. Media Commission is 8% ($6,081)."
            ),
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    category="Fees",
                    description="Professional fees",
                    extended=33081,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="L2",
                    category="Media",
                    description="Client media",
                    extended=40540,
                    lineItemType="client_passthrough",
                ),
            ],
        )
        md = render_budget_markdown(b)
        self.assertIn("## Terms", md)
        self.assertIn("| Component | Share | Amount |", md)
        self.assertIn("| Fees | 45% | $33,081 |", md)
        self.assertIn("| Media | 55% | $40,540 |", md)


if __name__ == "__main__":
    unittest.main()
