"""Capability claims must be backed by KB documents actually retrieved.

Cases below are the real fabrications the tool emitted for a municipal website
RFP: CMS implementation, hosting, and content migration reported "Verified"
with nothing in the KB, plus "content development" cited to evidence "content
migration" — a real document cited for a capability it does not support.
"""

from __future__ import annotations

import unittest

from app.models.go_no_go import GoNoGoCapabilityRow
from app.services.go_no_go_capability import (
    derive_technical_capability_score,
    unverified_core_requirements,
    validate_capability_rows,
)


def _hit(file_name: str, content: str) -> dict:
    return {"title": file_name, "content": content}


# What the KB actually contains for this RFP.
KB_HITS = [
    _hit(
        "03_CS_TorrentLaboratories.pdf",
        "Website redesign and brand refresh for Torrent Laboratories, a private "
        "diagnostics company. Responsive design and copywriting.",
    ),
    _hit(
        "03_CS_CityOfBend.pdf",
        "Branding, signage, template library and stakeholder engagement for the "
        "City of Bend. Content development for campaign guides.",
    ),
    _hit(
        "04_Bio_ShawnDiCriscio.pdf",
        "Shawn DiCriscio, web developer. 10+ years building and maintaining "
        "WordPress websites for small and mid-size organizations.",
    ),
]


def _row(requirement: str, status: str, source: str, core: bool = True):
    return GoNoGoCapabilityRow(
        requirement=requirement, status=status, kbSource=source, isCore=core
    )


def _adjudicated_gap(requirement: str, reason: str):
    """A row already judged and rejected by the adjudicator."""
    return GoNoGoCapabilityRow(
        requirement=requirement, status="gap", isCore=True, downgradeReason=reason
    )


class CapabilityValidationTests(unittest.TestCase):
    def test_fabricated_source_is_downgraded(self) -> None:
        rows = [_row("CMS implementation", "verified", "03_CS_MunicipalCMS.pdf")]

        out, msgs = validate_capability_rows(rows, KB_HITS)

        self.assertEqual(out[0].status, "unverified")
        self.assertIn("not retrieved", out[0].downgrade_reason)
        self.assertEqual(len(msgs), 1)

    def test_verified_with_no_citation_is_downgraded(self) -> None:
        rows = [_row("Hosting and maintenance", "verified", "")]

        out, _ = validate_capability_rows(rows, KB_HITS)

        self.assertEqual(out[0].status, "unverified")
        self.assertIn("no KB source", out[0].downgrade_reason)

    def test_real_document_cited_for_wrong_capability_is_downgraded(self) -> None:
        """'content development' must not evidence 'content migration'."""
        rows = [_row("Content migration", "verified", "03_CS_CityOfBend.pdf")]

        out, _ = validate_capability_rows(rows, KB_HITS)

        self.assertEqual(out[0].status, "unverified")
        self.assertIn("does not evidence", out[0].downgrade_reason)

    def test_genuine_match_survives(self) -> None:
        rows = [_row("WordPress website development", "verified",
                     "04_Bio_ShawnDiCriscio.pdf")]

        out, msgs = validate_capability_rows(rows, KB_HITS)

        self.assertEqual(out[0].status, "verified")
        self.assertEqual(msgs, [])

    def test_loose_citation_still_resolves(self) -> None:
        rows = [_row("Website redesign", "verified", "03_CS Torrent Laboratories")]

        out, _ = validate_capability_rows(rows, KB_HITS)

        self.assertEqual(out[0].status, "verified")

    def test_gap_rows_pass_through_untouched(self) -> None:
        rows = [_row("GIS mapping", "gap", "")]

        out, msgs = validate_capability_rows(rows, KB_HITS)

        self.assertEqual(out[0].status, "gap")
        self.assertEqual(msgs, [])

    def test_core_gaps_are_reported(self) -> None:
        rows, _ = validate_capability_rows(
            [
                _row("CMS implementation", "verified", "03_CS_MunicipalCMS.pdf"),
                _row("Hosting", "verified", ""),
                _row("WordPress website development", "verified",
                     "04_Bio_ShawnDiCriscio.pdf"),
            ],
            KB_HITS,
        )

        gaps = unverified_core_requirements(rows)

        self.assertIn("CMS implementation", gaps)
        self.assertIn("Hosting", gaps)
        self.assertNotIn("WordPress website development", gaps)


