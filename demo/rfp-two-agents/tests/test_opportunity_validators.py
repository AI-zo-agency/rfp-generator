"""Unit tests for post-LLM opportunity validators (no LLM)."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

DEMO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = DEMO_ROOT.parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(DEMO_ROOT))

from opportunity_validators import (  # noqa: E402
    apply_deterministic_pipeline,
    is_post_award_compliance_item,
    normalize_compliance_mandatory_flags,
    normalize_evaluation_scoring,
    normalize_success_criteria,
)


class TestComplianceMandatory(unittest.TestCase):
    def test_post_award_w9(self) -> None:
        item = {
            "requirement": "W-9 upon selection for award",
            "targetSection": "Post-Award",
            "mandatory": True,
        }
        self.assertTrue(is_post_award_compliance_item(item))
        cleaned = {"compliance": {"items": [item], "confidence": 0.9}}
        normalize_compliance_mandatory_flags(cleaned)
        self.assertFalse(cleaned["compliance"]["items"][0]["mandatory"])

    def test_subcontractor_conditional(self) -> None:
        item = {
            "requirement": "If subcontractors are used, provide resumes.",
            "mandatory": True,
        }
        cleaned = {"compliance": {"items": [item], "confidence": 0.9}}
        normalize_compliance_mandatory_flags(cleaned)
        self.assertFalse(cleaned["compliance"]["items"][0]["mandatory"])


class TestEvaluation(unittest.TestCase):
    def test_no_scoring_evidence(self) -> None:
        cleaned = {
            "evaluation": {
                "scoredResponseForm": True,
                "totalPoints": 100,
                "criteria": [{"name": "x"}],
            }
        }
        pack = {"explicitScoringLikely": False, "langextract": {"evaluationHits": []}}
        normalize_evaluation_scoring(cleaned, pack)
        ev = cleaned["evaluation"]
        self.assertFalse(ev["scoredResponseForm"])
        self.assertIsNone(ev["totalPoints"])
        self.assertEqual(ev["criteria"], [])


class TestSuccessCriteria(unittest.TestCase):
    def test_theme_to_criterion(self) -> None:
        cleaned = {"successCriteria": {"items": [{"theme": "Stormwater expertise", "why": "SOW"}]}}
        normalize_success_criteria(cleaned)
        self.assertEqual(cleaned["successCriteria"]["items"][0]["criterion"], "Stormwater expertise")

    def test_admin_dropped(self) -> None:
        cleaned = {
            "successCriteria": {
                "items": [{"criterion": "Strict adherence to page numbering", "why": ""}]
            }
        }
        normalize_success_criteria(cleaned)
        self.assertEqual(cleaned["successCriteria"]["items"], [])


class TestPipelineMemoryFacts(unittest.TestCase):
    def test_memory_facts_required(self) -> None:
        cleaned = {
            "understanding": {"client": "County of San Diego", "orgType": "County"},
            "compliance": {"items": [], "confidence": 0.5},
            "scope": {
                "mandatory": [],
                "optional": [],
                "futurePhases": [],
                "outOfScope": [],
                "dependencies": [],
                "notes": "",
                "confidence": 0.5,
            },
        }
        pack = {"pageCount": 50, "sections": {"scope_of_work": {"found": False}}}
        out, fixes, _ = apply_deterministic_pipeline(cleaned, pack)
        mf = out["understanding"]["memoryFacts"]
        self.assertEqual(mf.get("clientName"), "County of San Diego")
        self.assertTrue(mf.get("organizationType"))


class TestSanDiegoAssertionsModule(unittest.TestCase):
    def test_empty_json_reports_failures(self) -> None:
        from san_diego_assertions import assert_san_diego_opportunity

        fails = assert_san_diego_opportunity({})
        self.assertGreater(len(fails), 5)


class TestSanDiegoFixture(unittest.TestCase):
    """Full pipeline assertions when RFP_FIXTURE_PDF points at RFQ 13180."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = os.environ.get("RFP_FIXTURE_PDF", "").strip()
        cls.golden = os.environ.get("RFP_GOLDEN_JSON", "").strip()

    def test_fixture_env_documented(self) -> None:
        if not self.fixture:
            self.skipTest("Set RFP_FIXTURE_PDF to County RFQ 13180 PDF for integration assertions")
        self.assertTrue(Path(self.fixture).is_file())

    def test_golden_json_if_present(self) -> None:
        if not self.golden or not Path(self.golden).is_file():
            self.skipTest("Set RFP_GOLDEN_JSON for golden-file regression")
        data = json.loads(Path(self.golden).read_text())
        self.assertIn("understanding", data)
        self.assertIn("memoryFacts", data.get("understanding") or data["understanding"])
        ev = data.get("evaluation") or {}
        self.assertFalse(ev.get("scoredResponseForm", True))
        self.assertIn("provenance", data)


if __name__ == "__main__":
    unittest.main()
