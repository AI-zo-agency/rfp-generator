"""RFP-demanded sections must survive lean filter + structure recovery."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_fulfill_rfp_structure import (
    RfpSectionSpec,
    ensure_missing_scored_section_stubs,
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


if __name__ == "__main__":
    unittest.main()
