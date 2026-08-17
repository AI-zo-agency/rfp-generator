"""RFP-demanded sections must survive lean filter + structure recovery."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_fulfill_rfp_structure import (
    RfpSectionSpec,
    ensure_company_block_wrapper_heading,
    ensure_missing_scored_section_stubs,
    order_draft_to_rfp_sequence,
)
from app.services.proposal_intelligence.schemas import EvaluationCriterion, OutlineSection
from app.services.proposal_outline_dedup import (
    filter_lean_outline_sections,
    rfp_lists_section_heading,
    stamp_outline_evaluation_weights,
)


class TocDemandedSectionTests(unittest.TestCase):
    def test_toc_heading_detected_without_shall_submit_verb(self) -> None:
        rfp = (
            "SECTION IV — PROPOSAL CONTENTS\n"
            "1. Cover Letter\n"
            "2. Technical Approach\n"
            "3. Project Schedule\n"
            "4. Cost Proposal\n"
        )
        self.assertTrue(rfp_lists_section_heading(rfp, "Technical Approach"))
        self.assertTrue(rfp_lists_section_heading(rfp, "Cost Proposal"))
        self.assertFalse(rfp_lists_section_heading(rfp, "Sex Offender Registration"))

    def test_toc_listed_generic_title_survives_lean_filter(self) -> None:
        # "Our Approach" is generic filler, but TOC names it — must keep.
        rfp = (
            "Proposal shall include the following sections:\n"
            "1. Cover Letter\n"
            "2. Our Approach\n"
            "3. References\n"
        )
        section = {
            "id": "sec-approach",
            "title": "Our Approach",
            "required": True,
            "order": 1,
        }
        kept, dropped = filter_lean_outline_sections([section], rfp_context=rfp)
        self.assertEqual([s["id"] for s in kept], ["sec-approach"])
        self.assertEqual(dropped, [])


class StampEvalWeightTests(unittest.TestCase):
    def test_stamp_weights_from_evaluation_criteria_onto_outline(self) -> None:
        sections = [
            OutlineSection(id="a", title="Technical Approach & Methodology", order=1),
            OutlineSection(id="b", title="References", order=2),
        ]
        criteria = [
            EvaluationCriterion(name="Technical Approach", weight=35),
            EvaluationCriterion(name="References", weight=10),
        ]
        stamp_outline_evaluation_weights(sections, criteria)
        self.assertEqual(sections[0].evaluation_weight, 35)
        self.assertEqual(sections[1].evaluation_weight, 10)

    def test_stamped_weight_protects_from_lean_drop(self) -> None:
        rfp = "Submit pricing forms and three client references."  # no approach mention
        section = OutlineSection(
            id="sec-approach",
            title="Our Approach",
            order=1,
            required=True,
        )
        stamp_outline_evaluation_weights(
            [section],
            [EvaluationCriterion(name="Our Approach", weight=30)],
        )
        kept, dropped = filter_lean_outline_sections([section], rfp_context=rfp)
        self.assertEqual([s.id for s in kept], ["sec-approach"])
        self.assertEqual(dropped, [])


class StructureStubRecoveryTests(unittest.TestCase):
    def test_adds_stub_for_unmatched_scored_spec(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-cov",
            sections=[
                ProposalSection(
                    id="s1",
                    title="Cover Letter",
                    content="Dear evaluators.",
                )
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        specs = [
            RfpSectionSpec(
                rfp_title="Technical Approach",
                required_headings=["Discovery", "Delivery"],
                instructions="Follow RFP outline",
                evaluation_weight="30 pts",
            )
        ]
        updated, logs = ensure_missing_scored_section_stubs(draft, specs)
        titles = [s.title for s in updated.sections]
        self.assertIn("Technical Approach", titles)
        self.assertTrue(any("added missing" in x.casefold() for x in logs))
        stub = next(s for s in updated.sections if s.title == "Technical Approach")
        self.assertIn("Discovery", stub.content or "")
        self.assertIn("[MANUAL FILL:", stub.content or "")
        self.assertNotIn("[VERIFY:", stub.content or "")

    def test_does_not_add_references_twin_when_toc_already_has_references(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-cov",
            sections=[
                ProposalSection(
                    id="s20",
                    title="References — At least three references for similar engagements",
                    content="Three client contacts with phone and email.",
                )
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        specs = [
            RfpSectionSpec(
                rfp_title="References & Past Performance",
                required_headings=["Client name", "Contact"],
                instructions="Scored references narrative",
                evaluation_weight="15 pts",
            )
        ]
        updated, logs = ensure_missing_scored_section_stubs(draft, specs)
        titles = [s.title for s in updated.sections]
        self.assertEqual(len(updated.sections), 1)
        self.assertNotIn("References & Past Performance", titles)
        self.assertFalse(any("added missing" in x.casefold() for x in logs))

    def test_company_background_does_not_stub_when_static_1_3_covers_it(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-cov",
            sections=[
                ProposalSection(
                    id="section-1-who-we-are",
                    title="Who We Are",
                    content="zö is a full-service agency.",
                ),
                ProposalSection(
                    id="section-1-insurance",
                    title="Insurance Information",
                    content="GL and E&O as listed in companyfacts.",
                ),
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        specs = [
            RfpSectionSpec(
                rfp_title="Company Background",
                instructions="Required in this RFP's submission sequence.",
                same_ask_as=["Who We Are"],
                satisfied_by_static_company_block=True,
            )
        ]
        updated, logs = ensure_missing_scored_section_stubs(draft, specs)
        self.assertEqual(len(updated.sections), 2)
        self.assertEqual(updated.sections[0].title, "Who We Are")
        self.assertFalse(any("added missing" in x.casefold() for x in logs))


class IntelligenceTabOrderTests(unittest.TestCase):
    def test_keeps_section_1_3_first_and_orders_dynamic_tabs(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-cov",
            sections=[
                ProposalSection(
                    id="section-1-who-we-are",
                    title="Who We Are",
                    content="Who we are body.",
                ),
                ProposalSection(
                    id="section-2-bio-sonja",
                    title="Sonja — Bio",
                    content="Bio body.",
                ),
                ProposalSection(id="rfp-cost", title="Cost Proposal", content="Fees."),
                ProposalSection(id="rfp-cover", title="Cover Letter", content="Dear evaluators."),
                ProposalSection(id="rfp-tech", title="Technical Proposal", content="Approach."),
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        specs = [
            RfpSectionSpec(rfp_title="Cover Letter", instructions="First."),
            RfpSectionSpec(
                rfp_title="Company Background",
                instructions="Wrap 1.1–1.5.",
                same_ask_as=["Who We Are"],
                satisfied_by_static_company_block=True,
            ),
            RfpSectionSpec(rfp_title="Technical Proposal", instructions="Technical."),
            RfpSectionSpec(rfp_title="Cost Proposal", instructions="Cost."),
        ]
        ordered, logs = order_draft_to_rfp_sequence(draft, specs)
        self.assertEqual(
            [s.id for s in ordered.sections],
            [
                "section-1-who-we-are",
                "section-2-bio-sonja",
                "rfp-cover",
                "rfp-tech",
                "rfp-cost",
            ],
        )
        self.assertEqual(ordered.sections[0].title, "Who We Are")
        self.assertTrue(any("intelligence tabs" in x.casefold() for x in logs))

        wrapped, wrap_logs = ensure_company_block_wrapper_heading(ordered, specs)
        self.assertEqual(wrapped.sections[0].title, "Company Background")
        self.assertEqual(wrapped.sections[1].id, "section-1-who-we-are")
        self.assertEqual(wrapped.sections[1].content, "Who we are body.")
        self.assertTrue(any("1.1–1.5" in x or "1.1-1.5" in x for x in wrap_logs))

    def test_apply_layout_drops_pointer_tab_and_keeps_who_we_are(self) -> None:
        from app.services.proposal_fulfill_rfp_structure import apply_rfp_toc_layout

        draft = ProposalDraft(
            rfpId="rfp-cov",
            sections=[
                ProposalSection(
                    id="section-1-who-we-are",
                    title="Who We Are",
                    content="Who we are body.",
                ),
                ProposalSection(
                    id="rfp-company-bg",
                    title="Company Background",
                    content="Covered in Sections 1–3 (company / team / experience).",
                ),
                ProposalSection(id="rfp-cover", title="Cover Letter", content="Dear."),
                ProposalSection(id="rfp-tech", title="Technical Proposal", content="Tech."),
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        specs = [
            RfpSectionSpec(rfp_title="Cover Letter", instructions="First."),
            RfpSectionSpec(
                rfp_title="Company Background",
                instructions="Wrap.",
                same_ask_as=["Who We Are"],
                satisfied_by_static_company_block=True,
            ),
            RfpSectionSpec(rfp_title="Technical Proposal", instructions="Tech."),
        ]
        updated, logs = apply_rfp_toc_layout(draft, specs)
        titles = [s.title for s in updated.sections]
        self.assertEqual(titles[0], "Company Background")
        self.assertEqual(updated.sections[1].id, "section-1-who-we-are")
        self.assertEqual(updated.sections[1].content, "Who we are body.")
        self.assertNotIn("rfp-company-bg", [s.id for s in updated.sections])
        self.assertEqual(
            [s.id for s in updated.sections if s.id.startswith("rfp-")],
            ["rfp-structure-company-block-header", "rfp-cover", "rfp-tech"],
        )
        self.assertTrue(any("duplicate company-identity" in x.casefold() for x in logs))

    def test_layout_does_not_duplicate_company_header_or_background_stubs(self) -> None:
        from app.services.proposal_fulfill_rfp_structure import (
            COMPANY_BLOCK_HEADER_ID,
            apply_rfp_toc_layout,
            ensure_missing_scored_section_stubs,
        )

        draft = ProposalDraft(
            rfpId="rfp-dup",
            sections=[
                ProposalSection(
                    id="section-1-who-we-are",
                    title="Who We Are",
                    content="Who we are body.",
                ),
                ProposalSection(
                    id="rfp-structure-company-background",
                    title="Company Background",
                    content="Stub 1",
                ),
                ProposalSection(
                    id="rfp-structure-company-background-2",
                    title="Company Background",
                    content="Stub 2",
                ),
                ProposalSection(
                    id=COMPANY_BLOCK_HEADER_ID,
                    title="Company Background",
                    content="Header.",
                ),
                ProposalSection(
                    id=COMPANY_BLOCK_HEADER_ID,
                    title="Company Background",
                    content="Header copy.",
                ),
                ProposalSection(id="rfp-cover", title="Cover Letter", content="Dear."),
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        specs = [
            RfpSectionSpec(rfp_title="Cover Letter", instructions="First."),
            RfpSectionSpec(
                rfp_title="Company Background",
                instructions="Wrap.",
                satisfied_by_static_company_block=True,
            ),
        ]
        updated, _logs = apply_rfp_toc_layout(draft, specs)
        ids = [s.id for s in updated.sections]
        self.assertEqual(ids.count(COMPANY_BLOCK_HEADER_ID), 1)
        self.assertEqual(
            [s.title for s in updated.sections if s.title == "Company Background"],
            ["Company Background"],
        )
        self.assertNotIn("rfp-structure-company-background", ids)
        self.assertNotIn("rfp-structure-company-background-2", ids)

        stubbed, stub_logs = ensure_missing_scored_section_stubs(updated, specs)
        self.assertEqual(
            [s.id for s in stubbed.sections if "company-background" in s.id],
            [],
        )
        self.assertFalse(any("Company Background" in x for x in stub_logs))

    def test_faq_titles_are_rfp_spec_noise(self) -> None:
        from app.services.proposal_fulfill_rfp_structure import _spec_is_rfp_title_noise

        self.assertTrue(
            _spec_is_rfp_title_noise(
                RfpSectionSpec(rfp_title="How much does Remodeling Design cost?")
            )
        )
        self.assertFalse(_spec_is_rfp_title_noise(RfpSectionSpec(rfp_title="Cost Proposal")))


if __name__ == "__main__":
    unittest.main()
