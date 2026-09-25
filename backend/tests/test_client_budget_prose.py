"""Client-facing budget prose must not leak internal guide references."""

from __future__ import annotations

import sys
import types
import unittest

if "langchain_openai" not in sys.modules:
    langchain_openai = types.ModuleType("langchain_openai")

    class ChatOpenAI:  # pragma: no cover
        pass

    langchain_openai.ChatOpenAI = ChatOpenAI
    sys.modules["langchain_openai"] = langchain_openai

from app.services.proposal_budget_content import _scrub_internal_budget_jargon


class ClientBudgetProseTests(unittest.TestCase):
    def test_scrub_removes_guide_and_manual_fill(self) -> None:
        raw = (
            "Rates follow zö's Industry Low pricing guide for municipal work. "
            "Fixed-fee pricing per 00_Guide_Pricing (Industry Low tier). "
            "*Rates are fully burdened agency work rates from 00_Guide_Pricing where bound; "
            "blank cells are MANUAL FILL — never invent a named-person $/hr.*"
        )
        cleaned = _scrub_internal_budget_jargon(raw)
        self.assertNotIn("00_Guide_Pricing", cleaned)
        self.assertNotIn("blank cells are MANUAL FILL", cleaned.casefold())
        self.assertNotIn("pricing guide", cleaned.casefold())




if __name__ == "__main__":
    unittest.main()