class TechnicalScoreTests(unittest.TestCase):
    def test_all_core_unverified_scores_zero(self) -> None:
        rows = [
            _row("CMS implementation", "unverified", ""),
            _row("Hosting", "unverified", ""),
            _row("Content migration", "unverified", ""),
        ]
        self.assertEqual(derive_technical_capability_score(rows), 0)

    def test_all_verified_scores_five(self) -> None:
        rows = [
            _row("CMS implementation", "verified", "x"),
            _row("Hosting", "verified", "x"),
        ]
        self.assertEqual(derive_technical_capability_score(rows), 5)

    def test_core_gaps_outweigh_peripheral_matches(self) -> None:
        rows = [
            _row("CMS implementation", "unverified", "", core=True),
            _row("Hosting", "unverified", "", core=True),
            _row("Copywriting", "verified", "x", core=False),
            _row("Brand design", "verified", "x", core=False),
        ]
        score = derive_technical_capability_score(rows)
        self.assertIsNotNone(score)
        self.assertLessEqual(score, 2)

    def test_technical_score_ignores_role_gaps_when_craft_is_evidenced(self) -> None:
        """Staffing gaps must not drag Technical to 1 while WordPress craft is verified."""
        rows = [
            GoNoGoCapabilityRow(
                requirement="WordPress CMS implementation",
                status="verified",
                isCore=True,
                category="technical",
                evidence="Specializes in WordPress",
            ),
            GoNoGoCapabilityRow(
                requirement="Website redesign",
                status="verified",
                isCore=True,
                category="service",
            ),
            GoNoGoCapabilityRow(
                requirement="Responsive design",
                status="partial",
                isCore=True,
                category="technical",
            ),
            GoNoGoCapabilityRow(
                requirement="ADA/WCAG audit",
                status="gap",
                isCore=True,
                category="technical",
            ),
            GoNoGoCapabilityRow(
                requirement="Hosting / SLA",
                status="gap",
                isCore=True,
                category="technical",
            ),
            GoNoGoCapabilityRow(
                requirement="Project manager",
                status="gap",
                isCore=True,
                category="role",
            ),
            GoNoGoCapabilityRow(
                requirement="UX designer",
                status="gap",
                isCore=True,
                category="role",
            ),
            GoNoGoCapabilityRow(
                requirement="CA office presence",
                status="gap",
                isCore=True,
                category="logistics",
            ),
        ]
        tech = derive_technical_capability_score(rows)
        gaps = unverified_core_requirements(rows)
        self.assertIsNotNone(tech)
        self.assertGreaterEqual(tech or 0, 3)
        self.assertNotIn("Project manager", gaps)
        self.assertNotIn("UX designer", gaps)
        self.assertIn("Hosting / SLA", gaps)


class CalibratedTechnicalScoreTests(unittest.TestCase):
    """Strong campaign/comms proof should not collapse to 2/5."""

    def test_communications_rfp_calibrates_to_three_or_higher(self) -> None:
        from app.services.go_no_go_capability import calibrate_technical_capability_score

        rows = [
            GoNoGoCapabilityRow(
                requirement="Strategic communications planning",
                status="verified",
                isCore=True,
                category="service",
            ),
            GoNoGoCapabilityRow(
                requirement="Bilingual public health outreach",
                status="verified",
                isCore=True,
                category="service",
            ),
            GoNoGoCapabilityRow(
                requirement="Media buying and placement",
                status="partial",
                isCore=True,
                category="service",
            ),
            GoNoGoCapabilityRow(
                requirement="Polling-based impact evaluation",
                status="gap",
                isCore=True,
                category="service",
            ),
            GoNoGoCapabilityRow(
                requirement="Toolkit development for partners",
                status="gap",
                isCore=True,
                category="service",
            ),
            GoNoGoCapabilityRow(
                requirement="Media buyer specialist",
                status="gap",
                isCore=True,
                category="role",
            ),
        ]
        raw = derive_technical_capability_score(rows)
        calibrated = calibrate_technical_capability_score(rows)
        self.assertIsNotNone(raw)
        self.assertLessEqual(raw or 0, 3)
        self.assertGreaterEqual(calibrated or 0, 3)

    def test_three_plus_core_craft_gaps_cannot_calibrate_to_four(self) -> None:
        from app.services.go_no_go_capability import calibrate_technical_capability_score

        rows = [
            GoNoGoCapabilityRow(
                requirement="Digital display", status="verified", isCore=True, category="service"
            ),
            GoNoGoCapabilityRow(
                requirement="Video", status="verified", isCore=True, category="service"
            ),
            GoNoGoCapabilityRow(
                requirement="Analytics", status="verified", isCore=True, category="service"
            ),
            GoNoGoCapabilityRow(
                requirement="Social", status="partial", isCore=True, category="service"
            ),
            GoNoGoCapabilityRow(
                requirement="CTV", status="gap", isCore=True, category="service"
            ),
            GoNoGoCapabilityRow(
                requirement="Billboard", status="gap", isCore=True, category="service"
            ),
            GoNoGoCapabilityRow(
                requirement="Website troubleshooting", status="gap", isCore=True, category="technical"
            ),
        ]
        calibrated = calibrate_technical_capability_score(rows)
        self.assertLessEqual(calibrated or 0, 3)

    def test_written_cap_in_notes_clamps_displayed_score(self) -> None:
        from app.services.go_no_go_capability import clamp_score_to_written_cap

        notes = (
            "Score capped at 2/5 due to geographic distance, unconfirmed "
            "meeting attendance."
        )
        self.assertEqual(clamp_score_to_written_cap(4, notes), 2)
        self.assertEqual(clamp_score_to_written_cap(2, notes), 2)
        self.assertEqual(clamp_score_to_written_cap(4, "no cap stated"), 4)


