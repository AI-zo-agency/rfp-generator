"""Selection excerpt edit — remove/delete must allow empty replacement."""

from __future__ import annotations

import unittest

from app.services.proposal_section_editor import (
    _heal_selection_join_deterministic,
    _remove_heal_is_safe,
    _selection_asks_to_fill_verify,
    _selection_asks_to_remove,
    _selection_join_looks_broken,
    _selection_replacement_regressed,
    _splice_selection,
)


class SelectionRemoveTests(unittest.TestCase):
    def test_remove_phrasing_detected(self) -> None:
        self.assertTrue(_selection_asks_to_remove("remove this much part only."))
        self.assertTrue(_selection_asks_to_remove("Delete this excerpt"))
        self.assertTrue(_selection_asks_to_remove("cut this out"))
        self.assertFalse(_selection_asks_to_remove("make this warmer and clearer"))

    def test_empty_replacement_ok_when_removing(self) -> None:
        excerpt = "Add a section confirming compliance with KVCC submission requirements."
        self.assertTrue(
            _selection_replacement_regressed(excerpt, "", allow_remove=False)
        )
        self.assertFalse(
            _selection_replacement_regressed(excerpt, "", allow_remove=True)
        )

    def test_join_looks_broken_after_mid_opener_delete(self) -> None:
        before = ""
        after = (
            "compliance with KVCC submission requirements: proposal signed by "
            "person with authority to bind Z'Onion Creative Group LLC."
        )
        self.assertTrue(_selection_join_looks_broken(before, after))
        self.assertFalse(
            _selection_join_looks_broken(before, after[0].upper() + after[1:])
        )

    def test_deterministic_heal_capitalizes_section_start(self) -> None:
        content = (
            "Add a section confirming compliance with KVCC submission requirements: "
            "proposal signed by person with authority."
        )
        start, end = 0, len("Add a section confirming ")
        spliced = _splice_selection(content, start=start, end=end, replacement="")
        self.assertTrue(spliced.startswith("compliance "))
        healed = _heal_selection_join_deterministic(spliced, splice_at=start)
        self.assertTrue(healed.startswith("Compliance "))
        self.assertIn("submission requirements", healed)

    def test_deterministic_heal_capitalizes_after_paragraph_break(self) -> None:
        content = "Intro paragraph.\n\nAdd a section confirming compliance with rules."
        start = content.index("Add a section confirming ")
        end = start + len("Add a section confirming ")
        spliced = _splice_selection(content, start=start, end=end, replacement="")
        healed = _heal_selection_join_deterministic(spliced, splice_at=start)
        self.assertIn("\n\nCompliance with rules.", healed)

    def test_remove_heal_safety_rejects_balloon(self) -> None:
        spliced = "Compliance with KVCC submission requirements."
        balloon = spliced + (" extra claim." * 40)
        self.assertFalse(_remove_heal_is_safe(spliced=spliced, healed=balloon))
        self.assertTrue(
            _remove_heal_is_safe(
                spliced=spliced,
                healed="Compliance with KVCC submission requirements is confirmed.",
            )
        )


class SelectionVerifyFillTests(unittest.TestCase):
    def test_fill_verify_phrasing_detected(self) -> None:
        self.assertTrue(
            _selection_asks_to_fill_verify("fill missing verify tags in insurance")
        )
        self.assertTrue(
            _selection_asks_to_fill_verify(
                "Fill in the missing [VERIFY] tags with KB facts"
            )
        )
        self.assertFalse(
            _selection_asks_to_fill_verify("make this paragraph warmer")
        )

    def test_verify_fill_does_not_regress_when_prose_preserved(self) -> None:
        excerpt = (
            "We carry commercial general liability with limits of "
            "[VERIFY: CGL limit amount] per occurrence."
        )
        replacement = (
            "We carry commercial general liability with limits of "
            "$1,000,000 per occurrence."
        )
        self.assertFalse(
            _selection_replacement_regressed(
                excerpt, replacement, allow_verify_fill=True
            )
        )

    def test_verify_fill_still_rejects_truncated_span(self) -> None:
        excerpt = (
            "Insurance Information\n\n"
            "We maintain coverage through Next Insurance. "
            "General liability: [VERIFY: CGL limit]. "
            "Workers compensation: [VERIFY: WC confirmation]. "
            "Certificates available on request."
        )
        # Model returned only the filled value — must still reject.
        self.assertTrue(
            _selection_replacement_regressed(
                excerpt, "$1,000,000", allow_verify_fill=True
            )
        )


if __name__ == "__main__":
    unittest.main()
