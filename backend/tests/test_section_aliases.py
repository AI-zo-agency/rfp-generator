"""Task 8: close the matcher's last misses with a curated alias table, then
auto-apply ADD (amend_outline_for_missing_requirements) now that the matcher
is proven at 10/10 with zero false positives.

Four wording-variant pairs share zero (or only "boring") tokens and so can
never be closed by _scored_token_overlap_match's shared-token scoring alone:
Key Personnel/Staffing Plan, Project Schedule/Timeline, Company
Overview/About Us, Executive Summary/Summary of Approach. Cover
Letter/Letter of Transmittal already passed via token overlap (they share
"letter") but is also in the alias table for robustness/documentation.

This file also covers a pre-existing gap the measurement gate surfaced along
the way: two ordinary English words in a short, generic, multi-token title
("Our Work", "Key Staff", "Project Team") can coincidentally both appear in
an unrelated short requirement, clearing the old 1/3 longer-side-coverage
floor by luck. Fixed by requiring a stricter floor (1/2) specifically when a
MULTI-token side is FULLY swallowed by the other — see
_MIN_LONGER_SIDE_COVERAGE_FULL_CONTAINMENT in assembler.py. A single-token
full match (e.g. "References") does not get the stricter floor; that is the
genuine wording-variant case the matcher exists to catch.
"""

from __future__ import annotations

import unittest

from app.models.proposal import RfpSectionMap
from app.services.proposal_intelligence.assembler import (
    _alias_whole_concept_match,
    _match_outline_sections,
    _match_tokens,
    _normalize,
    derive_legacy_fields,
)
from app.services.proposal_intelligence.schemas import (
    ComplianceItem,
    ComplianceMatrix,
    EvaluationAnalysis,
    EvaluationCriterion,
    OutlineSection,
    ProposalExecutionPlan,
    ProposalOutline,
    SectionPlans,
)
from app.services.proposal_section_aliases import PROPOSAL_SECTION_ALIAS_GROUPS


def _matches(requirement_text: str, section_title: str) -> bool:
    sections = [RfpSectionMap(id="sec", title=section_title)]
    return bool(
        _match_outline_sections(
            requirement_text=requirement_text,
            target_hint="",
            outline_sections=sections,
        )
    )


# ---------------------------------------------------------------------------
# Structural sanity on the alias table itself: catches an editing mistake
# (e.g. a copy-paste that puts the same phrase, or two phrases that reduce to
# the same token set, in two different groups) that would silently MERGE two
# distinct procurement concepts.
# ---------------------------------------------------------------------------


class AliasTableSanityTests(unittest.TestCase):
    def test_no_alias_phrase_appears_in_two_groups(self) -> None:
        seen: dict[str, int] = {}
        for gi, group in enumerate(PROPOSAL_SECTION_ALIAS_GROUPS):
            for phrase in group:
                self.assertNotIn(
                    phrase, seen, f"{phrase!r} appears in groups {seen.get(phrase)} and {gi}"
                )
                seen[phrase] = gi

    def test_no_two_groups_share_a_token_set(self) -> None:
        """Two different phrases reducing to the same meaningful-token-set in
        different groups would let one alias silently satisfy the other
        group's concept too."""
        token_sets_by_group = [
            {frozenset(_match_tokens(_normalize(p))) for p in group}
            for group in PROPOSAL_SECTION_ALIAS_GROUPS
        ]
        for i, a in enumerate(token_sets_by_group):
            for j, b in enumerate(token_sets_by_group):
                if i >= j:
                    continue
                overlap = a & b
                self.assertFalse(
                    overlap, f"groups {i} and {j} share token set(s) {overlap}"
                )


# ---------------------------------------------------------------------------
# Step 4: re-measure both directions. All 10 wording-variant pairs must now
# hit; the four that could ONLY close via the alias channel are singled out
# here so a future refactor that removes the alias channel fails loudly on
# this file, not just on the aggregate count in test_outline_coverage.py.
# ---------------------------------------------------------------------------

_ALIAS_ONLY_PAIRS: list[tuple[str, str]] = [
    ("Key Personnel", "Staffing Plan"),
    ("Project Schedule", "Timeline"),
    ("Company Overview", "About Us"),
    ("Executive Summary", "Summary of Approach"),
]


