"""A required section must survive the lean-outline filters.

Observed: an RFP scoring Technical Approach produced no such section. The planner
prompt says "Do NOT invent a default 'Methodology' ... stack", _GENERIC_FILLER_TITLES
targets `our approach|methodology`, and the static section-4-project-approach was
removed. All three are anti-boilerplate rules with no carve-out for a scored criterion.

Also covers Task 2 Step 0 (matcher precision, measured against 10 realistic
wording-variant pairs plus the two false-positive guards from Task 1's review)
and Step 5 (static delegation must be proven from the section's own text, not
assumed from its title).
"""

from __future__ import annotations

import unittest

from app.models.proposal import RfpSectionMap
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.services.proposal_intelligence.assembler import (
    _match_outline_sections,
    amend_outline_for_missing_requirements,
    build_requirement_ledger,
)
from app.services.proposal_intelligence.schemas import ComplianceItem, EvaluationCriterion
from app.services.proposal_outline_dedup import filter_lean_outline_sections
from app.services.proposal_voice_enforcement import (
    is_duplicate_static_rfp_section,
    static_section_covers_requirement,
)


class ScoredSectionsSurviveFilteringTests(unittest.TestCase):
    def test_a_scored_approach_section_is_not_filtered_as_generic_filler(self) -> None:
        """"Our Approach" is in _GENERIC_FILLER_TITLES and must survive when scored."""
        # RFP context deliberately never mentions "approach" — this is the
        # exact condition under which the unscored version below IS dropped,
        # so the scored case proves the carve-out, not an RFP-mention escape.
        rfp_context = "Submit pricing forms and three client references."
        unscored = {
            "id": "sec-approach-unscored",
            "title": "Our Approach",
            "required": True,
            "order": 1,
        }
        scored = {
            "id": "sec-approach-scored",
            "title": "Our Approach",
            "required": True,
            "order": 1,
            "evaluationWeight": 30,
        }

        kept_unscored, dropped_unscored = filter_lean_outline_sections(
            [unscored], rfp_context=rfp_context
        )
        self.assertEqual(kept_unscored, [], "sanity: unscored generic filler is dropped")
        self.assertTrue(dropped_unscored)

        kept_scored, dropped_scored = filter_lean_outline_sections(
            [scored], rfp_context=rfp_context
        )
        self.assertEqual(
            [s["id"] for s in kept_scored],
            ["sec-approach-scored"],
            "a section carrying evaluation points must never be dropped as generic filler",
        )
        self.assertEqual(dropped_scored, [])

    def test_type_of_firm_is_not_deleted_as_a_static_duplicate(self) -> None:
        """proposal_voice_enforcement.py:84 deletes this on the assumption 1.3 covers it."""
        title = "Indicate the type of firm and legal structure"
        # duplicateOfStaticSection forces should_skip_rfp_section_as_static_duplicate's
        # explicit-field branch, which is unconditional unless the section is scored.
        unscored = {
            "id": "sec-firm-unscored",
            "title": title,
            "required": True,
            "order": 1,
            "duplicateOfStaticSection": "section-1",
        }
        scored = {
            "id": "sec-firm-scored",
            "title": title,
            "required": True,
            "order": 1,
            "duplicateOfStaticSection": "section-1",
            "evaluationWeight": 15,
        }

        kept_unscored, _ = filter_lean_outline_sections([unscored], rfp_context="")
        self.assertEqual(kept_unscored, [], "sanity: unscored duplicate-field tab is dropped")

        kept_scored, dropped_scored = filter_lean_outline_sections([scored], rfp_context="")
        self.assertEqual(
            [s["id"] for s in kept_scored],
            ["sec-firm-scored"],
            "a section carrying evaluation points must never be dropped as a static duplicate",
        )
        self.assertEqual(dropped_scored, [])


# ---------------------------------------------------------------------------
# Step 0: matcher precision, measured (not assumed).
#
# Task 1's review measured the old strict-exact matcher at 10/10 misses on
# these pairs. Replacing it with unguarded token overlap (bare Jaccard, or
# "does the shorter title's tokens all appear in the requirement") would have
# reintroduced the two false positives below — see the guard tests. The
# measured hit rate here (6/10, 0 false positives) is reported verbatim in
# task-2-report.md; three misses (Key Personnel/Staffing Plan, Project
# Schedule/Timeline, Company Overview/About Us) share zero tokens and cannot
# be bridged by any lexical matcher without a domain synonym dictionary this
# task was not asked to build. The fourth miss (Executive Summary/Summary of
# Approach) shares only the word "summary", which is exactly the token the
# false-positive guards require to be untrustworthy on its own.
# ---------------------------------------------------------------------------

_WORDING_VARIANT_PAIRS: list[tuple[str, str, bool]] = [
    ("Cover Letter", "Letter of Transmittal", True),
    ("Cost Proposal", "Pricing Proposal", True),
    ("Statement of Qualifications", "Qualifications", True),
    ("Executive Summary", "Summary of Approach", False),
    ("Key Personnel", "Staffing Plan", False),
    ("References", "Client References and Testimonials", True),
    ("Insurance Certificate", "Certificate of Insurance", True),
    ("Technical Approach", "Approach and Methodology", True),
    ("Project Schedule", "Timeline", False),
    ("Company Overview", "About Us", False),
]


