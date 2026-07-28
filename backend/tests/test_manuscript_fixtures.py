"""Regression fixture loader smoke tests (T1.1)."""

from __future__ import annotations

import re
import unittest

from tests.fixtures.manuscripts.loader import FIXTURE_NAMES, load_fixture

TRUNCATION_FRAGMENT = "Total Year 1 client invoicing: $325,242.66. 66 ($325,242."
MID_SENTENCE_GARBAGE = "Full resumes and bio summaries for each named"
FLAG_FRAGMENT = "FLAG FOR SONJA"


class ManuscriptFixtureTests(unittest.TestCase):
    def test_every_fixture_loads(self) -> None:
        for name in FIXTURE_NAMES:
            with self.subTest(name=name):
                draft, research, rfp, expected = load_fixture(name)
                self.assertTrue(draft.sections)
                self.assertTrue(rfp.id)
                self.assertIsInstance(expected, dict)

    def test_every_expected_findings_parses(self) -> None:
        for name in FIXTURE_NAMES:
            with self.subTest(name=name):
                _, _, _, expected = load_fixture(name)
                self.assertIn("critical", expected)
                self.assertIn("warning", expected)
                self.assertIsInstance(expected["critical"], list)
                self.assertIsInstance(expected["warning"], list)

    def test_gsu_has_two_year_claims_for_same_person(self) -> None:
        draft, _, _, expected = load_fixture("gsu_inconsistent_years")
        by_id = {s.id: s.content for s in draft.sections}
        self.assertIn("Ron Comer", by_id["s23"])
        self.assertIn("35+ years", by_id["s23"])
        self.assertIn("Ron Comer", by_id["s30"])
        self.assertIn("38 years", by_id["s30"])
        self.assertIn("years_inconsistency", expected["critical"])
        self.assertIn("invented_staffing_hours", expected["critical"])

    def test_cvvb_v1_investment_framing_in_three_sections(self) -> None:
        draft, _, _, expected = load_fixture("cvvb_v1_duplication_budget")
        framing_sections = [
            s.id for s in draft.sections if "Investment Framing" in s.content
        ]
        self.assertGreaterEqual(len(framing_sections), 3)
        self.assertTrue({"s13", "s14", "s18"}.issubset(set(framing_sections)))
        self.assertIn("boilerplate_duplication", expected["critical"])
        self.assertIn("identical_budget_figures", expected["critical"])

    def test_cvvb_v2_flag_and_truncation(self) -> None:
        draft, _, _, expected = load_fixture("cvvb_v2_truncation_orphan_commission")
        blob = "\n".join(s.content for s in draft.sections)
        self.assertIn(FLAG_FRAGMENT, blob)
        self.assertIn(TRUNCATION_FRAGMENT, blob)
        self.assertIn(MID_SENTENCE_GARBAGE, blob)
        for code in (
            "truncation_numeric_tail",
            "mid_sentence_cutoff",
            "internal_note_leak",
            "orphan_commission",
            "passthrough_mismatch",
        ):
            self.assertIn(code, expected["critical"])

    def test_known_good_has_no_flag_or_garbage(self) -> None:
        for name in ("known_good_clean", "known_good_budget_narrative"):
            with self.subTest(name=name):
                draft, _, _, expected = load_fixture(name)
                blob = "\n".join(s.content for s in draft.sections)
                self.assertNotIn("FLAG FOR", blob)
                self.assertNotIn(MID_SENTENCE_GARBAGE, blob)
                self.assertIsNone(re.search(r"\$[\d,]+\.\d+\.\s+\d+\s+\(\$", blob))
                self.assertEqual(expected["critical"], [])
                self.assertEqual(expected["warning"], [])

    def test_t1_validators_match_fixture_owned_codes(self) -> None:
        """T1 owns note_leak + truncation; budget codes (orphan/passthrough) are W5."""
        from app.services.proposal_t1_validators import scan_all_t1

        draft, _, _, expected = load_fixture("cvvb_v2_truncation_orphan_commission")
        findings = scan_all_t1(draft)
        categories = {f["category"] for f in findings}
        codes = {f["code"] for f in findings}

        self.assertIn("note_leak", categories)
        self.assertIn("truncation", categories)
        self.assertTrue(
            any(c.startswith("t1.note_leak.") for c in codes),
            msg=f"expected note_leak code, got {sorted(codes)}",
        )
        self.assertTrue(
            any(
                c
                in {
                    "t1.truncation.repeated_token_tail",
                    "t1.truncation.currency_fragment",
                    "t1.truncation.mid_sentence_cutoff",
                }
                for c in codes
            ),
            msg=f"expected truncation codes for fixture defects, got {sorted(codes)}",
        )
        for symptom in (
            "truncation_numeric_tail",
            "mid_sentence_cutoff",
            "internal_note_leak",
        ):
            self.assertIn(symptom, expected["critical"])

        clean, _, _, _ = load_fixture("known_good_clean")
        self.assertEqual(scan_all_t1(clean), [])


if __name__ == "__main__":
    unittest.main()
