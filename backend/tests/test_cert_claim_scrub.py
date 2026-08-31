"""Deterministic cert fabrication scrub."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_cert_claim_scrub import (
    apply_cert_claim_scrub_to_draft,
    scrub_section_cert_claims,
)


class CertClaimScrubTests(unittest.TestCase):
    def test_removes_spotify_api_cert(self) -> None:
        section = ProposalSection(
            id="pref",
            title="Preferred Qualifications",
            content=(
                "Credentials | Google Ads Certification, Meta Ads Certification, "
                "Spotify API certification with WBENC certification"
            ),
            status="generated",
        )
        scrubbed, logs = scrub_section_cert_claims(section)
        self.assertTrue(logs)
        self.assertNotIn("Spotify", scrubbed.content or "")
        self.assertIn("WBENC", scrubbed.content or "")

    def test_rewrites_agency_overclaim(self) -> None:
        section = ProposalSection(
            id="min",
            title="Minimum Qualifications",
            content="Our certified team holds Google Ads Certification for paid search.",
            status="generated",
        )
        scrubbed, logs = scrub_section_cert_claims(section)
        self.assertTrue(logs)
        self.assertNotRegex(scrubbed.content or "", r"Our certified team holds Google Ads")
        self.assertIn("individual platform certifications", scrubbed.content or "")

    def test_rewrites_sba_status_overclaim(self) -> None:
        section = ProposalSection(
            id="certs",
            title="1.4 Certifications",
            content="WOSB certification confirms SBA status for federal set-asides.",
            status="generated",
        )
        scrubbed, logs = scrub_section_cert_claims(section)
        self.assertTrue(logs)
        self.assertNotIn("confirms SBA status", scrubbed.content or "")
        self.assertIn("WBENC and WOSB", scrubbed.content or "")

    def test_draft_apply(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="s1",
                    title="Matrix",
                    content="Holds Spotify API Certification as proof.",
                    status="generated",
                )
            ],
        )
        updated, logs = apply_cert_claim_scrub_to_draft(draft)
        self.assertTrue(logs)
        self.assertNotIn("Spotify", updated.sections[0].content or "")

    def test_removes_mbe_dbe_and_unverified_marketing_certs(self) -> None:
        section = ProposalSection(
            id="forms",
            title="Required Forms & Attachments",
            content=(
                "| **Certifications** | Verified | "
                "WBENC WBE-2401236; SBA WOSB; MBE certified; DBE; "
                "1% for the Planet; B Corporation; LinkedIn Gold-Certified |"
            ),
            status="generated",
        )
        scrubbed, logs = scrub_section_cert_claims(section)
        body = scrubbed.content or ""
        self.assertTrue(logs)
        self.assertNotIn("MBE", body)
        self.assertNotIn("DBE", body)
        self.assertNotIn("1% for the Planet", body)
        self.assertNotIn("LinkedIn Gold", body)
        self.assertIn("WBENC WBE-2401236", body)
        self.assertIn("WOSB", body)
        self.assertNotIn("B Corporation", body)

    def test_removes_b_corp_member_and_attachment_claims(self) -> None:
        section = ProposalSection(
            id="portal",
            title="5.9 OpenGov Portal",
            content=(
                "| Business Classification | B Corporation member |\n"
                "| Attachments | B Corporation Certification — already submitted |\n"
                "We hold B-Corp certification alongside WBENC.\n"
            ),
            status="generated",
        )
        scrubbed, logs = scrub_section_cert_claims(section)
        body = scrubbed.content or ""
        self.assertTrue(any("b_corp" in line for line in logs))
        self.assertNotIn("B Corporation", body)
        self.assertNotIn("B-Corp", body)
        self.assertIn("WBENC", body)

    def test_cleans_orphaned_and_after_list_removal(self) -> None:
        section = ProposalSection(
            id="forms",
            title="Certifications",
            content=(
                "We hold B Corporation certification, 1% for the Planet membership, "
                "and LinkedIn Gold-Certified status."
            ),
            status="generated",
        )
        scrubbed, logs = scrub_section_cert_claims(section)
        self.assertTrue(logs)
        body = scrubbed.content or ""
        self.assertNotIn("B Corporation", body)
        self.assertNotIn("1% for the Planet", body)
        self.assertNotIn("LinkedIn Gold", body)
        self.assertNotIn(", and .", body)

    def test_user_asks_cert_claim_scrub(self) -> None:
        from app.services.proposal_cert_claim_scrub import user_asks_cert_claim_scrub

        self.assertTrue(
            user_asks_cert_claim_scrub(
                "Remove three false certifications (MBE, WBE, DBE)."
            )
        )
        # No retain-only synonym regex — intent must say remove/false cert.
        self.assertFalse(
            user_asks_cert_claim_scrub("retain only WBENC/WOSB and B Corp.")
        )
        self.assertFalse(user_asks_cert_claim_scrub("What certifications do we have?"))

    def test_cert_scrub_does_not_flatten_multiline_table(self) -> None:
        table = (
            "| Phase | Deliverable | Amount |\n"
            "| --- | --- | ---: |\n"
            "| Phase 1 | Discovery | $6,639.60 |\n"
            "| Phase 2 | Strategy | $4,742.57 |\n"
        )
        section = ProposalSection(
            id="price",
            title="Price",
            content=table,
            status="generated",
        )
        scrubbed, _logs = scrub_section_cert_claims(section)
        body = scrubbed.content or ""
        self.assertIn("| Phase | Deliverable | Amount |\n", body)
        self.assertIn("| Phase 1 | Discovery | $6,639.60 |\n", body)
        self.assertNotIn("| Amount | | --- |", body)


if __name__ == "__main__":
    unittest.main()
