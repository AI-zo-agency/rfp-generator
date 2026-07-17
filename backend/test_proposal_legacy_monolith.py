"""Tests for legacy monolith stripping in Sections 1–3 persistence."""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timezone

# proposal_generator imports langchain_openai at module load — stub for unit tests.
if "langchain_openai" not in sys.modules:
    stub = types.ModuleType("langchain_openai")

    class ChatOpenAI:  # noqa: D401
        pass

    stub.ChatOpenAI = ChatOpenAI
    sys.modules["langchain_openai"] = stub

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_generator import (
    _strip_legacy_monolith_sections,
    static_sections_1_3_have_content,
)


def _sec(section_id: str, title: str, content: str = "body") -> ProposalSection:
    return ProposalSection(
        id=section_id,
        title=title,
        content=content,
        status="generated",
        source="template",
        mode="pull",
        wordTarget=400,
    )


class LegacyMonolithStripTests(unittest.TestCase):
    def test_strip_removes_company_overview_monolith(self) -> None:
        sections = [
            _sec("section-1-company-overview", "Section 1 — Company Overview", "For HCCC, that means..."),
            _sec("section-1-who-we-are", "1.1 — Who We Are", "For MDWFP, that means..."),
            _sec("section-2-team-overview", "Section 2 — Team", "old team block"),
            _sec("section-2-bio-sonja-anderson", "2.1 — Sonja", "bio"),
        ]
        stripped = _strip_legacy_monolith_sections(sections)
        ids = [s.id for s in stripped]
        self.assertNotIn("section-1-company-overview", ids)
        self.assertNotIn("section-2-team-overview", ids)
        self.assertIn("section-1-who-we-are", ids)
        self.assertIn("section-2-bio-sonja-anderson", ids)

    def test_legacy_only_is_not_complete_sections_1_3(self) -> None:
        draft = ProposalDraft(
            rfpId="test",
            updatedAt=datetime.now(timezone.utc).isoformat(),
            sections=[
                _sec(
                    "section-1-company-overview",
                    "Section 1 — Company Overview",
                    "For HCCC, that means enrollment funnel...",
                ),
                _sec("section-2-team-overview", "Section 2 — Team", "team"),
                _sec("section-3-our-work", "Section 3 — Our Work", "work"),
            ],
        )
        self.assertFalse(static_sections_1_3_have_content(draft))


if __name__ == "__main__":
    unittest.main()
