"""Compliance candidate retention / disposition tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

DEMO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO_ROOT.parents[1] / "backend"))
sys.path.insert(0, str(DEMO_ROOT))

from compliance_retention import (  # noqa: E402
    candidate_represented,
    dispose_candidates,
    is_sow_performance_candidate,
)
from opportunity_validators import (  # noqa: E402
    accept_repair_if_improved,
    is_advisory_compliance_item,
    is_conditional_compliance_item,
    normalize_compliance_mandatory_flags,
)


class TestComplianceRetention(unittest.TestCase):
    def test_template_fill_when_anchor_missing(self) -> None:
        pack = {
            "langextract": {
                "complianceHits": [
                    {
                        "text": "Submit a completed Offeror Cover Page (PC600).",
                        "sourceText": "Submit a completed Offeror Cover Page (PC600).",
                        "page": 7,
                    },
                    {
                        "text": "Complete Payment Schedule for all job titles.",
                        "sourceText": "Complete Payment Schedule for all job titles.",
                        "page": 8,
                    },
                ]
            },
            "obligationCandidates": [],
            "sections": {},
        }
        cleaned = {"compliance": {"items": [], "confidence": 0.5}}
        fixes, stats = dispose_candidates(cleaned, pack)
        items = cleaned["compliance"]["items"]
        self.assertTrue(any(str(i["id"]).startswith("comp-anchor-") for i in items))
        blob = " ".join(i["requirement"] for i in items).casefold()
        self.assertIn("pc600", blob)
        self.assertFalse(any(str(i["id"]).startswith("comp-lx-") for i in items))
        self.assertGreaterEqual(float(stats["retentionRate"]), 0.9)
        self.assertTrue(any("anchor_template" in f for f in fixes))

    def test_does_not_dump_lx_when_sonnet_covers(self) -> None:
        pack = {
            "langextract": {
                "complianceHits": [
                    {
                        "text": "Submit a completed and signed Offeror's Cover Page (PC600) form.",
                        "sourceText": "Submit a completed and signed Offeror's Cover Page (PC600) form.",
                        "page": 7,
                    },
                    {
                        "text": "To see all information Offerors must select Participate in RFx in BuyNet.",
                        "sourceText": "To see all information Offerors must select Participate in RFx in BuyNet.",
                        "page": 1,
                    },
                ]
            },
            "obligationCandidates": [],
            "sections": {},
        }
        cleaned = {
            "compliance": {
                "items": [
                    {
                        "id": "comp-38",
                        "requirement": "Submit a completed and signed Offeror's Cover Page (PC600) form.",
                        "mandatory": True,
                    }
                ],
                "confidence": 0.9,
            }
        }
        dispose_candidates(cleaned, pack)
        ids = [i["id"] for i in cleaned["compliance"]["items"]]
        self.assertEqual(ids.count("comp-38"), 1)
        self.assertFalse(any(str(i).startswith("comp-lx-") for i in ids))

    def test_strips_prior_comp_lx(self) -> None:
        pack = {"langextract": {"complianceHits": []}, "obligationCandidates": [], "sections": {}}
        cleaned = {
            "compliance": {
                "items": [
                    {"id": "comp-1", "requirement": "Submit PC600.", "mandatory": True},
                    {
                        "id": "comp-lx-70",
                        "requirement": "Duplicate Participate in RFx...",
                        "mandatory": True,
                    },
                ]
            }
        }
        dispose_candidates(cleaned, pack)
        ids = [i["id"] for i in cleaned["compliance"]["items"]]
        self.assertNotIn("comp-lx-70", ids)

    def test_sow_contractor_not_materialized(self) -> None:
        text = "Contractor shall develop a Master Messaging Document on SharePoint."
        self.assertTrue(is_sow_performance_candidate(text))
        pack = {
            "langextract": {"complianceHits": [{"text": text, "sourceText": text, "page": 15}]},
            "obligationCandidates": [],
            "sections": {},
        }
        cleaned = {"compliance": {"items": [], "confidence": 0.5}}
        dispose_candidates(cleaned, pack)
        self.assertEqual(cleaned["compliance"]["items"], [])
        ledger = pack["_candidateDisposition"]["dispositions"]
        self.assertTrue(any(d.get("disposition") == "classified_as_scope" for d in ledger))

    def test_represented_by_form_code(self) -> None:
        cand = {"text": "Provide PC610 insurance certificate with quote.", "page": 1}
        items = [{"requirement": "Attach PC610 as required.", "mandatory": True}]
        self.assertTrue(candidate_represented(cand, items))


class TestConditionality(unittest.TestCase):
    def test_notes_conditional_forces_false(self) -> None:
        item = {
            "requirement": "Mark packaging with RFQ number.",
            "mandatory": True,
            "notes": "Conditional on using alternate submission method",
        }
        self.assertTrue(is_conditional_compliance_item(item))
        cleaned = {"compliance": {"items": [item]}}
        normalize_compliance_mandatory_flags(cleaned)
        self.assertFalse(cleaned["compliance"]["items"][0]["mandatory"])

    def test_advised_not_mandatory(self) -> None:
        item = {
            "requirement": "Offerors are advised to regularly check BuyNet for information.",
            "mandatory": True,
        }
        self.assertTrue(is_advisory_compliance_item(item))


class TestRepairGate(unittest.TestCase):
    def test_rejects_collapsed_success_criteria(self) -> None:
        before = {
            "successCriteria": {
                "items": [{"criterion": f"c{i}", "why": "x", "recurringTheme": True} for i in range(7)]
            },
            "compliance": {"items": [{"id": f"c{i}", "requirement": "x"} for i in range(30)]},
            "scope": {"mandatory": ["a"] * 10, "optional": []},
            "evaluation": {"emphasis": ["Technical merit", "Experience", "Price"]},
        }
        after = {
            "successCriteria": {"items": [{"criterion": "only one", "why": "bad", "recurringTheme": True}]},
            "compliance": before["compliance"],
            "scope": before["scope"],
            "evaluation": before["evaluation"],
        }
        out, notes = accept_repair_if_improved(before, after)
        self.assertEqual(len(out["successCriteria"]["items"]), 7)
        self.assertTrue(any("successCriteria" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
