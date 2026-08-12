"""Complete Scan — principle-based evidence grounding (not edge-case lists)."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalSection
from app.services.evidence_trust.legal_attestation_gate import (
    gate_section_legal_attestations,
    rfp_documents_likely_incomplete,
)
from app.services.proposal_scan_compliance_fabrication import (
    bio_narrative_ungrounded,
    repair_bio_role_from_org_chart,
    scrub_ungrounded_named_entities,
)


class ProcurementAttestationTests(unittest.TestCase):
    def test_cdta_style_registration_gated_via_legal_attestation(self) -> None:
        section = ProposalSection(
            id="submission-11",
            title="PDF format proposal submission",
            content=(
                "zö agency completed online vendor registration at www.cdta.org on "
                "August 8, 2026 and downloaded the complete procurement documents as required. "
                "Registration confirmation will be included as Attachment A."
            ),
        )
        notice = "Contract Reporter — No documents have been uploaded to this ad."
        updated, report = gate_section_legal_attestations(
            section,
            evidence_text="",
            rfp_context=notice,
        )
        self.assertGreater(report.procurement_flags, 0)
        self.assertIn("[MANUAL FILL:", updated.content or "")
        self.assertNotIn("Attachment A", updated.content or "")

    def test_notice_only_detector(self) -> None:
        self.assertTrue(
            rfp_documents_likely_incomplete(
                "Contract Reporter notice. No documents have been uploaded to this ad."
            )
        )
        self.assertFalse(
            rfp_documents_likely_incomplete("x" * 3000 + " Section 1 Scope of Work ")
        )


class BioGroundingTests(unittest.TestCase):
    def test_bio_role_aligned_to_org_chart(self) -> None:
        bio = (
            "### Sonja Anderson\n"
            "**Role on this engagement:** Creative Director\n\n"
            "Sonja brings over 15 years of brand strategy."
        )
        fixed, logs = repair_bio_role_from_org_chart(
            bio,
            member_name="Sonja Anderson",
            org_roles={"sonja anderson": "Agency Director"},
        )
        self.assertTrue(logs)
        self.assertIn("Agency Director", fixed)
        self.assertNotIn("Creative Director", fixed)

    def test_ungrounded_bio_detected_by_kb_overlap(self) -> None:
        bio = (
            "### Sonja Anderson\n"
            "**Role on this engagement:** Agency Director\n\n"
            "Sonja has led branding initiatives for multi-county transit authorities "
            "and bike share programs across the Northeast with deep mobility expertise."
        )
        kb = (
            "Sonja Anderson — Agency Director. Key accounts: Deschutes Brewery, "
            "University of Idaho, Hampton Lumber, San Francisco Travel."
        )
        self.assertTrue(bio_narrative_ungrounded(bio, kb))

    def test_kb_grounded_bio_passes(self) -> None:
        bio = (
            "### Sonja Anderson\n"
            "**Role on this engagement:** Agency Director\n\n"
            "Key accounts include Deschutes Brewery, University of Idaho, "
            "Hampton Lumber, and San Francisco Travel."
        )
        kb = (
            "Sonja Anderson Agency Director. Deschutes Brewery University of Idaho "
            "Hampton Lumber San Francisco Travel marketing leadership."
        )
        self.assertFalse(bio_narrative_ungrounded(bio, kb))


class NamedEntityGroundingTests(unittest.TestCase):
    def test_carrier_scrubbed_without_evidence(self) -> None:
        text = "Commercial General Liability: $1,000,000 (Next Insurance)"
        fixed, logs = scrub_ungrounded_named_entities(text, evidence_text="")
        self.assertTrue(logs)
        self.assertNotIn("Next Insurance", fixed)
        self.assertIn("[VERIFY:", fixed)

    def test_any_carrier_name_pattern(self) -> None:
        text = "Coverage through Acme Mutual Insurance per certificate."
        fixed, logs = scrub_ungrounded_named_entities(text, evidence_text="")
        self.assertTrue(logs)
        self.assertIn("[VERIFY:", fixed)

    def test_entity_kept_when_in_evidence(self) -> None:
        text = "Commercial General Liability: $1,000,000 (Next Insurance)"
        fixed, logs = scrub_ungrounded_named_entities(
            text,
            evidence_text="Certificate from Next Insurance workers comp",
        )
        self.assertFalse(logs)
        self.assertIn("Next Insurance", fixed)


if __name__ == "__main__":
    unittest.main()
