"""Routing accuracy over a labelled corpus of realistic chat asks.

The keyword gate (_wants_section_edit) is only a fallback when the LLM classifier
abstains (``degraded``). Structure routing (add/delete sidebar tabs) is LLM-only
via ``structure`` intent — not regex.

When a real message routes wrongly, add it to
tests/fixtures/chat_routing_cases.json and fix the LLM classifier or structure planner.
"""

from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path

if "langchain_openai" not in sys.modules:
    stub = types.ModuleType("langchain_openai")

    class ChatOpenAI:  # noqa: D401
        pass

    stub.ChatOpenAI = ChatOpenAI
    sys.modules["langchain_openai"] = stub

from app.services.proposal_section_editor import (  # noqa: E402
    _wants_section_edit,
)

FIXTURE = Path(__file__).parent / "fixtures" / "chat_routing_cases.json"
CASES = json.loads(FIXTURE.read_text())["cases"]


class RoutingAccuracyTests(unittest.TestCase):
    def test_every_case_routes_correctly(self) -> None:
        edit_failures: list[str] = []

        for case in CASES:
            message = case["message"]
            if _wants_section_edit(message) != case["edit"]:
                edit_failures.append(
                    f"  [{case['category']}] {message!r} "
                    f"— wants_edit={not case['edit']}, expected {case['edit']}"
                )

        total = len(CASES)
        report = f"\n_wants_section_edit (keyword fallback): {total - len(edit_failures)}/{total}"
        if edit_failures:
            report += "\n\nedit-intent misroutes:\n" + "\n".join(edit_failures)

        self.assertEqual([], edit_failures, report)

    def test_advisory_asks_never_mutate(self) -> None:
        """The costly direction: never rewrite a draft when asked a question."""
        for case in CASES:
            if case["category"] == "advisory":
                self.assertFalse(
                    _wants_section_edit(case["message"]),
                    f"advisory ask would have edited the draft: {case['message']!r}",
                )

    def test_structure_asks_are_also_edit_intent(self) -> None:
        """Adding a section is a mutation; keyword fallback must not treat it as advisory."""
        for case in CASES:
            if case["structure"]:
                self.assertTrue(
                    _wants_section_edit(case["message"]),
                    f"structure ask routed to advisory via keyword gate: {case['message']!r}",
                )

    def test_corpus_covers_both_directions(self) -> None:
        """Guard against the corpus decaying into a one-sided suite."""
        self.assertGreaterEqual(sum(1 for c in CASES if c["edit"]), 10)
        self.assertGreaterEqual(sum(1 for c in CASES if not c["edit"]), 5)


if __name__ == "__main__":
    unittest.main()
