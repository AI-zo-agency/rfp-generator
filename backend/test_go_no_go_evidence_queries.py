"""Unit tests for Go/No-Go evidence-query hygiene (no live Supermemory)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.models.go_no_go import (
    GoNoGoAnalysis,
    GoNoGoDecisionMatrixRow,
    GoNoGoDimension,
)
from app.services.go_no_go_service import (
    RfpContentInfo,
    _annotate_go_no_go_hit,
    _apply_hard_rules,
    _deterministic_evidence_queries,
    _merge_kb_hits_round_robin,
    analysis_activity_note,
)


def _rfp(**kwargs: object) -> SimpleNamespace:
    defaults = {
        "id": "rfp-test",
        "title": "Destination Brand Marketing",
        "client": "Hawaiʻi Tourism Authority",
        "sector": "Tourism",
        "location": "Hawaiʻi",
        "due_date": None,
        "estimated_value": None,
        "pdf_path": None,
        "description": "",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _content(text: str) -> RfpContentInfo:
    return RfpContentInfo(
        pdf_path=None,
        pdf_path_recorded=None,
        pdf_file_missing=False,
        pdf_exists=False,
        pdf_page_count=0,
        pdf_image_only=False,
        pdf_text=text,
        description="",
        substantive_chars=len(text),
        metadata_only=False,
    )


def _dim(summary: str = "ok") -> GoNoGoDimension:
    return GoNoGoDimension(summary=summary, scoreImpact="neutral", flags=[])


class GoNoGoEvidenceQueryTests(unittest.TestCase):
    def test_deterministic_queries_include_fin_won_and_certs(self) -> None:
        rfp = _rfp()
        content = _content(
            "The Offeror must have or must establish an office in Oceania. "
            "MCI meetings are excluded. Destination brand marketing for leisure visitors."
        )
        queries = _deterministic_evidence_queries(rfp, content)  # type: ignore[arg-type]
        blob = " | ".join(queries).casefold()
        self.assertIn("07_fin", blob)
        self.assertIn("06_won", blob)
        self.assertIn("vishal", blob)
        self.assertIn("oceania", blob)
        self.assertIn("san francisco travel", blob)
        self.assertIn("sonja", blob)
        self.assertIn("clientlist", blob)

    def test_health_coalition_rfp_queries_include_rno(self) -> None:
        rfp = _rfp(
            title="Communications and Social Marketing for Georgia State University",
            client="Georgia State University",
            sector="Public Sector",
            location="Georgia",
        )
        content = _content(
            "ARCHI health policy team seeks communications and social marketing. "
            "Coalition-based initiatives, stigma reduction, culturally sensitive messaging."
        )
        queries = _deterministic_evidence_queries(rfp, content)  # type: ignore[arg-type]
        blob = " | ".join(queries).casefold()
        self.assertIn("recovery network", blob)
        self.assertIn("rno", blob)
        self.assertIn("health policy", blob)
        self.assertIn("social marketing", blob)

    def test_annotate_fin_hit(self) -> None:
        hit = {
            "title": "07_FIN_CityofSanLeandro_Proposal.pdf",
            "content": "Resonance prepared Lynchburg Economic Development materials.",
        }
        annotated = _annotate_go_no_go_hit(hit)
        title = str(annotated.get("title") or "")
        self.assertIn("FINALIST/LOSS", title)
        self.assertIn("Resonance", title)

    def test_merge_kb_hits_round_robin_interleaves_queries(self) -> None:
        results = [
            [{"id": "a1"}, {"id": "a2"}],
            [{"id": "b1"}, {"id": "b2"}],
            [{"id": "c1"}],
        ]
        merged = _merge_kb_hits_round_robin(results)
        self.assertEqual(
            [h["id"] for h in merged],
            ["a1", "b1", "c1", "a2", "b2"],
        )

    def test_activity_note_omits_fit_score(self) -> None:
        analysis = GoNoGoAnalysis(
            summary="Strong capability, weak economics.",
            recommendation="review",
            fitScore=4,
            worthScore=2,
            decisionMatrix=[
                GoNoGoDecisionMatrixRow(
                    dimension="Technical Capability Match", score=4, notes="ok"
                ),
                GoNoGoDecisionMatrixRow(
                    dimension="Resource Availability", score=3, notes="ok"
                ),
                GoNoGoDecisionMatrixRow(
                    dimension="Financial Viability", score=2, notes="budget"
                ),
                GoNoGoDecisionMatrixRow(
                    dimension="Strategic Value", score=3, notes="ok"
                ),
                GoNoGoDecisionMatrixRow(
                    dimension="Win Probability", score=2, notes="comp"
                ),
            ],
            scopeMatch=_dim(),
            sectorMatch=_dim(),
            compliance=_dim(),
            teamMatch=_dim(),
            criticalGaps=["Budget undisclosed"],
            conditions=["Verify registration"],
        )
        note = analysis_activity_note(analysis)
        self.assertNotIn("Fit ", note)
        self.assertIn("Worth 2/5", note)
        self.assertIn("Overall 2.8/5", note)

    def test_scrub_removes_persisted_29pt_62pct_table(self) -> None:
        """Exact fabricated table that survived multiple GSU re-runs."""
        raw = {
            "summary": "Win Probability 3/5 due to cost-heavy evaluation (62% of points).",
            "worthScore": 3,
            "recommendation": "review",
            "stageOneReport": (
                "## EXECUTIVE SUMMARY\nok\n\n"
                "## EVALUATION CRITERIA BREAKDOWN\n"
                "Cost 18 points (14+4), Experience 5 points (2+3), Technical Proposal 2 points, "
                "References 4 points, Total 29 points, 62% cost-weighted.\n\n"
                "## FINAL RECOMMENDATION\nGO WITH CONDITIONS\n"
            ),
            "decisionMatrix": [
                {"dimension": "Technical Capability Match", "score": 4, "notes": "RNO"},
                {"dimension": "Resource Availability", "score": 3, "notes": "ok"},
                {
                    "dimension": "Financial Viability",
                    "score": 3,
                    "notes": "cost-heavy evaluation (62% of points)",
                },
                {"dimension": "Strategic Value", "score": 3, "notes": "ok"},
                {
                    "dimension": "Win Probability",
                    "score": 3,
                    "notes": "cost-heavy evaluation (62% of points)",
                },
            ],
            "criticalGaps": [],
            "scopeMatch": {"summary": "ok", "scoreImpact": "", "flags": []},
            "sectorMatch": {"summary": "ok", "scoreImpact": "", "flags": []},
            "compliance": {"summary": "ok", "scoreImpact": "", "flags": []},
            "teamMatch": {"summary": "ok", "scoreImpact": "", "flags": []},
        }
        # Simulate the false-positive path: extractor thought points existed, then we
        # correctly mark unreliable → scrub must still delete the table.
        cleaned = _apply_hard_rules(raw, evaluation_points_found=False)
        report = cleaned["stageOneReport"]
        self.assertIn("not disclosed", report.casefold())
        self.assertNotIn("62%", report)
        self.assertNotIn("29 points", report.casefold())
        self.assertNotIn("14+4", report)
        self.assertNotIn("cost-heavy evaluation (62%", cleaned["summary"])
        for row in cleaned["decisionMatrix"]:
            self.assertNotIn("62%", str(row.get("notes") or ""))

    def test_scrub_invented_eval_weights_bumps_depressed_scores(self) -> None:
        raw = {
            "summary": "Worth 2/5 due to cost evaluation weighted at 62%. Ella Lindeau flagged.",
            "worthScore": 2,
            "recommendation": "review",
            "stageOneReport": (
                "## EXECUTIVE SUMMARY\n"
                "Contract Value: Not disclosed in RFP (only $30 million reference found).\n\n"
                "## EVALUATION CRITERIA BREAKDOWN\n"
                "| Category | Max Points | zö Strength |\n"
                "| COST | 14 points (48%) | ok |\n"
                "| Cost | 4 points (14%) | ok |\n"
                "Total: 29 points - Cost heavily weighted at 62% combined.\n\n"
                "## FINAL RECOMMENDATION\n"
                "GO WITH CONDITIONS — flag Ella Lindeau for registration.\n"
            ),
            "decisionMatrix": [
                {
                    "dimension": "Technical Capability Match",
                    "score": 4,
                    "notes": "RNO match",
                },
                {
                    "dimension": "Resource Availability",
                    "score": 3,
                    "notes": "Drew Stone documented",
                },
                {
                    "dimension": "Financial Viability",
                    "score": 2,
                    "notes": "Cost evaluation weighted at 62% of total points",
                },
                {
                    "dimension": "Strategic Value",
                    "score": 3,
                    "notes": "ok",
                },
                {
                    "dimension": "Win Probability",
                    "score": 2,
                    "notes": "Heavy cost weighting (62%)",
                },
            ],
            "criticalGaps": [
                "Heavy cost weighting (62%) creates price pressure",
            ],
            "actionFlags": ["[FLAG FOR ELLA LINDEAU: Confirm Georgia registration]"],
            "scopeMatch": {"summary": "ok", "scoreImpact": "", "flags": []},
            "sectorMatch": {"summary": "ok", "scoreImpact": "", "flags": []},
            "compliance": {"summary": "ok", "scoreImpact": "", "flags": []},
            "teamMatch": {"summary": "ok", "scoreImpact": "", "flags": []},
        }
        cleaned = _apply_hard_rules(raw, evaluation_points_found=False)
        by_dim = {
            str(row["dimension"]): row for row in cleaned["decisionMatrix"]
        }
        self.assertGreaterEqual(by_dim["Financial Viability"]["score"], 3)
        self.assertGreaterEqual(by_dim["Win Probability"]["score"], 3)
        self.assertEqual(cleaned["worthScore"], 3)
        report = cleaned["stageOneReport"]
        self.assertIn("not disclosed", report.casefold())
        self.assertNotIn("62%", report)
        self.assertNotIn("| COST |", report)
        self.assertNotIn("14 points (48%)", report)
        self.assertNotIn("Total: 29 points", report)
        self.assertNotIn("$30 million reference", report.casefold())
        self.assertIn("unknowable from the RFP text", report)
        self.assertIn("Lindau", report)
        self.assertNotIn("Lindeau", report)
        gaps = " | ".join(str(g) for g in cleaned["criticalGaps"])
        self.assertNotIn("62%", gaps)
        self.assertIn("Drew Stone", gaps)
        self.assertNotIn("Drew Stone", by_dim["Resource Availability"]["notes"])
        flags = " | ".join(str(f) for f in cleaned.get("actionFlags") or [])
        self.assertIn("Lindau", flags)
        self.assertNotIn("Lindeau", flags)


if __name__ == "__main__":
    unittest.main()