class EnforceCapabilityEvidenceTests(unittest.TestCase):
    """End-to-end: the real municipal-website RFP that scored 3.4."""

    def _analysis(self):
        from app.models.go_no_go import (
            GoNoGoAnalysis,
            GoNoGoDecisionMatrixRow,
            GoNoGoDimension,
        )

        def dim():
            return GoNoGoDimension(summary="ok", scoreImpact="neutral", flags=[])

        return GoNoGoAnalysis(
            summary="Strong technical capability match.",
            recommendation="go",
            fitScore=4,
            worthScore=3,
            scopeMatch=dim(),
            sectorMatch=dim(),
            compliance=dim(),
            teamMatch=dim(),
            # Rows as the ADJUDICATOR would emit them: it already checked every
            # quote against the document cited, so unsupported claims arrive
            # marked gap. _enforce_capability_evidence no longer re-validates —
            # re-running term matching over adjudicated rows is what produced
            # "0 ungrounded claims rejected" followed by "downgrades=7".
            capabilityMatrix=[
                _adjudicated_gap("Website redesign", "cited source not retrieved"),
                _adjudicated_gap("CMS implementation", "cited source not retrieved"),
                _adjudicated_gap("Hosting and maintenance", "no KB source cited"),
                _adjudicated_gap("Content migration", "does not evidence it"),
            ],
            decisionMatrix=[
                GoNoGoDecisionMatrixRow(
                    dimension="Technical Capability Match", score=4,
                    notes="Municipal website experience verified",
                ),
                GoNoGoDecisionMatrixRow(dimension="Resource Availability", score=3, notes=""),
                GoNoGoDecisionMatrixRow(dimension="Financial Viability", score=3, notes=""),
                GoNoGoDecisionMatrixRow(dimension="Strategic Value", score=4, notes=""),
                GoNoGoDecisionMatrixRow(dimension="Win Probability", score=3, notes=""),
            ],
        )

    def test_fabricated_verifications_flip_go_to_no_go(self) -> None:
        from app.services.go_no_go_service import _enforce_capability_evidence
        from app.services.go_no_go_service import compute_overall_go_score

        analysis = self._analysis()
        before_score = compute_overall_go_score(analysis)
        self.assertEqual(analysis.recommendation, "go")

        out = _enforce_capability_evidence(analysis, KB_HITS)
        after_score = compute_overall_go_score(out)

        self.assertEqual(out.recommendation, "no_go")
        self.assertLess(after_score, before_score)
        self.assertTrue(
            all(r.status == "gap" for r in out.capability_matrix),
            [(r.requirement, r.status) for r in out.capability_matrix],
        )
        joined = " | ".join(out.critical_gaps)
        self.assertIn("CMS implementation", joined)
        self.assertIn("Content migration", joined)

    def test_genuine_evidence_is_left_intact(self) -> None:
        from app.services.go_no_go_service import _enforce_capability_evidence

        analysis = self._analysis().model_copy(
            update={
                "capability_matrix": [
                    _row("WordPress website development", "verified",
                         "04_Bio_ShawnDiCriscio.pdf"),
                ]
            }
        )

        out = _enforce_capability_evidence(analysis, KB_HITS)

        self.assertEqual(out.capability_matrix[0].status, "verified")
        self.assertEqual(out.recommendation, "go")

    def test_technical_score_is_raised_to_match_evidence_matrix(self) -> None:
        """LLM understated Technical at 2 while matrix evidence supports ~3+."""
        from app.models.go_no_go import GoNoGoDecisionMatrixRow
        from app.services.go_no_go_service import _enforce_capability_evidence

        analysis = self._analysis().model_copy(
            update={
                "capability_matrix": [
                    _row("WordPress CMS", "verified", "04_Bio_ShawnDiCriscio.pdf"),
                    _row("Website redesign", "verified", "03_CS_TorrentLaboratories.pdf"),
                    _row("Responsive design", "verified", "03_CS_TorrentLaboratories.pdf"),
                    _row("SEO basics", "partial", "03_CS_TorrentLaboratories.pdf"),
                    _adjudicated_gap("Enterprise hosting SLA", "absent"),
                    _adjudicated_gap("PlanetBids integration", "absent"),
                ],
                "decision_matrix": [
                    GoNoGoDecisionMatrixRow(
                        dimension="Technical Capability Match",
                        score=2,
                        notes="no WordPress case studies",
                    ),
                    GoNoGoDecisionMatrixRow(
                        dimension="Resource Availability", score=2, notes=""
                    ),
                    GoNoGoDecisionMatrixRow(
                        dimension="Financial Viability", score=3, notes=""
                    ),
                    GoNoGoDecisionMatrixRow(
                        dimension="Strategic Value", score=2, notes=""
                    ),
                    GoNoGoDecisionMatrixRow(
                        dimension="Win Probability", score=2, notes=""
                    ),
                ],
            }
        )

        out = _enforce_capability_evidence(analysis, KB_HITS)
        tech = next(
            r
            for r in out.decision_matrix
            if r.dimension.casefold() == "technical capability match"
        )
        win = next(
            r
            for r in out.decision_matrix
            if r.dimension.casefold() == "win probability"
        )
        self.assertGreaterEqual(tech.score, 3)
        self.assertGreaterEqual(win.score, 3)
        self.assertEqual(out.recommendation, "review")

    def test_partial_core_gaps_with_strong_composite_are_conditions_not_no_go(
        self,
    ) -> None:
        """Score ~3.8 must not print NO-GO solely because a minority of cores gap.

        Live NYCEDC-class bug: fit/worth and matrix average stayed high while
        any core gap forced recommendation=no_go and the summary lead with
        'NO-GO — N of M …'. Pipeline threshold logic treats ≥3.0 as go/conditions.
        """
        from app.models.go_no_go import GoNoGoDecisionMatrixRow
        from app.services.go_no_go_service import (
            _enforce_capability_evidence,
            compute_overall_go_score,
        )

        analysis = self._analysis().model_copy(
            update={
                "capability_matrix": [
                    _row("Brand strategy", "verified", "x"),
                    _row("Media planning", "verified", "x"),
                    _row("Creative production", "verified", "x"),
                    _row("Public education campaigns", "verified", "x"),
                    _adjudicated_gap(
                        "NYC PASSPort registration", "no KB source cited"
                    ),
                ],
                "decision_matrix": [
                    GoNoGoDecisionMatrixRow(
                        dimension="Technical Capability Match",
                        score=4,
                        notes="strong",
                    ),
                    GoNoGoDecisionMatrixRow(
                        dimension="Resource Availability", score=4, notes=""
                    ),
                    GoNoGoDecisionMatrixRow(
                        dimension="Financial Viability", score=4, notes=""
                    ),
                    GoNoGoDecisionMatrixRow(
                        dimension="Strategic Value", score=4, notes=""
                    ),
                    GoNoGoDecisionMatrixRow(
                        dimension="Win Probability", score=4, notes=""
                    ),
                ],
            }
        )

        out = _enforce_capability_evidence(analysis, KB_HITS)
        overall = compute_overall_go_score(out)

        self.assertGreaterEqual(overall or 0, 3.0)
        self.assertEqual(out.recommendation, "review")
        self.assertTrue(
            out.summary.startswith("GO WITH CONDITIONS"),
            out.summary,
        )
        self.assertNotIn("NO-GO —", out.summary[:80])

    def test_high_score_never_keeps_stale_no_go_label(self) -> None:
        """Safety net: overall ≥3.0 cannot wear a No-Go badge (NYCEDC live bug)."""
        from app.models.go_no_go import GoNoGoDecisionMatrixRow
        from app.services.go_no_go_service import align_recommendation_with_score

        analysis = self._analysis().model_copy(
            update={
                "recommendation": "no_go",
                "summary": (
                    "NO-GO — 5 of 24 required capabilities lack verifiable "
                    "knowledge-base evidence. Overall Go Score 3.8/5."
                ),
                "stage_one_report": "## FINAL RECOMMENDATION\nNO-GO\n",
                "capability_matrix": [
                    _row("Brand strategy", "verified", "x"),
                ],
                "decision_matrix": [
                    GoNoGoDecisionMatrixRow(
                        dimension="Technical Capability Match", score=4, notes=""
                    ),
                    GoNoGoDecisionMatrixRow(
                        dimension="Resource Availability", score=4, notes=""
                    ),
                    GoNoGoDecisionMatrixRow(
                        dimension="Financial Viability", score=4, notes=""
                    ),
                    GoNoGoDecisionMatrixRow(
                        dimension="Strategic Value", score=4, notes=""
                    ),
                    GoNoGoDecisionMatrixRow(
                        dimension="Win Probability", score=3, notes=""
                    ),
                ],
            }
        )

        out = align_recommendation_with_score(analysis)

        self.assertEqual(out.recommendation, "review")
        self.assertTrue(out.summary.startswith("GO WITH CONDITIONS"), out.summary)
        self.assertNotIn("NO-GO", out.stage_one_report)

    def test_missing_matrix_fails_closed_not_open(self) -> None:
        """An omitted matrix must not silently bypass validation.

        Returning the analysis untouched here would let any response that
        simply drops capabilityMatrix keep an unvalidated "go" — the same
        fail-open shape as the original bug.
        """
        from app.services.go_no_go_service import _enforce_capability_evidence

        analysis = self._analysis().model_copy(update={"capability_matrix": []})
        out = _enforce_capability_evidence(analysis, KB_HITS)

        self.assertNotEqual(out.recommendation, "go")
        self.assertTrue(
            any("has been checked against" in g for g in out.critical_gaps),
            out.critical_gaps,
        )

    def test_insufficient_data_path_is_left_alone(self) -> None:
        from app.services.go_no_go_service import _enforce_capability_evidence

        analysis = self._analysis().model_copy(
            update={"capability_matrix": [], "insufficient_data": True,
                    "recommendation": None}
        )
        out = _enforce_capability_evidence(analysis, KB_HITS)

        self.assertIsNone(out.recommendation)
        self.assertEqual(out.critical_gaps, analysis.critical_gaps)



