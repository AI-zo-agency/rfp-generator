"""Client-facing scrub: evidence lists + pricing flags must not ship."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_content import (
    insert_budget_table_into_section,
    render_embedded_budget_table_markdown,
)
from app.services.proposal_budget_playbook import user_asks_insert_budget_table
from app.services.proposal_manuscript import (
    scrub_client_facing_section_artifacts,
    strip_evidence_citation_markers,
)


class StripEvidenceMarkersTests(unittest.TestCase):
    def test_strips_comma_list_and_references_line(self) -> None:
        raw = (
            "We deliver on-budget task orders.\n\n"
            "**References:** [E12, E13, E14, E15, E16, E17, E18, E19, E20, E21, "
            "E22, E23, E24, E25, E26, E27]\n"
        )
        out = strip_evidence_citation_markers(raw)
        self.assertNotIn("E12", out)
        self.assertNotIn("References", out)
        self.assertIn("on-budget task orders", out)

    def test_scrub_removes_pricing_flags(self) -> None:
        raw = (
            "[PRICING FLAG: Cost weight → force Low tier]\n"
            "[PRICING FLAG: PM ratio 12%]\n\n"
            "We work within the County budget.\n"
        )
        out = scrub_client_facing_section_artifacts(raw)
        self.assertNotIn("PRICING FLAG", out)
        self.assertIn("County budget", out)


class EmbeddedBudgetTableTests(unittest.TestCase):
    def _budget(self) -> ProposalBudget:
        return ProposalBudget(
            rfpId="r1",
            updatedAt="2026-07-27T00:00:00+00:00",
            pricingTier="Low",
            lineItems=[
                BudgetLineItem(
                    id="1",
                    description="Discovery audit",
                    category="Discovery",
                    quantity=1,
                    unit="project",
                    rate=10000,
                    extended=10000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="2",
                    description="Campaign execution",
                    category="Execution",
                    quantity=1,
                    unit="project",
                    rate=25000,
                    extended=25000,
                    lineItemType="agency_fee",
                ),
            ],
            agencyFeeSubtotal=35000,
            agencyRevenueEstimate=35000,
            totalClientInvoicing=35000,
        )

    def test_embedded_table_uses_bold_labels_and_sums(self) -> None:
        md = render_embedded_budget_table_markdown(self._budget())
        self.assertIn("**Professional fees:**", md)
        self.assertIn("**Total proposed investment:**", md)
        self.assertIn("$35,000", md)
        self.assertNotIn("PRICING FLAG", md)
        self.assertNotIn("[E", md)

    def test_insert_replaces_flags_and_scrubs_e_markers(self) -> None:
        body = (
            "## BUDGETS (SECTION II.A.2)\n\n"
            "We work within the County budget.\n\n"
            "[PRICING FLAG: force Low tier]\n"
            "[PRICING FLAG: PM ratio high]\n\n"
            "## Proposed Investment\n\n"
            "**Total proposed investment: $2,550**\n\n"
            "**References:** [E12, E13, E14]\n"
        )
        table = render_embedded_budget_table_markdown(self._budget())
        out, action = insert_budget_table_into_section(body, table)
        self.assertIn(action, {"inserted", "replaced"})
        self.assertIn("We work within the County budget", out)
        self.assertNotIn("PRICING FLAG", out)
        self.assertNotIn("E12", out)
        self.assertNotIn("$2,550", out)
        self.assertIn("$35,000", out)
        self.assertIn("**Total proposed investment:**", out)

    def test_fix_budget_ask_detected(self) -> None:
        self.assertTrue(
            user_asks_insert_budget_table(
                "dont give this E and all and simply cant do calculations "
                "use proper bold letter for this budget one"
            )
        )


if __name__ == "__main__":
    unittest.main()
