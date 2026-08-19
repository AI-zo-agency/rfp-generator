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


def route(
    intent="none",
    message="rewrite this section",
    selection=False,
    history=None,
    improve_pinned=False,
):
    return decide_chat_route(
        chat_intent=intent,
        user_message=message,
        selection_mode=selection,
        conversation_history=history,
        improve_pinned=improve_pinned,
    )


class DecideChatRouteTests(unittest.TestCase):
    def test_selection_with_an_edit_instruction_mutates(self) -> None:
        r = route(intent="none", message="make this shorter", selection=True)
        self.assertFalse(r.advisory)
        self.assertEqual(r.reason, "selection_edit")

    def test_selection_with_a_question_answers_instead_of_mutating(self) -> None:
        """Highlighting text scopes the ask; it does not authorise a rewrite.

        This previously returned selection_edit for every pinned turn, so
        "can you verify if it is Z'Onion?" silently rewrote the excerpt.
        """
        r = route(intent="none", message="what is this?", selection=True)
        self.assertTrue(r.advisory)
        self.assertIn(
            r.reason,
            {"selection_question", "selection_informational_ask"},
        )

    def test_selection_respects_a_classifier_that_said_advisory(self) -> None:
        r = route(intent="advisory", message="what is this?", selection=True)
        self.assertTrue(r.advisory)
        self.assertEqual(r.reason, "selection_classifier_advisory")

    def test_structure_ask_overrides_a_classifier_that_said_advisory(self) -> None:
        """Adding a section is a mutation even if the classifier disagreed."""
        r = route(intent="advisory", message="add a new section titled Staff Planning")
        self.assertFalse(r.advisory)
        self.assertEqual(r.reason, "structure_ask")

    def test_improve_this_section_beats_classifier_advisory(self) -> None:
        r = route(intent="advisory", message="Improve this section for the RFP.")
        self.assertFalse(r.advisory)
        self.assertEqual(r.reason, "open_tab_edit")

    def test_rewrite_this_section_beats_classifier_advisory(self) -> None:
        r = route(intent="advisory", message="rewrite this section")
        self.assertFalse(r.advisory)
        self.assertEqual(r.reason, "open_tab_edit")

    def test_classifier_advisory_wins_when_ask_is_not_this_section(self) -> None:
        r = route(intent="advisory", message="review the whole proposal against the RFP")
        self.assertTrue(r.advisory)
        self.assertEqual(r.reason, "classifier_advisory")

    def test_informational_ask_overrides_classifier_single_edit(self) -> None:
        r = route(
            intent="single_edit",
            message="what this section about?",
        )
        self.assertTrue(r.advisory)
        self.assertEqual(r.reason, "informational_ask")

    def test_verify_ask_overrides_classifier_single_edit(self) -> None:
        """Fact-check must never rewrite — even when the classifier guesses edit."""
        r = route(
            intent="single_edit",
            message=(
                "for section 3.1 City of Umatilla can you just cross verify if "
                "everything is truth or any fabiracted values??"
            ),
        )
        self.assertTrue(r.advisory)
        self.assertEqual(r.reason, "verify_ask")

    def test_classifier_single_edit_beats_advisory_keywords(self) -> None:
        r = route(intent="single_edit", message="does this meet the RFP?")
        self.assertFalse(r.advisory)
        self.assertEqual(r.reason, "classifier_single_edit")

    def test_classifier_multi_patch_mutates(self) -> None:
        r = route(intent="multi_patch", message="apply those fixes")
        self.assertFalse(r.advisory)
        self.assertEqual(r.reason, "classifier_multi_patch")

    def test_keyword_gate_decides_only_when_classifier_abstains(self) -> None:
        r = route(intent="none", message="rewrite this section")
        self.assertFalse(r.advisory)
        self.assertIn(r.reason, {"keyword_edit", "open_tab_edit"})

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

    def test_improve_pin_question_answers_instead_of_rewriting(self) -> None:
        r = route(
            intent="single_edit",
            message="is this grounded in the knowledge base?",
            improve_pinned=True,
        )
        self.assertTrue(r.advisory)
        self.assertEqual(r.reason, "improve_pin_question")

    def test_improve_pin_change_request_edits_this_tab(self) -> None:
        r = route(
            intent="advisory",
            message="make the opening shorter and add a client quote",
            improve_pinned=True,
        )
        self.assertFalse(r.advisory)
        self.assertEqual(r.reason, "improve_pin_edit")

    def test_improve_pin_default_prompt_edits_this_tab(self) -> None:
        r = route(
            intent="advisory",
            message="Improve this section for the RFP.",
            improve_pinned=True,
        )
        self.assertFalse(r.advisory)
        self.assertEqual(r.reason, "improve_pin_edit")


if __name__ == "__main__":
    unittest.main()
