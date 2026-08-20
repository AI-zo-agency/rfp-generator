"""Fabricated personnel must never survive as Verified in Go/No-Go."""

from __future__ import annotations

import unittest

from app.services.evidence_trust.personnel_grounding import (
    find_known_fabricated_names,
    personnel_claim_failure,
)
from app.services.go_no_go_adjudicator import (
    build_adjudication_payload,
    rows_from_assessments,
)
from app.services.go_no_go_requirements import RfpRequirement


def _hit(file_name: str, content: str) -> dict:
    return {"title": file_name, "content": content}


MASTER = _hit(
    "02_MasterTemplate_OrgStructure_AllTeamBios.pdf",
    "Curt Schultz, Creative Director. 25+ years. Print and brand graphic design. "
    "Sonja Anderson, Agency Director. Ron Comer, Account Manager.",
)


class FabricatedPersonnelGroundingTests(unittest.TestCase):
    def test_brittany_frazier_is_known_fabrication(self) -> None:
        self.assertEqual(
            find_known_fabricated_names(
                "Brittany Frazier documented as Creative Director"
            ),
            ["Brittany Frazier"],
        )

    def test_master_template_does_not_evidence_brittany(self) -> None:
        fail = personnel_claim_failure(
            requirement="Brittany Frazier as Creative Director",
            quote="Curt Schultz, Creative Director. 25+ years.",
            source_text=MASTER["content"],
        )
        self.assertIsNotNone(fail)
        self.assertIn("Brittany Frazier", fail or "")

    def test_curt_schultz_grounded_in_master_template(self) -> None:
        fail = personnel_claim_failure(
            requirement="Curt Schultz as Creative Director",
            quote="Curt Schultz, Creative Director. 25+ years.",
            source_text=MASTER["content"],
        )
        self.assertIsNone(fail)

    def test_invented_rad_s_is_rejected_from_kb_only_rule(self) -> None:
        fail = personnel_claim_failure(
            requirement="Website maintenance, updates, and expansion",
            quote="Rad S. is the Web & Photography lead at zö agency.",
            source_text="Shawn DiCriscio, Web Developer. Specializes in WordPress.",
        )
        self.assertIsNotNone(fail)
        self.assertIn("Rad S", fail or "")

    def test_sonja_middle_initial_matches_roster_in_kb(self) -> None:
        fail = personnel_claim_failure(
            requirement="Program Director/designated liaison role",
            quote="Sonja M. Anderson is the CEO and Agency Director of zö agency.",
            source_text="Sonja M. Anderson, CEO and Agency Director.",
        )
        self.assertIsNone(fail)

    def test_retired_ron_comer_is_not_current_staff(self) -> None:
        from app.services.evidence_trust.personnel_grounding import (
            find_retired_team_names,
            is_retired_team_member,
        )

        self.assertTrue(is_retired_team_member("Ron Comer"))
        self.assertEqual(find_retired_team_names("Ron Comer, Account Manager"), ["Ron Comer"])
        fail = personnel_claim_failure(
            requirement="Public relations professional/account management",
            quote="Ron Comer, Senior Account Manager",
            source_text=MASTER["content"],
        )
        self.assertIsNotNone(fail)
        self.assertIn("retired", (fail or "").casefold())

    def test_requirement_titles_are_not_staffing_claims(self) -> None:
        """PSU live bug: Title Case in criteria was parsed as a person.

        'Oregon Revised Statutes', 'Portland State University', and
        'Service Disabled Veteran' are not roster names. Treating them as
        staffing claims flipped verified cert/policy rows into fake gaps.
        """
        source = (
            "zö agency is WBENC and WOSB certified as a women-owned business. "
            "We comply with applicable public contracting policies."
        )
        for requirement in (
            "Minority, Women, Service Disabled Veteran Owned, or Emerging Small Business certification",
            "Compliance with Oregon Revised Statutes (ORS) and PSU policies",
            "Ability to work with Portland State University and Oregon public universities",
            "Equal opportunity employment practices (non-discrimination)",
        ):
            fail = personnel_claim_failure(
                requirement=requirement,
                quote="zö agency is WBENC and WOSB certified as a women-owned business.",
                source_text=source,
            )
            self.assertIsNone(fail, requirement)

    def test_adjudicator_rejects_brittany_verified_against_master_template(
        self,
    ) -> None:
        """Live bug: Verified + MasterTemplate while Drew Stone scrub fired next row."""
        reqs = [
            RfpRequirement(
                requirement="Brittany Frazier documented as Creative Director",
                isCore=True,
            ),
            RfpRequirement(requirement="Project Lead", isCore=True),
        ]
        hits = {
            "Brittany Frazier documented as Creative Director": [MASTER],
            "Project Lead": [MASTER],
        }
        _body, sources, _full = build_adjudication_payload(reqs, hits)
        rows, rejected, _rec = rows_from_assessments(
            reqs,
            [
                {
                    "requirement": "Brittany Frazier documented as Creative Director",
                    "status": "verified",
                    "kbSource": "02_MasterTemplate_OrgStructure_AllTeamBios.pdf",
                    "quote": "Curt Schultz, Creative Director. 25+ years.",
                },
                {
                    "requirement": "Project Lead",
                    "status": "gap",
                    "evidenceState": "absent",
                    "reason": "no named lead in roster for this RFP",
                },
            ],
            sources,
        )
        brittany = next(r for r in rows if "Brittany" in r.requirement)
        self.assertEqual(brittany.status, "gap")
        self.assertIn("fabricated personnel", brittany.downgrade_reason.casefold())
        self.assertTrue(rejected)

    def test_scrub_removes_brittany_from_report_like_drew_stone(self) -> None:
        from app.services.go_no_go_service import _scrub_invented_eval_and_people

        raw = {
            "summary": "Brittany Frazier documented as Creative Director — Verified.",
            "stageOneReport": (
                "Creative Director: Brittany Frazier (02_MasterTemplate).\n"
                "Project Lead: [unverified name removed]."
            ),
            "fitScore": 3,
            "worthScore": 4,
            "recommendation": "review",
            "criticalGaps": [],
            "decisionMatrix": [
                {
                    "dimension": "Resource Availability",
                    "score": 3,
                    "notes": "Brittany Frazier Creative Director Verified",
                }
            ],
        }
        _scrub_invented_eval_and_people(raw, evaluation_points_found=True)
        blob = f"{raw['summary']}\n{raw['stageOneReport']}\n{raw['decisionMatrix'][0]['notes']}"
        self.assertNotIn("Brittany Frazier", blob)
        gaps = " | ".join(str(g) for g in raw["criticalGaps"])
        self.assertIn("Brittany Frazier", gaps)
        self.assertIn("not a documented zö team member", gaps)

    def test_scrub_removes_retired_ron_comer_from_report(self) -> None:
        from app.services.go_no_go_service import _scrub_invented_eval_and_people

        raw = {
            "summary": "Strong account management — Ron Comer as Senior AM.",
            "stageOneReport": "Public relations: Ron Comer, Senior Account Manager.",
            "fitScore": 3,
            "worthScore": 4,
            "recommendation": "review",
            "criticalGaps": [],
            "decisionMatrix": [
                {"dimension": "Resource Availability", "score": 3, "notes": "Ron Comer"}
            ],
        }
        _scrub_invented_eval_and_people(raw, evaluation_points_found=True)
        blob = f"{raw['summary']}\n{raw['stageOneReport']}\n{raw['decisionMatrix'][0]['notes']}"
        self.assertNotIn("Ron Comer", blob)
        gaps = " | ".join(str(g) for g in raw["criticalGaps"])
        self.assertIn("Ron Comer", gaps)
        self.assertIn("retired", gaps.casefold())


