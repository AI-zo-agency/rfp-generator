"""Production-grade edge cases for MANUAL FILL tag preservation helpers (T2.4)."""

from __future__ import annotations

import unittest

from app.services.proposal_common import ProposalError
from app.services.proposal_manual_flags import (
    MANUAL_FILL_TAG_RE,
    extract_manual_fill_tags,
    manual_fill_tags_preserved,
    mask_manual_fill_tags,
    missing_manual_fill_placeholders,
    unmask_manual_fill_tags,
)
from app.services.proposal_section_editor import _unmask_manual_fill_checked


class ManualFillPatternEdgeCases(unittest.TestCase):
    r"""Plan colon-optional form; repo uses FILL\b[^\]]* to also cover or N/A stubs."""

    def test_plan_forms_and_budget_or_na_variants(self) -> None:
        samples = [
            "[MANUAL FILL]",
            "[manual fill]",
            "[MANUAL  FILL]",
            "[MANUAL FILL: street]",
            "[MANUAL FILL: Sonja — confirm FEIN]",
            "[MANUAL FILL or N/A]",
            "[MANUAL FILL OR N/A]",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertRegex(sample, MANUAL_FILL_TAG_RE)
                self.assertEqual(len(extract_manual_fill_tags(sample)), 1)

    def test_does_not_match_verify_or_flag_or_designer_note(self) -> None:
        for sample in (
            "[VERIFY: staffing hours]",
            "[VERIFY]",
            "[FLAG FOR SONJA: add case study]",
            "[DESIGNER NOTE: tighten spacing]",
            "[INTERNAL: do not ship]",
        ):
            with self.subTest(sample=sample):
                self.assertIsNone(MANUAL_FILL_TAG_RE.search(sample))

    def test_does_not_match_partial_words(self) -> None:
        self.assertEqual(extract_manual_fill_tags("MANUAL FILL without brackets"), [])
        self.assertEqual(extract_manual_fill_tags("[MANUAL FILLING: wrong]"), [])


class MaskUnmaskEdgeCases(unittest.TestCase):
    def test_empty_and_none_safe(self) -> None:
        masked, originals = mask_manual_fill_tags("")
        self.assertEqual(masked, "")
        self.assertEqual(originals, [])
        self.assertEqual(unmask_manual_fill_tags("", []), "")
        self.assertEqual(missing_manual_fill_placeholders("", []), [])

    def test_no_tags_is_identity(self) -> None:
        text = "Clean prose with no placeholders."
        masked, originals = mask_manual_fill_tags(text)
        self.assertEqual(masked, text)
        self.assertEqual(originals, [])
        self.assertEqual(unmask_manual_fill_tags(masked, originals), text)

    def test_identical_duplicate_tags_preserve_both(self) -> None:
        text = "A [MANUAL FILL] then again [MANUAL FILL]."
        masked, originals = mask_manual_fill_tags(text)
        self.assertEqual(originals, ["[MANUAL FILL]", "[MANUAL FILL]"])
        self.assertIn("«MFILL_0»", masked)
        self.assertIn("«MFILL_1»", masked)
        self.assertEqual(unmask_manual_fill_tags(masked, originals), text)

    def test_adjacent_tags(self) -> None:
        text = "[MANUAL FILL][MANUAL FILL: next]"
        masked, originals = mask_manual_fill_tags(text)
        self.assertEqual(len(originals), 2)
        self.assertEqual(unmask_manual_fill_tags(masked, originals), text)

    def test_section_draft_stub_is_not_protected_on_rewrite(self) -> None:
        from app.services.proposal_manual_flags import (
            is_section_draft_stub_manual_fill,
            strip_section_draft_stub_manual_fills,
        )
        from app.services.proposal_section_editor import _mask_manual_fill_for_rewrite

        stub = (
            "[MANUAL FILL: Draft this RFP-required section — "
            "Understanding of Island County and Tourism Context]"
        )
        self.assertTrue(is_section_draft_stub_manual_fill(stub))
        body = (
            f"## Understanding of Island County and Tourism Context\n\n{stub}\n\n"
            "RFP-required outline:\n- Whidbey Island\n"
        )
        cleaned = strip_section_draft_stub_manual_fills(body)
        self.assertNotIn("MANUAL FILL", cleaned)
        self.assertIn("Whidbey Island", cleaned)

        masked, originals = _mask_manual_fill_for_rewrite(body)
        self.assertEqual(originals, [])
        self.assertNotIn("«MFILL_", masked)
        self.assertNotIn("Draft this RFP-required section", masked)

        # Real fact placeholders still protected.
        mixed = body + "\nAddress: [MANUAL FILL: street]\n"
        masked2, originals2 = _mask_manual_fill_for_rewrite(mixed)
        self.assertEqual(originals2, ["[MANUAL FILL: street]"])
        self.assertIn("«MFILL_0»", masked2)
        self.assertNotIn("Draft this RFP-required section", masked2)

    def test_tags_inside_table_cells(self) -> None:
        text = (
            "| Field | Value |\n"
            "| ----- | ----- |\n"
            "| Title | [MANUAL FILL] |\n"
            "| Fax | [MANUAL FILL or N/A] |\n"
        )
        masked, originals = mask_manual_fill_tags(text)
        self.assertEqual(len(originals), 2)
        self.assertNotIn("[MANUAL FILL", masked)
        self.assertEqual(unmask_manual_fill_tags(masked, originals), text)

    def test_unicode_and_em_dash_in_description(self) -> None:
        text = "Contact: [MANUAL FILL: Sonja — confirm FEIN before ship]"
        masked, originals = mask_manual_fill_tags(text)
        self.assertEqual(unmask_manual_fill_tags(masked, originals), text)

    def test_multiline_content_with_scattered_tags(self) -> None:
        text = (
            "Intro paragraph.\n\n"
            "Address: [MANUAL FILL: street]\n\n"
            "Closing with [MANUAL FILL or N/A] at end."
        )
        masked, originals = mask_manual_fill_tags(text)
        self.assertEqual(len(originals), 2)
        self.assertEqual(unmask_manual_fill_tags(masked, originals), text)

    def test_unknown_placeholder_index_left_intact(self) -> None:
        # Model invents «MFILL_9» — leave it; do not IndexError.
        out = unmask_manual_fill_tags("x «MFILL_9» y", ["[MANUAL FILL]"])
        self.assertIn("«MFILL_9»", out)

    def test_missing_when_placeholder_and_raw_tag_both_gone(self) -> None:
        originals = ["[MANUAL FILL: Title]", "[MANUAL FILL]"]
        missing = missing_manual_fill_placeholders(
            "Model rewrote without any markers", originals
        )
        self.assertEqual(missing, originals)

    def test_not_missing_when_raw_tag_restored_by_model(self) -> None:
        originals = ["[MANUAL FILL: Title]"]
        missing = missing_manual_fill_placeholders(
            "Title stays [MANUAL FILL: Title] verbatim", originals
        )
        self.assertEqual(missing, [])


class PreservationComparisonEdgeCases(unittest.TestCase):
    def test_preserved_when_no_tags_in_before(self) -> None:
        self.assertTrue(manual_fill_tags_preserved("clean", "also clean"))

    def test_preserved_when_all_tags_remain(self) -> None:
        before = "A [MANUAL FILL] B [MANUAL FILL: x]"
        after = "Rewritten A [MANUAL FILL] B [MANUAL FILL: x] trailing."
        self.assertTrue(manual_fill_tags_preserved(before, after))

    def test_not_preserved_when_one_tag_dropped(self) -> None:
        before = "A [MANUAL FILL] B [MANUAL FILL: x]"
        after = "A [MANUAL FILL] B was filled in"
        self.assertFalse(manual_fill_tags_preserved(before, after))

    def test_not_preserved_when_tag_text_mutated(self) -> None:
        before = "Sign: [MANUAL FILL: wet/digital signature]"
        after = "Sign: [MANUAL FILL: signature]"
        self.assertFalse(manual_fill_tags_preserved(before, after))

    def test_empty_after_with_tags_in_before(self) -> None:
        self.assertFalse(manual_fill_tags_preserved("[MANUAL FILL]", ""))


class RewriteHardFailEdgeCases(unittest.TestCase):
    def test_unmask_checked_raises_when_placeholders_dropped(self) -> None:
        originals = ["[MANUAL FILL]", "[MANUAL FILL or N/A]"]
        with self.assertRaises(ProposalError) as ctx:
            _unmask_manual_fill_checked(
                "Model dropped every placeholder",
                originals,
                attempt=1,
            )
        self.assertIn("MANUAL FILL", str(ctx.exception))

    def test_unmask_checked_succeeds_when_all_placeholders_present(self) -> None:
        originals = ["[MANUAL FILL]", "[MANUAL FILL or N/A]"]
        restored = _unmask_manual_fill_checked(
            "Keep «MFILL_0» and «MFILL_1» here.",
            originals,
            attempt=0,
        )
        self.assertEqual(
            restored, "Keep [MANUAL FILL] and [MANUAL FILL or N/A] here."
        )

    def test_unmask_checked_allows_raw_tag_instead_of_placeholder(self) -> None:
        originals = ["[MANUAL FILL: Title]"]
        restored = _unmask_manual_fill_checked(
            "Role: [MANUAL FILL: Title]",
            originals,
            attempt=0,
        )
        self.assertEqual(restored, "Role: [MANUAL FILL: Title]")


if __name__ == "__main__":
    unittest.main()