class NarrativeReconciliationTests(unittest.TestCase):
    """The Markdown report must not contradict the enforced verdict.

    Observed output: stage label "No-Go", Technical Capability Match 0/5, a list
    of six core requirements admitted to be unevidenced — and immediately above
    it "## FINAL RECOMMENDATION\nGO WITH CONDITIONS" plus the model's own
    "Overall Go Score 3.4/5". Readers act on the prose.
    """

    REPORT = (
        "## EXECUTIVE SUMMARY\n"
        "Recommendation: GO WITH CONDITIONS. Overall Go Score 3.4/5.\n\n"
        "## CAPABILITY ASSESSMENT\n"
        "| Requirement | Status |\n| CMS implementation | Gap |\n\n"
        "## FINAL RECOMMENDATION\n"
        "GO WITH CONDITIONS — proceed with named conditions.\n"
    )

    def test_narrative_verdict_follows_enforced_no_go(self) -> None:
        from app.services.go_no_go_capability import reconcile_narrative

        out = reconcile_narrative(
            self.REPORT, recommendation="no_go", overall_score=2.6
        )

        self.assertNotIn("GO WITH CONDITIONS", out)
        self.assertIn("NO-GO", out)
        # Substitutions must not cascade: replacing the verdict inline once
        # produced "NO-NO-GO" when the bare-GO pass re-matched its own output.
        self.assertNotIn("NO-NO-GO", out)
        self.assertEqual(out.count("NO-GO"), 2, out)

    def test_stated_score_is_corrected(self) -> None:
        from app.services.go_no_go_capability import reconcile_narrative

        out = reconcile_narrative(
            self.REPORT, recommendation="no_go", overall_score=2.6
        )

        self.assertNotIn("3.4/5", out)
        self.assertIn("2.6/5", out)

    def test_review_verdict_keeps_conditions_wording(self) -> None:
        from app.services.go_no_go_capability import reconcile_narrative

        out = reconcile_narrative(
            self.REPORT, recommendation="review", overall_score=3.0
        )

        self.assertIn("GO WITH CONDITIONS", out)
        self.assertIn("3.0/5", out)

    def test_end_to_end_report_matches_verdict(self) -> None:
        from app.services.go_no_go_service import (
            _enforce_capability_evidence,
            compute_overall_go_score,
        )

        base = EnforceCapabilityEvidenceTests._analysis(EnforceCapabilityEvidenceTests())
        analysis = base.model_copy(update={"stage_one_report": self.REPORT})

        out = _enforce_capability_evidence(analysis, KB_HITS)

        self.assertEqual(out.recommendation, "no_go")
        self.assertNotIn("GO WITH CONDITIONS", out.stage_one_report)
        self.assertIn(
            f"{compute_overall_go_score(out)}/5", out.stage_one_report
        )

    def test_empty_report_is_untouched(self) -> None:
        from app.services.go_no_go_capability import reconcile_narrative

        self.assertEqual(reconcile_narrative("", recommendation="no_go",
                                             overall_score=1.0), "")



