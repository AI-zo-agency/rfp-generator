"""Routing accuracy over a labelled corpus of realistic chat asks.

Measured before this suite existed:
    _wants_section_edit       24/29 (82%)
    _is_outline_structure_ask 27/29 (93%)

Roughly one in five edit requests was answered with advice instead of being
executed. This file turns routing accuracy into a number a test enforces, so it
is not something a human has to check by typing into the chat.

When a real message routes wrongly, add it to
tests/fixtures/chat_routing_cases.json rather than patching a regex blind.
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
    _is_outline_structure_ask,
    _wants_section_edit,
)

FIXTURE = Path(__file__).parent / "fixtures" / "chat_routing_cases.json"
CASES = json.loads(FIXTURE.read_text())["cases"]


class RoutingAccuracyTests(unittest.TestCase):
    def test_every_case_routes_correctly(self) -> None:
        edit_failures: list[str] = []
        structure_failures: list[str] = []

        for case in CASES:
            message = case["message"]
            if _wants_section_edit(message) != case["edit"]:
                edit_failures.append(
                    f"  [{case['category']}] {message!r} "
                    f"— wants_edit={not case['edit']}, expected {case['edit']}"
                )
            if _is_outline_structure_ask(message) != case["structure"]:
                structure_failures.append(
                    f"  [{case['category']}] {message!r} "
                    f"— is_structure={not case['structure']}, expected {case['structure']}"
                )

        total = len(CASES)
        report = (
            f"\n_wants_section_edit      : {total - len(edit_failures)}/{total}"
            f"\n_is_outline_structure_ask: {total - len(structure_failures)}/{total}"
        )
        if edit_failures:
            report += "\n\nedit-intent misroutes:\n" + "\n".join(edit_failures)
        if structure_failures:
            report += "\n\nstructure misroutes:\n" + "\n".join(structure_failures)

        self.assertEqual([], edit_failures + structure_failures, report)

    def test_advisory_asks_never_mutate(self) -> None:
        """The costly direction: never rewrite a draft when asked a question."""
        for case in CASES:
            if case["category"] == "advisory":
                self.assertFalse(
                    _wants_section_edit(case["message"]),
                    f"advisory ask would have edited the draft: {case['message']!r}",
                )

    def test_structure_asks_are_also_edit_intent(self) -> None:
        """Adding a section is a mutation; it must never fall to advisory."""
        for case in CASES:
            if case["structure"]:
                self.assertTrue(
                    _wants_section_edit(case["message"]),
                    f"structure ask routed to advisory: {case['message']!r}",
                )

    def test_corpus_covers_both_directions(self) -> None:
        """Guard against the corpus decaying into a one-sided suite."""
        self.assertGreaterEqual(sum(1 for c in CASES if c["edit"]), 10)
        self.assertGreaterEqual(sum(1 for c in CASES if not c["edit"]), 5)


if __name__ == "__main__":
    unittest.main()
