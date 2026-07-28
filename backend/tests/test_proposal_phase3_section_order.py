"""Phase 3 must draft in outline order and not mark complete with empty forms."""

from __future__ import annotations

import sys
import types
import unittest

if "langchain_openai" not in sys.modules:
    langchain_openai = types.ModuleType("langchain_openai")

    class ChatOpenAI:  # pragma: no cover - import stub
        pass

    langchain_openai.ChatOpenAI = ChatOpenAI
    sys.modules["langchain_openai"] = langchain_openai

from app.models.proposal import (
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
    RfpSectionMap,
)
from app.services.proposal_draft_llm import SECTION_DRAFT_FAILURE_PLACEHOLDER
from app.services.proposal_drafting_graph import (
    order_sections_for_phase3_draft,
    partition_phase3_sections,
)
from app.services.proposal_pipeline_checkpoint import phase_is_complete


def _mapped(sid: str, title: str, weight: int | None = None) -> RfpSectionMap:
    return RfpSectionMap(
        id=sid,
        title=title,
        requirements=["Address per RFP"],
        evaluationWeight=weight,
    )


def _section(sid: str, title: str, content: str = "") -> ProposalSection:
    return ProposalSection(
        id=sid,
        title=title,
        content=content,
        source="rfp",
        status="generated" if content.strip() else "outline",
    )


class Phase3SectionOrderTests(unittest.TestCase):
    def test_preserves_outline_order_not_evaluation_weight(self) -> None:
        sections = [
            {"id": "form-2", "title": "Form 2 — Proposal Submission", "evaluationWeight": None},
            {"id": "qual", "title": "Firm Qualifications", "evaluationWeight": 40},
            {"id": "form-4", "title": "Form 4 — Debarment", "evaluationWeight": 0},
            {"id": "access", "title": "Accessibility Expertise", "evaluationWeight": 25},
        ]
        ordered = order_sections_for_phase3_draft(sections)
        self.assertEqual(
            [s["id"] for s in ordered],
            ["form-2", "qual", "form-4", "access"],
        )

    def test_partition_skips_filled_keeps_empty_and_failures(self) -> None:
        mapped = [
            _mapped("form-2", "Form 2"),
            _mapped("qual", "Firm Qualifications", 40),
            _mapped("form-4", "Form 4"),
            _mapped("access", "Accessibility Expertise", 25),
        ]
        existing = {
            "qual": _section("qual", "Firm Qualifications", "Strong quals prose."),
            "access": _section(
                "access",
                "Accessibility Expertise",
                SECTION_DRAFT_FAILURE_PLACEHOLDER,
            ),
        }
        to_draft, already = partition_phase3_sections(mapped, existing)
        self.assertEqual([s.id for s in to_draft], ["form-2", "form-4", "access"])
        self.assertEqual([s.id for s in already], ["qual"])

    def test_phase3_not_complete_at_85_percent_with_empty_forms(self) -> None:
        mapped = [_mapped(f"s{i}", f"Section {i}", 10 if i > 1 else None) for i in range(1, 11)]
        # Form-like first section empty; 9/10 filled (=90%) — must still be incomplete.
        draft_sections = [
            _section("s1", "Section 1", ""),
            *[_section(f"s{i}", f"Section {i}", f"Content {i}") for i in range(2, 11)],
        ]
        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=draft_sections,
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        research = ProposalResearchCache(
            rfpId="rfp-1",
            rfpSections=mapped,
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        self.assertFalse(
            phase_is_complete(draft=draft, research=research, phase="phase-3")
        )

    def test_phase3_complete_when_all_draftable_filled(self) -> None:
        mapped = [
            _mapped("form-2", "Form 2"),
            _mapped("qual", "Firm Qualifications", 40),
            _mapped("dup", "Section 1 — Company Overview"),
        ]
        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _section("form-2", "Form 2", "Filled form."),
                _section("qual", "Firm Qualifications", "Filled quals."),
            ],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        research = ProposalResearchCache(
            rfpId="rfp-1",
            rfpSections=mapped,
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        self.assertTrue(
            phase_is_complete(draft=draft, research=research, phase="phase-3")
        )


if __name__ == "__main__":
    unittest.main()
