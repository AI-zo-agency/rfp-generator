"""Complete Scan must gate unverified insurance Compliant certifications."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_case_study_eligibility import (
    is_eligible_section3_case_study_title,
)
from app.services.proposal_scan_insurance_certification import (
    build_insurance_inventory,
    gate_draft_insurance_certifications,
    is_insurance_certification_section,
)


def _draft(*sections: ProposalSection) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-ins-test",
        sections=list(sections),
        updatedAt="2026-01-01T00:00:00Z",
    )


class InsuranceCertificationGateTests(unittest.TestCase):
    def test_exception_form_auto_and_2m_gated_when_15_lacks_them(self) -> None:
        draft = _draft(
            ProposalSection(
                id="section-1-insurance",
                title="1.5 — Insurance Information",
                content=(
                    "Our current policies include General Liability, Professional "
                    "Liability, and Workers Compensation insurance."
                ),
            ),
            ProposalSection(
                id="rfp-exception-form",
                title="15 — Exception Form",
                content=(
                    "The Vendor currently maintains insurance coverage meeting or "
                    "exceeding IEUA's stated requirements.\n\n"
                    "| Coverage | RFP Minimum | Status |\n"
                    "| --- | --- | --- |\n"
                    "| General Liability | $1M / $2M aggregate | Compliant |\n"
                    "| Automobile Liability | $1M | Compliant |\n"
                    "| Professional Liability | $1M | Compliant |\n"
                    "| Workers Compensation | Statutory | Compliant |\n\n"
                    "No exceptions, clarifications, or alternative language are requested."
                ),
            ),
        )
        updated, logs, human = gate_draft_insurance_certifications(draft)
        self.assertTrue(logs)
        self.assertTrue(human)
        exc = next(s for s in updated.sections if s.id == "rfp-exception-form")
        body = exc.content or ""
        self.assertIn("[MANUAL FILL:", body)
        self.assertNotRegex(body, r"Automobile Liability \| \$1M \| Compliant")
        self.assertNotRegex(body, r"General Liability \| \$1M / \$2M aggregate \| Compliant")
        # meets-or-exceeds assertion must be gated
        self.assertNotRegex(
            body,
            r"maintains insurance coverage meeting or exceeding",
            msg="must not leave unverified meets-or-exceeds certification",
        )
        inv = build_insurance_inventory(draft)
        self.assertIn("general liability", inv.categories)
        self.assertNotIn("automobile liability", inv.categories)

    def test_section_15_inventory_detection(self) -> None:
        section = ProposalSection(
            id="x",
            title="Exception Form",
            content="No exceptions are requested. Insurance rows marked Compliant.",
        )
        self.assertTrue(is_insurance_certification_section(section))
        self.assertFalse(
            is_insurance_certification_section(
                ProposalSection(
                    id="section-1-insurance",
                    title="1.5 — Insurance Information",
                    content="We carry General Liability.",
                )
            )
        )

    def test_keeps_compliant_when_15_lists_coverage_and_matching_dollars(self) -> None:
        draft = _draft(
            ProposalSection(
                id="section-1-insurance",
                title="1.5 — Insurance Information",
                content=(
                    "General Liability $1,000,000 / $1,000,000 aggregate. "
                    "Professional Liability $1,000,000. Workers Compensation as required."
                ),
            ),
            ProposalSection(
                id="exc",
                title="Exceptions",
                content=(
                    "| Coverage | Status |\n"
                    "| --- | --- |\n"
                    "| General Liability $1,000,000 | Compliant |\n"
                    "| Professional Liability $1,000,000 | Compliant |\n"
                ),
            ),
        )
        updated, logs, _human = gate_draft_insurance_certifications(draft)
        body = next(s for s in updated.sections if s.id == "exc").content or ""
        # GL $1M is in inventory — may keep Compliant for that row
        self.assertIn("Compliant", body)
        # No auto row → no auto-related log required
        self.assertFalse(any("automobile" in line.casefold() for line in logs))


class CaseStudyEligibilityCivicTests(unittest.TestCase):
    def test_private_medical_blocked_on_utility_rfp(self) -> None:
        self.assertFalse(
            is_eligible_section3_case_study_title(
                "3.1 — Bend Gynecology Brand Refresh",
                rfp_title="IEUA Website Redesign",
                rfp_sector="Municipal Utility",
            )
        )

    def test_municipal_campaign_still_allowed(self) -> None:
        self.assertTrue(
            is_eligible_section3_case_study_title(
                "3.2 — City of Umatilla Festival",
                rfp_title="IEUA Website Redesign",
                rfp_sector="Municipal Utility",
            )
        )


if __name__ == "__main__":
    unittest.main()
