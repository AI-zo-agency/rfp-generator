"""The advisory-vs-edit decision, tested directly.

This expression used to live inline inside a 1,700-line dispatcher, so the only
way to exercise it was to run the whole turn. Its ordering is load-bearing: a
structure ask must beat a classifier that said "advisory", and the keyword gate
must only decide when the classifier abstained.
"""

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

from app.services.proposal_section_editor import decide_chat_route  # noqa: E402


def route(intent="none", message="rewrite this section", selection=False, history=None):
    return decide_chat_route(
        chat_intent=intent,
        user_message=message,
        selection_mode=selection,
        conversation_history=history,
    )


class DecideChatRouteTests(unittest.TestCase):
    def test_selection_edit_always_mutates(self) -> None:
        r = route(intent="advisory", message="what is this?", selection=True)
        self.assertFalse(r.advisory)
        self.assertEqual(r.reason, "selection_edit")

    def test_structure_ask_overrides_a_classifier_that_said_advisory(self) -> None:
        """Adding a section is a mutation even if the classifier disagreed."""
        r = route(intent="advisory", message="add a new section titled Staff Planning")
        self.assertFalse(r.advisory)
        self.assertEqual(r.reason, "structure_ask")

    def test_classifier_advisory_wins_over_edit_keywords(self) -> None:
        r = route(intent="advisory", message="rewrite this section")
        self.assertTrue(r.advisory)
        self.assertEqual(r.reason, "classifier_advisory")

    def test_classifier_single_edit_beats_advisory_keywords(self) -> None:
        r = route(intent="single_edit", message="does this meet the RFP?")
        self.assertFalse(r.advisory)
        self.assertEqual(r.reason, "classifier_single_edit")

    def test_classifier_multi_patch_mutates(self) -> None:
        r = route(intent="multi_patch", message="apply those fixes")
        self.assertFalse(r.advisory)
        self.assertEqual(r.reason, "classifier_multi_patch")

    def test_keyword_gate_decides_only_when_classifier_abstains(self) -> None:
        self.assertFalse(route(intent="none", message="rewrite this section").advisory)
        self.assertEqual(
            route(intent="none", message="rewrite this section").reason, "keyword_edit"
        )

    def test_default_is_advisory(self) -> None:
        """The safe direction: never rewrite a draft on an unrecognised ask."""
        r = route(intent="none", message="hmm")
        self.assertTrue(r.advisory)
        self.assertEqual(r.reason, "keyword_default_advisory")

    def test_degraded_classifier_falls_through_to_keywords(self) -> None:
        """A degraded classifier reports intent 'none', so keywords decide.

        With the broadened verb list this now routes correctly instead of
        defaulting to advisory — which is what made an outage look like a refusal.
        """
        self.assertFalse(route(intent="none", message="tighten the opening").advisory)
        self.assertTrue(route(intent="none", message="what is missing here?").advisory)

    def test_short_confirmation_after_an_offer_is_an_edit(self) -> None:
        history = [{"role": "assistant", "content": "I found 3 issues. Shall I apply them?"}]
        r = route(intent="none", message="yes", history=history)
        self.assertFalse(r.advisory)
        self.assertEqual(r.reason, "keyword_edit")


if __name__ == "__main__":
    unittest.main()
