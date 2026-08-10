"""Section-scoped revise/patch asks must never fan out proposal-wide.

Reported: 'check rfp what to add in that' ran a fabrication purge on every tab.
Guarantee: revise / patch / fix THIS or THAT section → single-tab only.
"""

from __future__ import annotations

import unittest

from app.services.proposal_chat_ops import (
    chat_ask_is_proposal_wide,
    chat_ask_is_section_scoped,
    classify_chat_op,
    coerce_chat_intent_for_scope,
)


class ChatAskScopeTests(unittest.TestCase):
    def test_revise_this_section_is_scoped(self) -> None:
        self.assertTrue(
            chat_ask_is_section_scoped("revise the opening of this section only")
        )
        self.assertTrue(chat_ask_is_section_scoped("patch that paragraph"))
        self.assertTrue(
            chat_ask_is_section_scoped(
                "can you check rfp what to add in that and add a designer note?"
            )
        )
        self.assertFalse(chat_ask_is_proposal_wide("revise this section"))

    def test_explicit_whole_proposal_is_wide(self) -> None:
        self.assertTrue(
            chat_ask_is_proposal_wide("apply these fixes across the proposal")
        )
        self.assertTrue(chat_ask_is_proposal_wide("check duplicates thoroughly"))
        self.assertTrue(
            chat_ask_is_proposal_wide("remove fabricated content from every section")
        )

    def test_section_local_never_triggers_chat_ops(self) -> None:
        self.assertEqual(
            classify_chat_op(
                "is anything left to add in that?? check rfp cross verify "
                "what to add in that and add designer note"
            ),
            "none",
        )
        self.assertEqual(
            classify_chat_op("revise this section and check against the rfp"),
            "none",
        )

    def test_multi_patch_downgrades_when_section_scoped(self) -> None:
        intent, reason = coerce_chat_intent_for_scope(
            "multi_patch",
            "revise the challenge paragraph in this section",
        )
        self.assertEqual(intent, "single_edit")
        self.assertIn("section", reason)

    def test_multi_patch_kept_when_explicitly_wide(self) -> None:
        intent, reason = coerce_chat_intent_for_scope(
            "multi_patch",
            "apply these fixes across the proposal",
        )
        self.assertEqual(intent, "multi_patch")
        self.assertEqual(reason, "unchanged")


if __name__ == "__main__":
    unittest.main()
