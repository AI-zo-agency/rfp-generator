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
# reintroduced the two false positives below — see the guard tests. Step 0's
# scored-overlap channel alone measured 6/10 (0 false positives), reported
# verbatim in task-2-report.md; four misses (Key Personnel/Staffing Plan,
# Project Schedule/Timeline, Company Overview/About Us, Executive
# Summary/Summary of Approach) share zero (or only "boring") tokens and
# cannot be bridged by any lexical overlap scoring. Task 8 added a curated,
# conservative alias table (proposal_section_aliases.py) as an additional
# match channel specifically for standard procurement synonyms like these,
# raising the measured hit rate to 10/10 with the false-positive battery
# below still at zero — see task-8-report.md and tests/test_section_aliases.py
# for the alias-specific measurements and adversarial probes.
# ---------------------------------------------------------------------------

_WORDING_VARIANT_PAIRS: list[tuple[str, str, bool]] = [
    ("Cover Letter", "Letter of Transmittal", True),
    ("Cost Proposal", "Pricing Proposal", True),
    ("Statement of Qualifications", "Qualifications", True),
    ("Executive Summary", "Summary of Approach", True),
    ("Key Personnel", "Staffing Plan", True),
    ("References", "Client References and Testimonials", True),
    ("Insurance Certificate", "Certificate of Insurance", True),
    ("Technical Approach", "Approach and Methodology", True),
    ("Project Schedule", "Timeline", True),
    ("Company Overview", "About Us", True),
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
        self.assertEqual(hits, 10, f"measured hit rate changed: {results}")

    def test_a_section_titled_summary_does_not_satisfy_an_insurance_requirement(self) -> None:
        self.assertFalse(
            self._matches("Provide a summary of your insurance coverage", "Summary")
        )

    def test_a_section_titled_cost_does_not_satisfy_a_cost_proposal_requirement(self) -> None:
        self.assertFalse(self._matches("Provide a detailed cost proposal", "Cost"))


# ---------------------------------------------------------------------------
# The false-positive battery.
#
# The first cut of the scored-overlap tier only tested the two guards the brief
# named ("Summary", "Cost") — both of which happen to be on the small
# _BORING_SHARED_TOKENS denylist. Every OTHER single-word title was wide open:
# a title normalizing to ONE meaningful token scores 1.0 on the shorter-side
# coverage test whenever that word appears anywhere in the requirement, so
# "Team" silently satisfied "...your project team, subcontractors, and key
# personnel including resumes and organizational charts". That marks the
# requirement satisfied and hides it from ledger.missing() forever — the exact
# defect the ledger exists to catch, and worse than the strict-exact matcher it
# replaced (which was useless but safe).
#
# Fixed by also requiring coverage of the LONGER side (see
# _MIN_LONGER_SIDE_COVERAGE). These titles are ordinary proposal section names,
# not adversarial probes, so this battery is committed to keep it that way.
# ---------------------------------------------------------------------------

_SINGLE_TOKEN_TITLE_FALSE_POSITIVES: list[tuple[str, str]] = [
    (
        "Overview",
        "Describe your firm's overall management approach, staffing plan, quality "
        "control procedures, and organizational overview of the proposed engagement",
    ),
    (
        "Team",
        "Provide detailed information about your project team, subcontractors, and "
        "key personnel including resumes and organizational charts",
    ),
    (
        "Experience",
        "Summarize your firm's relevant experience including similar projects, client "
        "references, and staff experience with municipal contracts",
    ),
    ("Approach", "Provide your project management approach and quality assurance plan."),
    ("Insurance", "Submit your firm's insurance certificate and bonding capacity docs."),
    (
        "References",
        "Provide a minimum of three professional references from municipal clients "
        "served within the past five years, including contact name, title, phone and email",
    ),
    (
        "Qualifications",
        "Describe the qualifications of your proposed project manager, including "
        "certifications, years of relevant experience, and professional licenses held",
    ),
    ("Summary", "Provide a summary of your insurance coverage"),
    ("Cost", "Provide a detailed cost proposal"),
]


class SingleTokenTitleFalsePositiveTests(unittest.TestCase):
    """A one-word section title must not claim a long, specific requirement."""

    def _matches(self, requirement_text: str, section_title: str) -> bool:
        sections = [RfpSectionMap(id="sec", title=section_title)]
        return bool(
            _match_outline_sections(
                requirement_text=requirement_text,
                target_hint="",
                outline_sections=sections,
            )
        )

    def test_no_single_token_title_satisfies_a_long_specific_requirement(self) -> None:
        false_positives = [
            (title, requirement)
            for title, requirement in _SINGLE_TOKEN_TITLE_FALSE_POSITIVES
            if self._matches(requirement, title)
        ]
        self.assertEqual(
            false_positives,
            [],
            "a false 'satisfied' hides the requirement from ledger.missing() forever",
        )

    def test_the_reproduced_team_case_specifically(self) -> None:
        """Named in the review: an org-chart-only "Team" tab must not discharge a
        requirement demanding subcontractor disclosures and key-personnel resumes."""
        self.assertFalse(
            self._matches(
                "Provide detailed information about your project team, subcontractors, "
                "and key personnel including resumes and organizational charts",
                "Team",
            )
        )

    def test_a_genuine_short_requirement_still_matches_its_short_title(self) -> None:
        """The longer-side guard must not make the matcher useless: when both
        sides really are short and about the same thing, it still matches."""
        self.assertTrue(self._matches("Statement of Qualifications", "Qualifications"))
        self.assertTrue(self._matches("References", "Client References and Testimonials"))


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
    """Step 4's interface (unit-level, no ledger/outline plumbing). Wired into
    the live assembler pipeline (derive_legacy_fields) by Task 8, once the
    alias table brought matcher precision to a measured 10/10 with zero false
    positives — see amend_outline_for_missing_requirements' docstring and
    tests/test_section_aliases.py for the wiring-level end-to-end proof."""

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

    def test_a_fractional_criterion_weight_is_not_truncated(self) -> None:
        """An RFP can weight a criterion at 12.5 pts; int() silently made it 12."""
        ledger = RequirementLedger(
            requirements=[
                LedgerRequirement(
                    id="r1",
                    text="Technical Approach",
                    source="scored_criterion",
                    points=12.5,
                    satisfiedBy=[],
                )
            ]
        )
        amended = amend_outline_for_missing_requirements(ledger, [])
        self.assertEqual(amended[0].evaluation_weight, 12.5)


class FractionalWeightFlowsThroughDownstreamModelsTests(unittest.TestCase):
    """A fractional weight must survive every model it is handed to.

    proposal_ending_report.py:228 assigns ``mapped.evaluation_weight`` straight
    into EndingRequirementStatus, and proposal_proof_points.py:121 feeds it into
    the payload ProofPoint validates. While those stayed ``int | None`` and
    RfpSectionMap was widened, a fractional weight raised ValidationError in the
    first case and was silently dropped in the second.
    """

    def test_ending_requirement_status_accepts_a_fractional_weight(self) -> None:
        from app.services.proposal_ending_report import EndingRequirementStatus

        status = EndingRequirementStatus(
            sectionId="s1",
            sectionTitle="Technical Approach",
            requirement="Technical Approach",
            covered=False,
            evaluationWeight=12.5,
        )
        self.assertEqual(status.evaluation_weight, 12.5)

    def test_proof_point_accepts_a_fractional_weight(self) -> None:
        from app.models.proposal import ProofPoint

        point = ProofPoint.model_validate(
            {
                "requirement": "Technical Approach",
                "caseStudy": "City of Test",
                "evaluationWeight": 12.5,
            }
        )
        self.assertEqual(point.evaluation_weight, 12.5)

    def test_whole_number_weights_still_serialize_as_integers(self) -> None:
        """The union is int | float, not float, precisely so existing consumers
        keep seeing 30 rather than 30.0 on the wire."""
        import json

        from app.services.proposal_ending_report import EndingRequirementStatus

        section = RfpSectionMap(id="s1", title="T", evaluationWeight=30)
        self.assertEqual(
            json.loads(section.model_dump_json(by_alias=True))["evaluationWeight"], 30
        )
        status = EndingRequirementStatus(
            sectionId="s1",
            sectionTitle="T",
            requirement="r",
            covered=True,
            evaluationWeight=30,
        )
        self.assertEqual(
            json.loads(status.model_dump_json(by_alias=True))["evaluationWeight"], 30
        )


if __name__ == "__main__":
    unittest.main()
