"""Zero-fabrication guard suite."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget, ProposalDraft, ProposalSection
from app.services.proposal_zero_fabrication import (
    apply_zero_fabrication_guards,
    detect_contradictory_phase_tables,
)


def _draft_with_tables() -> ProposalDraft:
    table_a = (
        "### Disbursement\n\n| Phase | Amount |\n| --- | ---: |\n"
        "| Discovery | $8,000 |\n| Launch | $42,000 |\n"
    )
    table_b = (
        "### Fee Detail\n\n| Phase | Amount |\n| --- | ---: |\n"
        "| Discovery | $6,500 |\n| Launch | $43,500 |\n"
    )
    return ProposalDraft(
        rfpId="rfp-test",
        updatedAt="2026-01-01T00:00:00Z",
        sections=[
            ProposalSection(id="a", title="Disbursement", content=table_a),
            ProposalSection(id="b", title="Fee Detail", content=table_b),
        ],
    )


def _budget() -> ProposalBudget:
    return ProposalBudget(
        rfpId="rfp-test",
        updatedAt="2026-01-01T00:00:00Z",
        lineItems=[
            BudgetLineItem(
                id="L1",
                category="Discovery",
                description="Discovery",
                extended=6500.0,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="L2",
                category="Launch",
                description="Launch",
                extended=43500.0,
                lineItemType="agency_fee",
            ),
        ],
        totalClientInvoicing=50000.0,
    )


class ZeroFabricationGuardTests(unittest.TestCase):
    def test_detect_contradictory_phase_tables(self) -> None:
        conflicts = detect_contradictory_phase_tables(_draft_with_tables())
        self.assertTrue(conflicts)

    def test_apply_leaves_legacy_budget_tables_as_saved(self) -> None:
        # Pre-v2 budget (no pricing plan) is frozen: no sibling phase-table sync.
        original = _draft_with_tables()
        updated, _report = apply_zero_fabrication_guards(
            original,
            budget=_budget(),
            label="test",
        )
        bodies = " ".join(s.content or "" for s in updated.sections)
        self.assertIn("$8,000", bodies)
        self.assertIn("$6,500", bodies)

    def test_scrubs_fabricated_personnel_and_cert_claims(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-test",
            updatedAt="2026-01-01T00:00:00Z",
            sections=[
                ProposalSection(
                    id="team",
                    title="Personnel",
                    content=(
                        "Brittany Frazier serves as Creative Director.\n\n"
                        "zö is certified MBE/DBE for this procurement."
                    ),
                ),
            ],
        )
        updated, report = apply_zero_fabrication_guards(draft, label="test")
        body = updated.sections[0].content or ""
        self.assertNotIn("Brittany Frazier", body)
        self.assertIn("MANUAL FILL", body)
        self.assertNotIn("MBE/DBE", body)
        joined = " ".join(report.logs)
        self.assertIn("personnel", joined.casefold())

    def test_roster_replaces_murilo_with_marcelle(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-test",
            updatedAt="2026-01-01T00:00:00Z",
            sections=[
                ProposalSection(
                    id="org",
                    title="1.2 — Organizational Structure",
                    content="Kelvin Kiruthu Senior Graphic Designer Murilo Mendes Graphic Designer",
                ),
            ],
        )
        updated, report = apply_zero_fabrication_guards(draft, label="test")
        body = updated.sections[0].content or ""
        self.assertNotIn("Murilo Mendes", body)
        self.assertIn("Marcelle Benevides", body)
        self.assertTrue(any("roster" in line.casefold() for line in report.logs))

    def test_checklist_cannot_claim_missing_sidebar_tab(self) -> None:
        """Generate / Complete Scan / chat persist all run ZF — Location ∉ sidebar → No."""
        draft = ProposalDraft(
            rfpId="rfp-test",
            updatedAt="2026-01-01T00:00:00Z",
            sections=[
                ProposalSection(
                    id="check",
                    title="Submittal Checklist",
                    content=(
                        "| Submittal Item | Included | Location |\n"
                        "| --- | --- | --- |\n"
                        "| Cover Letter | Yes | Cover Letter tab |\n"
                        "| Executive Summary | Yes | Executive Summary tab |\n"
                    ),
                ),
                ProposalSection(id="cover", title="Cover Letter", content="Dear client."),
            ],
        )
        updated, report = apply_zero_fabrication_guards(draft, label="generate")
        body = next(s.content or "" for s in updated.sections if s.id == "check")
        self.assertIn("Executive Summary | No |", body)
        self.assertIn("MANUAL FILL", body)
        self.assertTrue(any("cross-ref" in line for line in report.logs))


if __name__ == "__main__":
    unittest.main()
