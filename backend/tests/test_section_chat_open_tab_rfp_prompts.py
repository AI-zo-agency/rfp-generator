"""Section chat prompts: open-tab-only + RFP alignment for LLM revise agents."""

from __future__ import annotations

import unittest

from app.services.proposal_langchain_agents import USER_REVISE_SYSTEM
from app.services.proposal_section_editor import (
    EDIT_SCOPE_PLAN_PROMPT,
    SECTION_REDRAFT_PROMPT,
    SELECTION_EDIT_PROMPT,
)


class SectionChatLlmGuardrailPromptTests(unittest.TestCase):
    def test_user_revise_open_tab_and_rfp_alignment(self) -> None:
        self.assertIn("OPEN TAB ONLY", USER_REVISE_SYSTEM)
        self.assertIn("MUST NOT rewrite", USER_REVISE_SYSTEM)
        self.assertIn("RFP ALIGNMENT", USER_REVISE_SYSTEM)
        self.assertIn("search_rfp_requirements", USER_REVISE_SYSTEM)

    def test_section_redraft_open_tab_and_rfp_alignment(self) -> None:
        self.assertIn("OPEN TAB ONLY", SECTION_REDRAFT_PROMPT)
        self.assertIn("READ-ONLY", SECTION_REDRAFT_PROMPT)
        self.assertIn("RFP ALIGNMENT", SECTION_REDRAFT_PROMPT)

    def test_selection_and_scope_plan_same_guards(self) -> None:
        self.assertIn("OPEN TAB + EXCERPT ONLY", SELECTION_EDIT_PROMPT)
        self.assertIn("RFP ALIGNMENT", SELECTION_EDIT_PROMPT)
        self.assertIn("siblingEdits MUST be []", EDIT_SCOPE_PLAN_PROMPT)
        self.assertIn("check demanded / scored asks", EDIT_SCOPE_PLAN_PROMPT)


if __name__ == "__main__":
    unittest.main()
