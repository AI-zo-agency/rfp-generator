"""Reference phones must match KB evidence corpus digits."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_integrity_guards import (
    apply_reference_contact_evidence_guard,
    scrub_unverified_reference_phones,
)


class ReferencePhoneEvidenceGuardTests(unittest.TestCase):
    def test_scrub_replaces_phone_not_in_evidence(self) -> None:
        content = (
            "**Reference 1 — Deschutes Public Library**\n"
            "Contact: Chantal Strobel\n"
            "Phone: (541) 617-7050\n"
            "Email: cstrobel@deschuteslibrary.org\n"
        )
        evidence = (
            "Deschutes Public Library reference contact Chantal Strobel "
            "Phone: (541) 312-1032"
        )
        updated, logs = scrub_unverified_reference_phones(content, evidence_text=evidence)
        self.assertTrue(logs)
        self.assertNotIn("617-7050", updated)
        self.assertIn("[VERIFY: phone from KB reference doc]", updated)

    def test_scrub_keeps_phone_in_evidence(self) -> None:
        content = "Phone: (541) 312-1032\n"
        evidence = "Chantal Strobel (541) 312-1032"
        updated, logs = scrub_unverified_reference_phones(content, evidence_text=evidence)
        self.assertFalse(logs)
        self.assertIn("312-1032", updated)


if __name__ == "__main__":
    unittest.main()
