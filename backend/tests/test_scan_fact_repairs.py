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


if __name__ == "__main__":
    unittest.main()
