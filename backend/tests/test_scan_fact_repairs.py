"""Scan fact repair deterministic fixes."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_scan_fact_repairs import (
    repair_cover_letter_facts,
    repair_sole_proprietor_language,
    repair_timeline_week_totals,
    scrub_leaked_system_fragments,
    scrub_unverified_banking_claims,
)


class ScanFactRepairTests(unittest.TestCase):
    def test_scrub_leaked_clientlist_fragment(self) -> None:
        text = (
            "s — no verified ClientList/KB match for reference contacts; "
            "do not invent names or emails — provide verified contacts only]\n\n"
            "Ownership: S-Corp/LLC\n"
        )
        cleaned, logs = scrub_leaked_system_fragments(text)
        self.assertTrue(logs)
        self.assertNotIn("ClientList", cleaned)
        self.assertIn("S-Corp", cleaned)

    def test_sole_proprietor_replaced(self) -> None:
        text = "Ownership: Sole proprietor Sonja Anderson."
        fixed, logs = repair_sole_proprietor_language(text)
        self.assertTrue(logs)
        self.assertNotIn("Sole proprietor", fixed)
        self.assertIn("S-Corp/LLC", fixed)

    def test_columbia_bank_scrubbed_without_evidence(self) -> None:
        text = "Primary banking relationship established with Columbia Bank.\n\nOther facts."
        cleaned, logs = scrub_unverified_banking_claims(text, evidence_text="")
        self.assertTrue(logs)
        self.assertNotIn("Columbia Bank", cleaned)

    def test_timeline_week_header_synced(self) -> None:
        text = (
            "Eight milestone-gated phases over 38 weeks:\n\n"
            "| Phase | Weeks |\n| --- | --- |\n"
            "| Discovery | 6 weeks |\n| Build | 30 weeks |\n"
        )
        fixed, logs = repair_timeline_week_totals(text)
        self.assertTrue(logs)
        self.assertIn("36 weeks", fixed)
        self.assertNotIn("38 weeks", fixed)

    def test_cover_letter_key_personnel_and_email(self) -> None:
        text = (
            "Primary Contact: Ron Comer — sonja@zo.agency\n"
            "Key Personnel: Ella Lindau (Principal & Creative Director, 15+ years), , Ron Comer\n"
        )
        org = {
            "ella lindau": "Account and Operations Manager",
            "ron comer": "Principal & Creative Director",
        }
        bios = {
            "ella lindau": "| Marketing | 5 years |",
            "ron comer": "| Leadership | 35 years |",
        }
        fixed, logs = repair_cover_letter_facts(text, org_roles=org, bio_by_name=bios)
        self.assertTrue(logs)
        self.assertIn("ron@zo.agency", fixed)
        self.assertIn("Account and Operations Manager", fixed)
        self.assertNotIn("Principal & Creative Director, 15", fixed)


class RebuildBioStubTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_resume_becomes_designer_pdf_stub(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.models.proposal import ProposalDraft, ProposalSection
        from app.services.proposal_bio_stub import is_bio_pdf_designer_note
        from app.services import proposal_sections_graph as sections_graph
        from app.services.proposal_scan_fact_repairs import rebuild_team_bios_from_kb

        draft = ProposalDraft(
            rfpId="rfp-test",
            updatedAt="2026-08-12T00:00:00Z",
            sections=[
                ProposalSection(
                    id="section-2-bio-ron-comer",
                    title="2.3 — Ron Comer",
                    content=(
                        "### Ron Comer — senior account manager\n\n"
                        "Ron Comer has 40 years of experience in advertising.\n\n"
                        "**Years of Experience**\n"
                        "| Area of Expertise | Years |\n"
                        "| --- | --- |\n"
                        "| Traditional Media | 20 years |\n"
                    ),
                )
            ],
        )
        with patch.object(
            sections_graph,
            "_fetch_member_bio_kb",
            new=AsyncMock(
                return_value=(
                    "Approved 04_Bio content for Ron Comer. " * 20,
                    ["04_Bio_RonComer.pdf"],
                )
            ),
        ):
            updated, logs = await rebuild_team_bios_from_kb(draft)
        body = updated.sections[0].content or ""
        self.assertTrue(logs)
        self.assertIn("Insert approved bio PDF — 04_Bio_RonComer.pdf", body)
        self.assertNotIn("**Years of Experience**", body)
        self.assertNotIn("40 years of experience", body)
        self.assertTrue(is_bio_pdf_designer_note(body))


if __name__ == "__main__":
    unittest.main()
