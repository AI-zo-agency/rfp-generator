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

    def test_does_not_stub_when_closing_tab_already_covers_exhibit(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-closing-dedup",
            sections=[
                ProposalSection(
                    id="rfp-closing-exhibit_k",
                    title="Exhibit K — Contractor References",
                    content="[MANUAL FILL: Attach signed reference form]",
                )
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        specs = [
            RfpSectionSpec(
                rfp_title="Exhibit K Contractor References",
                instructions="Required exhibit",
                mandated_submission_format=True,
            )
        ]
        updated, logs = ensure_missing_scored_section_stubs(draft, specs)
        self.assertEqual(len(updated.sections), 1)
        self.assertEqual(updated.sections[0].id, "rfp-closing-exhibit_k")
        self.assertFalse(
            any("added missing scored section stub" in x.casefold() for x in logs),
            msg=f"unexpected stub: {logs}",
        )

    def test_does_not_stub_when_rfp_title_already_present(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-title-dedup",
            sections=[
                ProposalSection(
                    id="rfp-cover-letter",
                    title="Cover Letter",
                    content="Dear evaluators.",
                )
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        specs = [
            RfpSectionSpec(
                rfp_title="Cover Letter",
                instructions="Signed cover letter required",
                mandated_submission_format=True,
            )
        ]
        updated, logs = ensure_missing_scored_section_stubs(draft, specs)
        self.assertEqual(len(updated.sections), 1)
        self.assertFalse(
            any(
                "added missing scored section stub" in x.casefold()
                for x in logs
            ),
            msg=f"unexpected stub: {logs}",
        )


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
        self.assertIn("DESIGNER NOTE", wrapped.sections[0].content or "")
        self.assertTrue(any("1.1–1.5" in x or "1.1-1.5" in x for x in wrap_logs))

    def test_repair_pointer_only_section_gets_draft_stub(self) -> None:
        from app.services.proposal_fulfill_rfp_structure import (
            is_pointer_only_company_delegation,
            repair_pointer_only_rfp_sections,
        )

        pointer = (
            "The company background for this submission is Sections 1.1–1.5 below "
            "(Who We Are through Insurance Information)."
        )
        self.assertTrue(is_pointer_only_company_delegation(pointer))
        draft = ProposalDraft(
            rfpId="rfp-cov",
            sections=[
                ProposalSection(
                    id="rfp-bg",
                    title="Background and Experience",
                    content=pointer,
                )
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        repaired, logs = repair_pointer_only_rfp_sections(draft)
        self.assertIn("MANUAL FILL", repaired.sections[0].content or "")
        self.assertTrue(any("pointer-only" in x.casefold() for x in logs))

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


class AlignOutlineOnIntelligencePlanTests(unittest.TestCase):
    def test_adds_missing_rfp_tabs_and_reorders(self) -> None:
        from app.services.proposal_fulfill_rfp_structure import (
            align_outline_sections_to_rfp_specs,
        )

        planner = [
            OutlineSection(id="rfp-sec-1", title="Price", order=1, required=True),
            OutlineSection(
                id="rfp-sec-2",
                title="Technical Approach",
                order=2,
                required=True,
            ),
        ]
        specs = [
            RfpSectionSpec(rfp_title="Cover Letter", instructions="Required cover letter."),
            RfpSectionSpec(
                rfp_title="Technical Approach",
                required_headings=["A. Method", "B. Timeline"],
                instructions="Answer the technical ask.",
            ),
            RfpSectionSpec(
                rfp_title="Proposal Pricing — Hourly Rates by Labor Category",
                evaluation_weight="25 pts",
                same_ask_as=["Price"],
                mandated_submission_format=True,
                instructions="Use the buyer's rate table.",
            ),
        ]
        kept, logs = align_outline_sections_to_rfp_specs(
            planner,
            specs,
            section_factory=lambda raw: OutlineSection.model_validate(raw),
        )
        titles = [s.title for s in kept]
        self.assertEqual(titles[0], "Cover Letter")
        self.assertEqual(titles[1], "Technical Approach")
        # Align does not retitle a near-duplicate pricing tab ("Price" ≈
        # "Proposal Pricing…") — it keeps the existing label and does not stub.
        self.assertTrue(any("price" in t.casefold() for t in titles))
        self.assertNotIn("1.1 — Who We Are", titles)
        cover = next(s for s in kept if s.title == "Cover Letter")
        self.assertTrue(cover.protect_from_cap)
        self.assertTrue(any("added missing scored section stub" in x for x in logs))
        approach = next(s for s in kept if s.title == "Technical Approach")
        self.assertEqual(approach.children, ["A. Method", "B. Timeline"])
        self.assertEqual(approach.id, "rfp-sec-2")

    def test_static_company_ask_does_not_mint_outline_tab(self) -> None:
        from app.services.proposal_fulfill_rfp_structure import (
            align_outline_sections_to_rfp_specs,
        )

        planner = [
            OutlineSection(id="rfp-sec-1", title="Technical Approach", order=1),
        ]
        specs = [
            RfpSectionSpec(
                rfp_title="Company Background",
                satisfied_by_static_company_block=True,
                instructions="Header only.",
            ),
            RfpSectionSpec(rfp_title="Technical Approach", instructions="Required."),
        ]
        kept, logs = align_outline_sections_to_rfp_specs(
            planner,
            specs,
            section_factory=lambda raw: OutlineSection.model_validate(raw),
        )
        self.assertEqual([s.title for s in kept], ["Technical Approach"])
        self.assertFalse(any("Company Background" in x for x in logs))

    def test_planner_prompt_lists_required_tabs_not_static_identity(self) -> None:
        from app.services.proposal_fulfill_rfp_structure import (
            format_rfp_structure_specs_for_planner,
        )

        block = format_rfp_structure_specs_for_planner(
            [
                RfpSectionSpec(
                    rfp_title="Who We Are",
                    satisfied_by_static_company_block=True,
                ),
                RfpSectionSpec(rfp_title="Cover Letter", instructions="Required."),
                RfpSectionSpec(
                    rfp_title="Technical Approach",
                    required_headings=["A. Method"],
                    evaluation_weight="40 pts",
                ),
            ]
        )
        self.assertIn("Cover Letter", block)
        self.assertIn("Technical Approach", block)
        self.assertIn("A. Method", block)
        self.assertIn("1. Cover Letter", block)
        self.assertNotIn("Who We Are", block)


class IntelligencePlannerUsesAlignExtractTests(unittest.IsolatedAsyncioTestCase):
    async def test_format_extract_replaces_planner_llm(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.services.proposal_fulfill_rfp_structure import RfpSectionSpec
        from app.services.proposal_intelligence.agents.dynamic_section_planner import (
            run_dynamic_section_planner,
        )
        from app.services.proposal_intelligence.schemas import ProposalExecutionPlan

        specs = [
            RfpSectionSpec(rfp_title="Cover Letter", instructions="Required cover letter."),
            RfpSectionSpec(rfp_title="Technical Approach", instructions="Required approach."),
        ]
        planner_llm = AsyncMock(side_effect=AssertionError("planner LLM must not run"))
        rfp = (
            "Proposal shall include:\n"
            "1. Cover Letter\n"
            "2. Technical Approach\n"
        )
        with (
            patch(
                "app.services.proposal_fulfill_rfp_structure.extract_rfp_submission_format_specs",
                new=AsyncMock(return_value=specs),
            ),
            patch(
                "app.services.proposal_intelligence.agents.dynamic_section_planner.safe_chat_json",
                new=planner_llm,
            ),
            patch(
                "app.services.proposal_closing_ledger.get_or_extract_closing_ledger",
                new=AsyncMock(return_value=(None, None)),
            ),
            patch(
                "app.services.proposal_intelligence.agents.dynamic_section_planner.ensure_missing_submittals_coverage",
                new=AsyncMock(side_effect=lambda kept, *_a, **_k: (kept, [])),
            ),
        ):
            plan = await run_dynamic_section_planner(
                plan=ProposalExecutionPlan(rfpId="r1"),
                rfp_context=rfp,
                rfp_meta={"title": "Test RFP"},
            )
        planner_llm.assert_not_awaited()
        titles = [s.title for s in plan.writing.proposal_outline.sections]
        self.assertIn("Cover Letter", titles)
        self.assertIn("Technical Approach", titles)
        self.assertLess(titles.index("Cover Letter"), titles.index("Technical Approach"))

    async def test_empty_format_extract_falls_back_to_planner(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.services.proposal_intelligence.agents.dynamic_section_planner import (
            run_dynamic_section_planner,
        )
        from app.services.proposal_intelligence.schemas import ProposalExecutionPlan

        planner_json = {
            "sections": [
                {
                    "id": "rfp-sec-1",
                    "title": "Technical Approach",
                    "order": 1,
                    "required": True,
                }
            ],
            "confidence": 0.8,
        }
        rfp = "Proposal shall include:\n1. Technical Approach\n"
        with (
            patch(
                "app.services.proposal_fulfill_rfp_structure.extract_rfp_submission_format_specs",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.proposal_intelligence.agents.dynamic_section_planner.safe_chat_json",
                new=AsyncMock(return_value=(planner_json, "test")),
            ),
            patch(
                "app.services.proposal_closing_ledger.get_or_extract_closing_ledger",
                new=AsyncMock(return_value=(None, None)),
            ),
            patch(
                "app.services.proposal_intelligence.agents.dynamic_section_planner.ensure_missing_submittals_coverage",
                new=AsyncMock(side_effect=lambda kept, *_a, **_k: (kept, [])),
            ),
        ):
            plan = await run_dynamic_section_planner(
                plan=ProposalExecutionPlan(rfpId="r1"),
                rfp_context=rfp,
                rfp_meta={"title": "Test RFP"},
            )
        titles = [s.title for s in plan.writing.proposal_outline.sections]
        self.assertIn("Technical Approach", titles)


if __name__ == "__main__":
    unittest.main()
