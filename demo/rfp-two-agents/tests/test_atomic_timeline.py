"""Atomic compliance + timeline extraction tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

DEMO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO_ROOT.parents[1] / "backend"))
sys.path.insert(0, str(DEMO_ROOT))

from agent1_tools import RfpDoc  # noqa: E402
from compliance_atomic_split import atomize_compliance_items  # noqa: E402
from timeline_extract import extract_timeline_intel, normalize_rfp_text  # noqa: E402


class TestAtomicSplit(unittest.TestCase):
    def test_verb_list_split(self) -> None:
        items = [
            {
                "id": "c1",
                "requirement": (
                    "Offeror shall provide project start dates, client address, email address, "
                    "telephone number, and description of work performed."
                ),
                "mandatory": True,
            }
        ]
        out, fixes = atomize_compliance_items(items, {})
        self.assertGreaterEqual(len(out), 3)
        self.assertTrue(any("atomize" in f for f in fixes))

    def test_reference_atoms_from_section(self) -> None:
        pack = {
            "boundedSections": {
                "references": {
                    "text": (
                        "Organization Name Address Telephone Number Email Address "
                        "Contact Name Title Relationship Period Description of work performed"
                    )
                }
            }
        }
        items = [
            {
                "id": "r1",
                "requirement": "Provide three professional references with all required information for each reference project.",
                "mandatory": True,
            }
        ]
        out, _ = atomize_compliance_items(items, pack)
        self.assertGreaterEqual(len(out), 4)


class TestTimeline(unittest.TestCase):
    def test_normalize_line_break(self) -> None:
        self.assertIn("questions", normalize_rfp_text("ques-\ntions due"))

    def test_extract_dues(self) -> None:
        pages = [
            "KEY DATES. Questions regarding this RFQ due September 21, 2026 prior to 5:00 p.m.",
            "Sealed Quotes due October 30, 2026 prior to 5:00 p.m. San Diego Time.",
        ]
        doc = RfpDoc(pages=pages)
        tl = extract_timeline_intel(doc, {})
        self.assertIn("questionsDue", tl)
        self.assertIn("quotesDue", tl)
        self.assertIn("September 21, 2026", tl["questionsDue"])


if __name__ == "__main__":
    unittest.main()
