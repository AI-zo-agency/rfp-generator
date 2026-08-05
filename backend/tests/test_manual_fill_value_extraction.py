"""MANUAL FILL values must come from what the user typed, not regex artefacts.

Observed live on RFP manual-8d94fe76 section 3.3. The message

    "Rewrite this case study from the source document in the knowledge base."

was treated as a literal fill value and written into all three MANUAL FILL tags
as "e study from the source document in the knowledge base" — sliced mid-word,
because the connector alternation `with|to|as` carried no word boundaries and
matched the "as" inside "case".

The same flaw hit ordinary instructions:
    "please use the case study in the KB"   -> "e study in the KB"    (c-as-e)
    "resolve these from the knowledge base" -> "e"                    (b-as-e)
    "fill this in based on the case study"  -> "ed on the case study" (b-as-ed)

Each silently corrupted a section of the manuscript.
"""

from __future__ import annotations

import unittest

from app.services.proposal_manual_flags import (
    MANUAL_FILL_TAG_RE,
    _USER_IS_VALUE_RE,
    _USER_WITH_VALUE_RE,
    _is_plausible_fill_value,
)


class ConnectorWordBoundaryTests(unittest.TestCase):
    """Instructions that merely *contain* "as"/"to" must not yield a value."""

    CORRUPTING_MESSAGES = (
        "Rewrite this case study from the source document in the knowledge base.",
        "please use the case study in the KB",
        "resolve these from the knowledge base",
        "fill this in based on the case study",
        "fill the gaps using the case studies",
        "resolve this from the database",
    )

    def test_ordinary_instructions_yield_no_value(self) -> None:
        for msg in self.CORRUPTING_MESSAGES:
            with self.subTest(msg=msg):
                m = _USER_WITH_VALUE_RE.search(msg)
                value = m.group(1) if m else None
                self.assertIsNone(
                    value,
                    f"instruction was mistaken for a fill value: {value!r}",
                )


class RealValuesStillExtractTests(unittest.TestCase):
    """The feature must keep working for genuine 'fill X with Y' asks."""

    def test_fill_with_value(self) -> None:
        m = _USER_WITH_VALUE_RE.search("fill the title with Director of Marketing")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "Director of Marketing")

    def test_tag_text_is_not_scanned_for_the_value(self) -> None:
        """The ":" inside a tag must not out-rank the real "with".

        Leftmost-match would otherwise capture "Title] with Director of Marketing".
        _user_supplied_value_for_tag strips bracketed tags before this runs.
        """
        stripped = MANUAL_FILL_TAG_RE.sub(
            " ", "fill [MANUAL FILL: Title] with Director of Marketing"
        )
        m = _USER_WITH_VALUE_RE.search(stripped)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "Director of Marketing")

    def test_set_to_value(self) -> None:
        m = _USER_WITH_VALUE_RE.search("set the FEIN to 84-1234567")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "84-1234567")

    def test_colon_form(self) -> None:
        m = _USER_WITH_VALUE_RE.search("fill signatory: Sonja Anderson")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "Sonja Anderson")

    def test_is_form(self) -> None:
        m = _USER_IS_VALUE_RE.search("the authorized signatory is Sonja Anderson")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "Sonja Anderson")


class PlausibilityGuardTests(unittest.TestCase):
    """Defence in depth: even if a regex slips, fragments never reach the draft."""

    def test_single_character_is_rejected(self) -> None:
        self.assertFalse(_is_plausible_fill_value("e"))

    def test_empty_and_whitespace_rejected(self) -> None:
        self.assertFalse(_is_plausible_fill_value(""))
        self.assertFalse(_is_plausible_fill_value("   "))

    def test_bare_stopword_rejected(self) -> None:
        self.assertFalse(_is_plausible_fill_value("the"))
        self.assertFalse(_is_plausible_fill_value("these"))

    def test_value_containing_a_tag_rejected(self) -> None:
        self.assertFalse(_is_plausible_fill_value("[MANUAL FILL: Title]"))

    def test_real_values_accepted(self) -> None:
        self.assertTrue(_is_plausible_fill_value("Director of Marketing"))
        self.assertTrue(_is_plausible_fill_value("84-1234567"))
        self.assertTrue(_is_plausible_fill_value("Sonja Anderson"))


if __name__ == "__main__":
    unittest.main()