class AliasWholeConceptContractTests(unittest.TestCase):
    """Direct, unit-level test of the whole-set-equality contract, isolated
    from the overlap channel and from _match_outline_sections' other
    branches."""

    def test_exact_whole_token_sets_match(self) -> None:
        self.assertTrue(
            _alias_whole_concept_match(
                _match_tokens(_normalize("Key Personnel")),
                _match_tokens(_normalize("Staffing Plan")),
            )
        )

    def test_a_superset_does_not_match(self) -> None:
        """One extra token anywhere breaks whole-set equality — this is the
        mechanism, not a coincidence, that keeps aliases from firing on a
        token buried inside a longer ask."""
        self.assertFalse(
            _alias_whole_concept_match(
                _match_tokens(_normalize("Detailed Key Personnel Roster")),
                _match_tokens(_normalize("Staffing Plan")),
            )
        )

    def test_empty_token_sets_never_match(self) -> None:
        self.assertFalse(_alias_whole_concept_match(set(), set()))
        self.assertFalse(_alias_whole_concept_match({"key", "personnel"}, set()))


class AliasChannelMeasurementTests(unittest.TestCase):
    def test_alias_only_pairs_now_match(self) -> None:
        for req, title in _ALIAS_ONLY_PAIRS:
            with self.subTest(req=req, title=title):
                self.assertTrue(_matches(req, title))

    def test_alias_only_pairs_share_no_usable_overlap_token(self) -> None:
        """Sanity: proves these really are alias-channel wins, not token
        overlap in disguise (a stale alias entry that happens to also work
        via overlap would hide a token-overlap regression)."""
        for req, title in _ALIAS_ONLY_PAIRS:
            with self.subTest(req=req, title=title):
                ta = _match_tokens(_normalize(req))
                tb = _match_tokens(_normalize(title))
                inter = ta & tb
                self.assertTrue(
                    not inter or inter <= {"summary", "cost", "price", "pricing", "budget", "fee", "fees"},
                    f"{req!r}/{title!r} share a non-boring token {inter}; not an alias-only case",
                )

    def test_reverse_direction_also_matches(self) -> None:
        """Bidirectional: the section title can be the canonical procurement
        term and the compliance-item text the plain-English variant, or vice
        versa — both directions must work."""
        for req, title in _ALIAS_ONLY_PAIRS:
            with self.subTest(req=req, title=title):
                self.assertTrue(_matches(title, req))


# ---------------------------------------------------------------------------
# The pre-existing multi-token coincidental false-positive fix.
# ---------------------------------------------------------------------------

_MULTI_TOKEN_COINCIDENTAL_FALSE_POSITIVES: list[tuple[str, str]] = [
    ("Our Work", "Our work order requires site approval"),
    ("Key Staff", "Key deliverable needs staff approval"),
    ("Project Team", "Project closeout needs team signoff"),
]


class MultiTokenCoincidentalFalsePositiveTests(unittest.TestCase):
    def test_no_multi_token_generic_title_coincidentally_satisfies_a_short_unrelated_requirement(
        self,
    ) -> None:
        false_positives = [
            (title, req)
            for title, req in _MULTI_TOKEN_COINCIDENTAL_FALSE_POSITIVES
            if _matches(req, title)
        ]
        self.assertEqual(false_positives, [])

    def test_genuine_two_token_wording_variants_still_match(self) -> None:
        """The stricter full-containment floor must not make the matcher
        useless for real two-token synonym pairs."""
        self.assertTrue(_matches("Insurance Certificate", "Certificate of Insurance"))


# ---------------------------------------------------------------------------
# Five (plus two extra) adversarial alias probes: cases where a curated
# alias could plausibly over-fire if the whole-concept-equality contract
# were weakened to substring/subset containment. All must stay False.
# ---------------------------------------------------------------------------