class BroadcastEvidenceSynonymTests(unittest.TestCase):
    """Maricopa-style TV/broadcast KB text must evidence multimedia requirements."""

    def test_broadcast_aliases_match_television_kb(self) -> None:
        from app.services.go_no_go_capability import _source_supports

        src = (
            "Maricopa County contract explicitly lists Television advertisement "
            "production, Broadcast (TV/Radio) campaign management, and Produce "
            "broadcast-quality video for TV/cinema."
        )
        self.assertTrue(
            _source_supports(
                "broadcast production and multimedia editing",
                src,
            )
        )


class DisclaimedSkillTests(unittest.TestCase):
    """KB text that qualifies a skill must not be read as evidence of it.

    Verbatim from zö's master bio: "Web Design/Development (Not Programming) —
    15 years". Bare term matching sees web/design/development and counts it as
    development capability; the source is disclaiming exactly that.
    """

    HITS = [
        _hit(
            "02_MasterTemplate_OrgStructure_AllTeamBios.pdf",
            "Curt Schultz, Creative Director. Web Design/Development "
            "(Not Programming) - 15 years. Print and brand graphic design.",
        ),
        _hit(
            "04_Bio_ShawnDiCriscio.pdf",
            "Shawn DiCriscio, Web Developer. 10+ years building and maintaining "
            "WordPress websites. Built and worked on hundreds of websites.",
        ),
    ]

    def test_disclaimed_programming_is_not_evidence(self) -> None:
        rows = [
            _row("Programming and development",
                 "verified",
                 "02_MasterTemplate_OrgStructure_AllTeamBios.pdf")
        ]

        out, _ = validate_capability_rows(rows, self.HITS)

        self.assertEqual(out[0].status, "unverified", out[0].downgrade_reason)

    def test_the_real_web_developer_still_validates(self) -> None:
        rows = [_row("WordPress website development", "verified",
                     "04_Bio_ShawnDiCriscio.pdf")]

        out, _ = validate_capability_rows(rows, self.HITS)

        self.assertEqual(out[0].status, "verified")

    def test_affirmative_claims_in_same_doc_survive(self) -> None:
        """The disclaimer must not poison unrelated claims in that document."""
        rows = [_row("Brand graphic design", "verified",
                     "02_MasterTemplate_OrgStructure_AllTeamBios.pdf")]

        out, _ = validate_capability_rows(rows, self.HITS)

        self.assertEqual(out[0].status, "verified", out[0].downgrade_reason)


if __name__ == "__main__":
    unittest.main()
