"""Fabricated personnel scrub on manuscript drafts."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.evidence_trust.personnel_grounding import (
    scrub_fabricated_personnel_from_draft,
)
from app.services.proposal_fulfill_rfp_repairs import apply_deterministic_roster_fixes


class FabricatedPersonnelScrubTests(unittest.TestCase):
    def test_removes_known_fabricated_names(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="bios",
                    title="Team",
                    content="Creative Director: Brittany Frazier. PM: Drew Stone.",
                )
            ],
        )
        updated, logs = scrub_fabricated_personnel_from_draft(draft)
        body = updated.sections[0].content or ""
        self.assertNotIn("Brittany Frazier", body)
        self.assertNotIn("Drew Stone", body)
        self.assertTrue(logs)

    def test_removes_priyal_solanki_blocklist(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="team-bios",
                    title="§26 — Team Bios",
                    content=(
                        "### Priyal Solanki, Digital Project Manager\n\n"
                        "Priyal Solanki brings strategic digital project management expertise."
                    ),
                )
            ],
        )
        updated, logs = scrub_fabricated_personnel_from_draft(draft)
        body = updated.sections[0].content or ""
        self.assertNotIn("Priyal Solanki", body)
        self.assertTrue(logs)

    def test_removes_murilo_mendes_keeps_marcelle(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="section-1-2",
                    title="1.2 — Organizational Structure",
                    content=(
                        "Kelvin Kiruthu Senior Graphic Designer "
                        "Murilo Mendes Graphic Designer "
                        "Miguel Perez Production Designer "
                        "Marcelle Benevides Graphic Designer"
                    ),
                )
            ],
        )
        updated, logs = scrub_fabricated_personnel_from_draft(draft)
        body = updated.sections[0].content or ""
        self.assertNotIn("Murilo Mendes", body)
        self.assertIn("Marcelle Benevides", body)
        self.assertIn("Kelvin Kiruthu", body)
        self.assertTrue(logs)

    def test_removes_retired_ron_comer_from_content_and_title(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="section-2-bio-ron",
                    title="2.3 — Ron Comer",
                    content=(
                        "Ron Comer serves as your primary contract administrator "
                        "and Senior Account Manager."
                    ),
                ),
                ProposalSection(
                    id="section-25",
                    title="Respondent Contract Administrator",
                    content="Primary contact: Ron Comer, Senior Account Manager.",
                ),
            ],
        )
        updated, logs = scrub_fabricated_personnel_from_draft(draft)
        blob = "\n".join(
            f"{s.title}\n{s.content}" for s in (updated.sections or [])
        )
        self.assertNotIn("Ron Comer", blob)
        self.assertIn("retired", " ".join(logs).casefold())
        self.assertIn("assign current staff", (updated.sections[0].content or "").casefold())

    def test_removes_org_chart_accounting_coach_inventions(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="section-1-org-structure",
                    title="1.2 — Organizational Structure",
                    content=(
                        "| Name | Role |\n"
                        "| --- | --- |\n"
                        "| Sonja Anderson | Agency Director |\n"
                        "| Kelly Vlach | Accounting (CPA) |\n"
                        "| Katie Post | Leadership Coach |\n"
                        "| Dave Luke | Leadership Coach |\n"
                    ),
                )
            ],
        )
        updated, logs = scrub_fabricated_personnel_from_draft(draft)
        body = updated.sections[0].content or ""
        self.assertNotIn("Kelly Vlach", body)
        self.assertNotIn("Katie Post", body)
        self.assertNotIn("Dave Luke", body)
        self.assertIn("Sonja Anderson", body)
        self.assertTrue(logs)


class UnverifiedOrgChartRosterTests(unittest.IsolatedAsyncioTestCase):
    async def test_org_chart_only_names_are_not_self_verifying(self) -> None:
        """Invented org seats must scrub even when absent from the blocklist."""
        from unittest.mock import AsyncMock, patch

        from app.services.evidence_trust.personnel_grounding import (
            scrub_unverified_personnel_from_draft,
        )

        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="section-1-org-structure",
                    title="Organizational Structure",
                    content=(
                        "| Name | Role |\n"
                        "| --- | --- |\n"
                        "| Sonja Anderson | Agency Director |\n"
                        "| Jane Invented | Accounting (CPA) |\n"
                        "| Ella Lindau | Operations Director |\n"
                    ),
                )
            ],
        )
        with (
            patch(
                "app.services.proposal_knowledge_base_tools.fetch_master_team_roster",
                new=AsyncMock(return_value=("", [])),
            ),
            patch(
                "app.services.proposal_sections_graph._find_member_bio_document",
                new=AsyncMock(return_value=None),
            ),
        ):
            updated, logs = await scrub_unverified_personnel_from_draft(draft)
        body = updated.sections[0].content or ""
        self.assertNotIn("Jane Invented", body)
        self.assertIn("Sonja Anderson", body)
        self.assertIn("Ella Lindau", body)
        self.assertTrue(any("Jane Invented" in line for line in logs))

