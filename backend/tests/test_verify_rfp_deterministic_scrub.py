"""Deterministic RFP-aware VERIFY strip + anti-fabrication scrub guards."""

from __future__ import annotations

import re
import unittest

from app.services.proposal_verify_optional_scrub import (
    scrub_result_introduces_fabrication,
    strip_verify_tags_not_required_by_rfp,
)


class DeterministicVerifyStripTests(unittest.TestCase):
    def test_removes_optional_partner_verify_when_rfp_silent(self) -> None:
        body = (
            "Delivery partners support the build.\n"
            "[VERIFY: backup mobile partner name]\n"
            "WCAG testing follows."
        )
        rfp = "Vendor shall describe technical approach and timeline. Subcontracting optional."
        cleaned, removed = strip_verify_tags_not_required_by_rfp(body, rfp)
        self.assertGreaterEqual(removed, 1)
        self.assertNotIn("[VERIFY", cleaned)
        self.assertIn("WCAG testing", cleaned)

    def test_keeps_fein_verify_when_rfp_requires_tax_id(self) -> None:
        body = "Legal entity FEIN: [VERIFY: FEIN / federal tax ID]"
        rfp = "Offeror shall provide FEIN and federal employer identification number."
        cleaned, removed = strip_verify_tags_not_required_by_rfp(body, rfp)
        self.assertEqual(removed, 0)
        self.assertIn("[VERIFY: FEIN", cleaned)

    def test_keeps_locked_legal_everify_tag(self) -> None:
        body = "Compliance: [VERIFY: E-Verify enrollment confirmation — Sonja]"
        rfp = "No mention of employment verification systems here."
        cleaned, removed = strip_verify_tags_not_required_by_rfp(body, rfp)
        self.assertEqual(removed, 0)
        self.assertIn("E-Verify", cleaned)

    def test_removes_ask_whose_topic_never_appears_in_rfp(self) -> None:
        body = "Reporting uses [VERIFY: attached KPI dashboard screenshot URL]."
        rfp = "Submit cover letter, qualifications, and cost proposal only."
        cleaned, removed = strip_verify_tags_not_required_by_rfp(body, rfp)
        self.assertGreaterEqual(removed, 1)
        self.assertNotIn("[VERIFY", cleaned)


class ScrubAntiFabricationTests(unittest.TestCase):
    def test_rejects_new_phone_not_in_sources(self) -> None:
        original = "Contact: [VERIFY: reference phone]"
        updated = "Contact: (555) 123-4567"
        self.assertTrue(
            scrub_result_introduces_fabrication(
                original,
                updated,
                rfp_text="Provide three references.",
                kb_text="",
            )
        )

    def test_allows_phone_present_in_kb(self) -> None:
        original = "Contact: [VERIFY: reference phone]"
        updated = "Contact: (555) 123-4567"
        self.assertFalse(
            scrub_result_introduces_fabrication(
                original,
                updated,
                rfp_text="Provide three references.",
                kb_text="Jane Doe phone (555) 123-4567 email jane@example.com",
            )
        )


if __name__ == "__main__":
    unittest.main()