class MatcherPrecisionMeasurementTests(unittest.TestCase):
    """Step 0: report the hit rate, do not assume it."""

    def _matches(self, requirement_text: str, section_title: str) -> bool:
        sections = [RfpSectionMap(id="sec", title=section_title)]
        return bool(
            _match_outline_sections(
                requirement_text=requirement_text,
                target_hint="",
                outline_sections=sections,
            )
        )

    def test_measured_hit_rate_on_the_ten_wording_variant_pairs(self) -> None:
        results = []
        hits = 0
        for requirement_text, section_title, expected in _WORDING_VARIANT_PAIRS:
            got = self._matches(requirement_text, section_title)
            results.append((requirement_text, section_title, expected, got))
            if got:
                hits += 1
            self.assertEqual(
                got,
                expected,
                f"{requirement_text!r} vs {section_title!r}: expected match={expected}, got={got}",
            )
        # Documents the measured rate so a future matcher change has a baseline
        # to beat, and so this test fails loudly (not silently) if it regresses.
        self.assertEqual(hits, 6, f"measured hit rate changed: {results}")

    def test_a_section_titled_summary_does_not_satisfy_an_insurance_requirement(self) -> None:
        self.assertFalse(
            self._matches("Provide a summary of your insurance coverage", "Summary")
        )

    def test_a_section_titled_cost_does_not_satisfy_a_cost_proposal_requirement(self) -> None:
        self.assertFalse(self._matches("Provide a detailed cost proposal", "Cost"))


class GateReportsMissingScoredCriteriaTests(unittest.TestCase):
    """The reviewer's explicit acceptance check: prove the gate fires on a real
    miss, and prove it stays quiet when the same ask is covered under a
    different title (Step 0's matcher, not Step 3's structural carve-out)."""

    def test_an_uncovered_scored_technical_approach_is_reported_missing(self) -> None:
        ledger = build_requirement_ledger(
            [],
            [EvaluationCriterion(name="Technical Approach", weight=30.0)],
            [RfpSectionMap(id="s1", title="Pricing Proposal")],
        )
        missing_texts = {r.text for r in ledger.missing()}
        self.assertIn("Technical Approach", missing_texts)

    def test_a_scored_technical_approach_covered_under_a_different_title_is_not_reported(
        self,
    ) -> None:
        ledger = build_requirement_ledger(
            [],
            [EvaluationCriterion(name="Technical Approach", weight=30.0)],
            [RfpSectionMap(id="s1", title="Approach and Methodology")],
        )
        missing_texts = {r.text for r in ledger.missing()}
        self.assertNotIn("Technical Approach", missing_texts)
        scored = ledger.scored()
        self.assertEqual(scored[0].satisfied_by, ["s1"])


class StaticDelegationProofTests(unittest.TestCase):
    """Step 5: a requirement may be marked satisfied by a static section only
    if that section's text actually contains the answer."""

    def test_type_of_firm_is_not_assumed_covered_without_static_text(self) -> None:
        title = "Indicate the type of firm and legal structure"
        self.assertFalse(is_duplicate_static_rfp_section(title))

    def test_type_of_firm_is_proven_covered_when_static_text_names_the_entity(self) -> None:
        title = "Indicate the type of firm and legal structure"
        static_text = "zö agency is a limited liability company (LLC) formed in Oregon."
        self.assertTrue(
            is_duplicate_static_rfp_section(title, static_section_text=static_text)
        )

    def test_type_of_firm_stays_unproven_when_static_text_never_names_the_entity(
        self,
    ) -> None:
        title = "Indicate the type of firm and legal structure"
        static_text = "zö agency has served clients since 2013 with award-winning campaigns."
        self.assertFalse(
            is_duplicate_static_rfp_section(title, static_section_text=static_text)
        )

    def test_static_section_covers_requirement_helper_is_direct(self) -> None:
        self.assertTrue(
            static_section_covers_requirement(
                "type of firm", "We operate as a corporation registered in Oregon."
            )
        )
        self.assertFalse(
            static_section_covers_requirement(
                "type of firm", "We have twelve employees and a downtown office."
            )
        )


class AmendOutlineForMissingRequirementsTests(unittest.TestCase):
    """Step 4's interface, implemented and unit-tested — not wired into the
    live assembler pipeline. See amend_outline_for_missing_requirements'
    docstring for why (matcher precision is not yet proven against a real RFP)."""

    def test_appends_one_section_per_missing_mandatory_requirement(self) -> None:
        ledger = RequirementLedger(
            requirements=[
                LedgerRequirement(id="r1", text="Cover Letter", satisfiedBy=[]),
                LedgerRequirement(
                    id="r2",
                    text="Technical Approach",
                    source="scored_criterion",
                    points=30.0,
                    satisfiedBy=[],
                ),
                LedgerRequirement(id="r3", text="Already covered", satisfiedBy=["sec-1"]),
            ]
        )
        outline = [RfpSectionMap(id="sec-1", title="Already covered")]
        amended = amend_outline_for_missing_requirements(ledger, outline)

        titles = [s.title for s in amended]
        self.assertIn("Cover Letter", titles)
        self.assertIn("Technical Approach", titles)
        self.assertEqual(len(amended), 3, "must not duplicate the already-satisfied section")

    def test_is_idempotent_against_its_own_output(self) -> None:
        ledger = RequirementLedger(
            requirements=[LedgerRequirement(id="r1", text="Cover Letter", satisfiedBy=[])]
        )
        first = amend_outline_for_missing_requirements(ledger, [])
        second = amend_outline_for_missing_requirements(ledger, first)
        self.assertEqual(len(second), len(first))


if __name__ == "__main__":
    unittest.main()
