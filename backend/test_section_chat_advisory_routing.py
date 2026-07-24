"""Tests for advisory vs rewrite routing in section chat."""

from __future__ import annotations

import sys
import types
import unittest

if "langchain_openai" not in sys.modules:
    stub = types.ModuleType("langchain_openai")

    class ChatOpenAI:  # noqa: D401
        pass

    stub.ChatOpenAI = ChatOpenAI
    sys.modules["langchain_openai"] = stub

from app.services.proposal_section_editor import _wants_section_edit


class WantsSectionEditTests(unittest.TestCase):
    def test_case_study_fit_audit_is_advisory(self) -> None:
        msg = (
            "now check all case studies meet expetations of rfp "
            "if not then list which dont"
        )
        self.assertFalse(_wants_section_edit(msg))

    def test_evaluate_questions_are_advisory(self) -> None:
        self.assertFalse(_wants_section_edit("Does 3.3 meet the RFP?"))
        self.assertFalse(_wants_section_edit("which case studies don't fit"))
        self.assertFalse(
            _wants_section_edit("review all Our Work sections against requirements")
        )

    def test_explicit_rewrites_still_edit(self) -> None:
        self.assertTrue(_wants_section_edit("rewrite 3.3 with San Leandro from KB"))
        self.assertTrue(_wants_section_edit("replace Maricopa with a tourism case study"))
        self.assertTrue(_wants_section_edit("improve this section — more punchy"))
        self.assertTrue(_wants_section_edit("add a paragraph about seasonal campaigns"))

    def test_check_then_fix_is_edit(self) -> None:
        self.assertTrue(
            _wants_section_edit(
                "check which case studies fail the RFP then replace the weak ones from KB"
            )
        )


if __name__ == "__main__":
    unittest.main()