class AdversarialAliasProbeTests(unittest.TestCase):
    def test_fee_schedule_is_not_swept_into_project_schedule(self) -> None:
        """"Schedule" is shared vocabulary between a project timeline and a
        pricing/payment schedule — two unrelated procurement concepts."""
        self.assertFalse(
            _matches(
                "Provide a detailed cost proposal including your fee schedule "
                "and payment terms",
                "Fee Schedule",
            )
        )

    def test_bare_schedule_does_not_satisfy_project_schedule(self) -> None:
        self.assertFalse(
            _matches(
                "Attach a certified payment schedule showing unit prices for "
                "each line item",
                "Schedule",
            )
        )

    def test_bare_staff_does_not_satisfy_key_personnel(self) -> None:
        """"Staff" alone (not "Key Staff") must not ride the Key Personnel
        alias just because it shares a topic area."""
        self.assertFalse(
            _matches(
                "Describe the qualifications of your proposed project manager, "
                "including certifications, years of relevant experience, and "
                "professional licenses held",
                "Staff",
            )
        )

    def test_bare_plan_does_not_satisfy_staffing_plan(self) -> None:
        """"Plan" is reused across a dozen unrelated section types (QA Plan,
        Transition Plan, Safety Plan) and must not resolve to Staffing Plan."""
        self.assertFalse(
            _matches(
                "Submit a quality control plan describing inspection "
                "frequency and defect remediation procedures",
                "Plan",
            )
        )

    def test_timeline_inside_a_longer_specific_ask_does_not_match(self) -> None:
        """The exact case named in the brief: "Timeline" must be an alias of
        the WHOLE concept, never of one token inside a longer, more specific
        requirement."""
        self.assertFalse(
            _matches(
                "Provide a timeline for subcontractor onboarding and describe "
                "your quality assurance methodology",
                "Timeline",
            )
        )

    def test_diluted_title_does_not_ride_the_timeline_alias(self) -> None:
        """A title combining "Timeline" with an unrelated second concept must
        not silently satisfy a bare "Project Schedule" requirement — the
        title's own meaningful-token-set no longer equals {"timeline"}."""
        self.assertFalse(_matches("Project Schedule", "Timeline and Budget"))

    def test_about_our_approach_does_not_cross_into_executive_summary(self) -> None:
        """Combining tokens from two different alias groups (About Our Firm /
        Summary of Approach) must not accidentally land in either group."""
        self.assertFalse(
            _matches("Executive Summary", "About Our Approach")
        )


# ---------------------------------------------------------------------------
# End-to-end proof (A): a scored requirement the outline omits gets amended
# in BEFORE drafting.
# ---------------------------------------------------------------------------


def _plan_missing_technical_approach() -> ProposalExecutionPlan:
    plan = ProposalExecutionPlan(rfpId="rfp-proof-a")
    plan.opportunity.evaluation = EvaluationAnalysis(
        criteria=[EvaluationCriterion(name="Technical Approach", weight=30.0)],
        confidence=0.9,
    )
    plan.writing.proposal_outline = ProposalOutline(
        sections=[OutlineSection(id="sec-pricing", title="Pricing Proposal", order=1, required=True)],
        confidence=0.85,
    )
    plan.writing.section_plans = SectionPlans(plans=[], confidence=0.85)
    return plan


def _plan_with_letter_of_transmittal() -> ProposalExecutionPlan:
    plan = ProposalExecutionPlan(rfpId="rfp-proof-b")
    plan.opportunity.compliance = ComplianceMatrix(
        items=[ComplianceItem(id="comp-1", requirement="Cover Letter", mandatory=True)],
        confidence=0.9,
    )
    plan.writing.proposal_outline = ProposalOutline(
        sections=[
            OutlineSection(id="sec-transmittal", title="Letter of Transmittal", order=1, required=True)
        ],
        confidence=0.85,
    )
    plan.writing.section_plans = SectionPlans(plans=[], confidence=0.85)
    return plan


class EndToEndAmendmentProofTests(unittest.TestCase):
    def test_proof_a_missing_scored_technical_approach_is_amended_into_the_outline(
        self,
    ) -> None:
        legacy = derive_legacy_fields(_plan_missing_technical_approach())
        titles = [s.title for s in legacy["rfpSections"]]
        self.assertIn("Technical Approach", titles)
        self.assertIn("Pricing Proposal", titles, "must not drop the existing section")

        ledger = legacy["requirementLedger"]
        technical_approach = next(r for r in ledger.requirements if r.text == "Technical Approach")
        self.assertTrue(technical_approach.satisfied_by)
        self.assertNotIn("Technical Approach", {r.text for r in ledger.missing()})

        amended = next(s for s in legacy["rfpSections"] if s.title == "Technical Approach")
        self.assertEqual(amended.evaluation_weight, 30.0, "must not drop the evaluation points")

    def test_proof_b_cover_letter_already_present_as_letter_of_transmittal_is_not_duplicated(
        self,
    ) -> None:
        legacy = derive_legacy_fields(_plan_with_letter_of_transmittal())
        titles = [s.title for s in legacy["rfpSections"]]

        # Exactly the section that was already there — no second cover-letter
        # section under any title.
        self.assertEqual(titles, ["Letter of Transmittal"])
        self.assertNotIn("Cover Letter", titles)

        ledger = legacy["requirementLedger"]
        cover_letter = next(r for r in ledger.requirements if r.text == "Cover Letter")
        self.assertEqual(cover_letter.satisfied_by, ["sec-transmittal"])
        self.assertNotIn("Cover Letter", {r.text for r in ledger.missing()})


if __name__ == "__main__":
    unittest.main()
