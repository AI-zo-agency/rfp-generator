"""Phase 3 partial merge must keep budget, closing, and custom tabs."""

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

from app.models.proposal import ProposalSection
from app.services.proposal_generator import _merge_phase3_preserving_extras


def _sec(
    sid: str,
    title: str,
    content: str = "",
    *,
    custom: bool = False,
) -> ProposalSection:
    return ProposalSection(
        id=sid,
        title=title,
        content=content,
        custom=custom,
        status="generated" if content.strip() else "outline",
    )


class Phase3MergePreserveExtrasTests(unittest.TestCase):
    def test_keeps_budget_and_closing_when_rebuilding_rfp_tabs(self) -> None:
        static = [
            _sec("section-1", "Company Overview", "Overview prose."),
            _sec("section-2-bio-1", "Jane Doe", "Designer note stub."),
        ]
        rfp_drafted = [_sec("rfp-qual", "Qualifications", "Qual prose.")]
        existing = [
            *static,
            _sec("section-budget-pricing", "Budget & Pricing", "| Line | Fee |\n|---|---|\n| A | $1 |"),
            _sec("rfp-closing-w9", "W-9", "W-9 attestation."),
            _sec("custom-appendix", "Appendix A", "Custom appendix.", custom=True),
            _sec("rfp-qual", "Qualifications (old)", "Stale qual — replaced by draft."),
        ]

        merged = _merge_phase3_preserving_extras(static, rfp_drafted, existing)
        ids = {section.id for section in merged}

        self.assertIn("section-budget-pricing", ids)
        self.assertIn("rfp-closing-w9", ids)
        self.assertIn("custom-appendix", ids)
        self.assertIn("rfp-qual", ids)
        qual = next(s for s in merged if s.id == "rfp-qual")
        self.assertEqual(qual.content, "Qual prose.")

    def test_drops_empty_extras(self) -> None:
        static = [_sec("section-1", "Company Overview", "Overview.")]
        existing = [_sec("section-budget-pricing", "Budget & Pricing", "")]

        merged = _merge_phase3_preserving_extras(static, [], existing)
        ids = {section.id for section in merged}

        self.assertNotIn("section-budget-pricing", ids)
