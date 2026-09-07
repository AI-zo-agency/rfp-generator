"""Sample PSA / exhibit contract clauses must never become proposal tabs."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_fulfill_rfp_structure import (
    RfpSectionSpec,
    _row_to_rfp_section_spec,
    _spec_is_sample_agreement_boilerplate,
    _title_is_non_deliverable,
    drop_non_deliverable_rfp_sections,
    ensure_missing_scored_section_stubs,
    outline_sections_from_rfp_specs,
)


class SampleAgreementBoilerplateFilterTests(unittest.TestCase):
    def test_construction_captions_clause_is_boilerplate(self) -> None:
        title = (
            "3.7.13 Construction; References; Captions. "
            "Since the Parties or their agents have"
        )
        self.assertTrue(_spec_is_sample_agreement_boilerplate(RfpSectionSpec(rfp_title=title)))
        self.assertTrue(_title_is_non_deliverable(title))

    def test_real_proposal_tabs_are_not_boilerplate(self) -> None:
        for title in (
            "Fee Proposal",
            "9. Insurance",
            "References",
            "Litigation",
            "Certification of Proposal",
            "Exceptions to RFP (if any)",
        ):
            self.assertFalse(
                _spec_is_sample_agreement_boilerplate(RfpSectionSpec(rfp_title=title)),
                msg=title,
            )

    def test_row_parser_rejects_boilerplate(self) -> None:
        spec = _row_to_rfp_section_spec(
            {
                "rfpTitle": (
                    "3.7.13 Construction; References; Captions. "
                    "Since the Parties or their agents have"
                )
            },
            mandated_submission_format=True,
        )
        self.assertIsNone(spec)

    def test_outline_from_specs_skips_boilerplate(self) -> None:
        specs = [
            RfpSectionSpec(rfp_title="Fee Proposal"),
            RfpSectionSpec(
                rfp_title=(
                    "3.7.13 Construction; References; Captions. "
                    "Since the Parties or their agents have"
                ),
            ),
            RfpSectionSpec(rfp_title="Litigation"),
        ]
        out = outline_sections_from_rfp_specs(specs, section_factory=lambda raw: raw)
        titles = [s["title"] for s in out]
        self.assertEqual(titles, ["Fee Proposal", "Litigation"])

    def test_drop_removes_boilerplate_even_with_prose(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="rfp-closing-references",
                    title=(
                        "3.7.13 Construction; References; Captions. "
                        "Since the Parties or their agents have"
                    ),
                    content=(
                        "We accept the Agreement's construction, references, "
                        "and captions terms as written. " * 8
                    ),
                    status="generated",
                ),
                ProposalSection(
                    id="rfp-structure-fee-proposal",
                    title="Fee Proposal",
                    content="Fee detail " * 40,
                    status="generated",
                ),
            ],
        )
        cleaned, logs = drop_non_deliverable_rfp_sections(draft)
        titles = [s.title for s in cleaned.sections]
        self.assertNotIn(
            "3.7.13 Construction; References; Captions. Since the Parties or their agents have",
            titles,
        )
        self.assertIn("Fee Proposal", titles)
        self.assertTrue(any("boilerplate" in x.casefold() or "non-deliverable" in x.casefold() for x in logs))


class InsuranceTocNotAbsorbedByStaticTests(unittest.TestCase):
    def test_insurance_toc_stub_despite_section_1_5(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="section-1-insurance",
                    title="1.5 — Insurance Information",
                    content="We maintain General Liability and Workers Comp. " * 5,
                    status="generated",
                ),
                ProposalSection(
                    id="rfp-structure-fee-proposal",
                    title="Fee Proposal",
                    content="fees " * 50,
                    status="generated",
                ),
                ProposalSection(
                    id="rfp-structure-litigation",
                    title="Litigation",
                    content="litigation " * 50,
                    status="generated",
                ),
            ],
        )
        specs = [
            RfpSectionSpec(
                rfp_title="Insurance",
                mandated_submission_format=True,
                instructions="Provide evidence of insurance as required by the RFP.",
            ),
        ]
        updated, logs = ensure_missing_scored_section_stubs(draft, specs)
        titles = [s.title for s in updated.sections]
        self.assertIn("Insurance", titles)
        self.assertTrue(any(s.id == "rfp-structure-insurance" for s in updated.sections))


if __name__ == "__main__":
    unittest.main()
