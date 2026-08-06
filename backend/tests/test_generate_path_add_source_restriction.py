"""Task 15 (generate-path half): the SAME duplicate-criteria defect that
Task 14 fixed in Scan-RFP's reconciler (proposal_rfp_compliance.reconcile_
requirement_ledger / test_scan_rfp_add_source_restriction.py) was still live
on the generate-from-scratch path — amend_outline_for_missing_requirements
(app/services/proposal_intelligence/assembler.py), wired into Phase 2's
derive_legacy_fields, amended one new outline section per missing
scored_criterion with no source filter at all. Generating a proposal from
scratch against an RFP shaped like the live incident would have grown a
23-section outline the same way Scan-RFP once did, just one step earlier in
the pipeline.

Root defect data, reproduced verbatim from the live incident (see
test_scan_rfp_add_source_restriction.py's docstring for the original
Scan-RFP-side writeup): five EVALUATION CRITERIA (scoring categories) whose
names share no meaningful tokens with the requirement-phrased sections that
already cover them —

    MISS  Relevant Experience                    / Examples of similar work performed within the past five (5) years
    MISS  Personnel and Project Management       / Team members who will be assigned to MSU Denver
    MISS  Strategic Approach and Methodology     / Brief description of campaign planning methodology and competitor research
    MISS  Cost and Overall Value                 / Pricing structure
    MISS  Reporting and Performance Optimization / Sample reporting dashboard or campaign report

Fix: amend_outline_for_missing_requirements now filters ledger.missing() to
sources in _ADD_ELIGIBLE_SOURCES (required_content, form) — imported from
proposal_rfp_compliance rather than redefined, so the pre-draft (assembler)
and post-draft (Scan-RFP reconciler) checks can never drift on what counts
as addable. A missing scored_criterion stays advisory (ledger.missing()
still reports it) but is never auto-amended into the outline. The matcher
(_match_outline_sections) is untouched — see test_outline_coverage.py /
test_section_aliases.py's false-positive battery for why loosening it is
rejected.
"""

from __future__ import annotations

import unittest

from app.models.proposal import RfpSectionMap
from app.services.proposal_intelligence.assembler import (
    amend_outline_for_missing_requirements,
    build_requirement_ledger,
)
from app.services.proposal_intelligence.schemas import ComplianceItem, EvaluationCriterion

# The five real (criterion name, covering section title) pairs from the live
# incident — identical to REAL_SCORED_CRITERIA_FROM_INCIDENT in
# test_scan_rfp_add_source_restriction.py, reused here against the
# generate-path amender instead of the Scan-RFP reconciler.
REAL_SCORED_CRITERIA_FROM_INCIDENT = [
    (
        "Relevant Experience",
        "Examples of similar work performed within the past five (5) years",
    ),
    (
        "Personnel and Project Management",
        "Team members who will be assigned to MSU Denver",
    ),
    (
        "Strategic Approach and Methodology",
        "Brief description of campaign planning methodology and competitor research",
    ),
    ("Cost and Overall Value", "Pricing structure"),
    (
        "Reporting and Performance Optimization",
        "Sample reporting dashboard or campaign report",
    ),
]


class ScoredCriteriaAreNeverAutoAmendedOnTheGeneratePathTests(unittest.TestCase):
    """Required test: all five real scored criteria from the incident are
    advisory only — none are auto-amended into the outline — while the
    outline's pre-existing requirement-phrased sections are left untouched."""

    def _outline_and_criteria(self) -> tuple[list[RfpSectionMap], list[EvaluationCriterion]]:
        outline = [
            RfpSectionMap(id=f"sec-{i}", title=covering_title)
            for i, (_c, covering_title) in enumerate(REAL_SCORED_CRITERIA_FROM_INCIDENT)
        ]
        criteria = [
            EvaluationCriterion(name=criterion_name, weight=20.0)
            for criterion_name, _covering in REAL_SCORED_CRITERIA_FROM_INCIDENT
        ]
        return outline, criteria

    def test_none_of_the_five_scored_criteria_are_auto_amended(self) -> None:
        outline, criteria = self._outline_and_criteria()
        ledger = build_requirement_ledger([], criteria, outline)

        # Confirm the matcher genuinely misses every pair (the defect
        # condition) before asserting on the amend step's behavior.
        missing_texts = {r.text for r in ledger.missing()}
        for criterion_name, _covering in REAL_SCORED_CRITERIA_FROM_INCIDENT:
            self.assertIn(
                criterion_name,
                missing_texts,
                f"expected the matcher to miss {criterion_name!r} (defect condition)",
            )

        amended = amend_outline_for_missing_requirements(ledger, outline)

        self.assertEqual(
            len(amended),
            len(outline),
            "no duplicate stub sections were created for the five scored criteria",
        )
        amended_titles = {s.title for s in amended}
        for criterion_name, _covering in REAL_SCORED_CRITERIA_FROM_INCIDENT:
            self.assertNotIn(
                criterion_name,
                amended_titles,
                f"{criterion_name!r} must never be auto-amended into the outline",
            )
        # The five pre-existing requirement-phrased sections must survive untouched.
        for _criterion_name, covering_title in REAL_SCORED_CRITERIA_FROM_INCIDENT:
            self.assertIn(covering_title, amended_titles)

    def test_genuinely_missing_required_content_cover_letter_is_still_amended(self) -> None:
        """The fix narrows WHO gets auto-amended, not whether amend still
        works at all — a genuinely absent required_content item is still
        added."""
        outline, criteria = self._outline_and_criteria()
        compliance_items = [
            ComplianceItem(id="comp-cover-letter", requirement="A signed cover letter", mandatory=True)
        ]
        ledger = build_requirement_ledger(compliance_items, criteria, outline)

        amended = amend_outline_for_missing_requirements(ledger, outline)

        amended_titles = [s.title for s in amended]
        self.assertIn("A signed cover letter", amended_titles)
        self.assertEqual(
            len(amended),
            len(outline) + 1,
            "exactly one new section for the genuinely missing cover letter — "
            "nothing else added",
        )


if __name__ == "__main__":
    unittest.main()