class EnforceFabricatedCapabilityRowTests(unittest.TestCase):
    def test_enforce_downgrades_brittany_verified_row(self) -> None:
        from app.models.go_no_go import (
            GoNoGoAnalysis,
            GoNoGoCapabilityRow,
            GoNoGoDecisionMatrixRow,
            GoNoGoDimension,
        )
        from app.services.go_no_go_service import _enforce_capability_evidence

        def dim():
            return GoNoGoDimension(summary="ok", scoreImpact="neutral", flags=[])

        analysis = GoNoGoAnalysis(
            summary="ok",
            recommendation="review",
            fitScore=3,
            worthScore=4,
            scopeMatch=dim(),
            sectorMatch=dim(),
            compliance=dim(),
            teamMatch=dim(),
            capabilityMatrix=[
                GoNoGoCapabilityRow(
                    requirement="Brittany Frazier documented as Creative Director",
                    status="verified",
                    kbSource="02_MasterTemplate_OrgStructure_AllTeamBios.pdf",
                    evidence="Curt Schultz, Creative Director",
                    isCore=True,
                )
            ],
            decisionMatrix=[
                GoNoGoDecisionMatrixRow(
                    dimension="Technical Capability Match", score=3, notes=""
                ),
                GoNoGoDecisionMatrixRow(
                    dimension="Resource Availability", score=3, notes=""
                ),
                GoNoGoDecisionMatrixRow(
                    dimension="Financial Viability", score=4, notes=""
                ),
                GoNoGoDecisionMatrixRow(
                    dimension="Strategic Value", score=3, notes=""
                ),
                GoNoGoDecisionMatrixRow(
                    dimension="Win Probability", score=2, notes=""
                ),
            ],
        )
        out = _enforce_capability_evidence(analysis, [])
        self.assertEqual(out.capability_matrix[0].status, "gap")
        self.assertIn("Brittany Frazier", out.capability_matrix[0].downgrade_reason)


if __name__ == "__main__":
    unittest.main()
