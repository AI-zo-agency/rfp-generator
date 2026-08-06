"""Task 8: close the matcher's last misses with a curated alias table, then
auto-apply ADD (amend_outline_for_missing_requirements) now that the matcher
is proven at 10/10 with zero false positives.

Four wording-variant pairs share zero (or only "boring") tokens and so can
never be closed by _scored_token_overlap_match's shared-token scoring alone:
Key Personnel/Staffing Plan, Project Schedule/Timeline, Company
Overview/About Us, Executive Summary/Summary of Approach. Cover
Letter/Letter of Transmittal already passed via token overlap (they share
"letter") but is also in the alias table for robustness/documentation.

TWO of those four are now DELIBERATE MISSES (measured 8/10, not 10/10).
Task 8's first cut closed all four and shipped two Criticals, because an
alias GROUP makes every member mutually equivalent — adding an Nth phrase
adds N-1 live equivalences, and the first cut measured only the plan-named
pairs and cross-GROUP probes, never the same-group cross-pairs:

  C1  "staffing plan" in the Key Personnel group let an RFP scoring "Key
      Personnel" (15 pts) and "Staffing Plan" (10 pts) as separate criteria
      mark BOTH satisfied by one "Staffing Plan" section — the WHO ask hidden
      from missing() by a HOW section.
  C2  "summary of approach" reduces to {summary, approach}, the same token
      set as "Approach Summary" (an ordinary sub-heading inside a Technical
      Approach section) — so a 20-pt Executive Summary criterion was absorbed
      by a subsection.

Both are reproduced end to end in CriticalRegressionEndToEndTests, and
SameGroupCrossPairTests now enumerates every same-group cross-pair with a
written justification so a new group member cannot be added without stating
what it claims to be equivalent to. That enumeration, not review, is the
control that keeps this table honest.

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
    _BORING_SHARED_TOKENS,
    _alias_whole_concept_match,
    _match_outline_sections,
    _match_tokens,
    _normalize,
    build_requirement_ledger,
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
# Step 4: re-measure both directions. Two of the four originally-targeted
# pairs are now deliberate MISSES (see DeliberateAliasMissTests below); the
# two that could ONLY close via the alias channel are singled out here so a
# future refactor that removes the alias channel fails loudly on this file,
# not just on the aggregate count in test_outline_coverage.py.
# ---------------------------------------------------------------------------

_ALIAS_ONLY_PAIRS: list[tuple[str, str]] = [
    ("Project Schedule", "Timeline"),
    ("Company Overview", "About Us"),
]


class AliasWholeConceptContractTests(unittest.TestCase):
    """Direct, unit-level test of the whole-set-equality contract, isolated
    from the overlap channel and from _match_outline_sections' other
    branches."""

    def test_exact_whole_token_sets_match(self) -> None:
        self.assertTrue(
            _alias_whole_concept_match(
                _match_tokens(_normalize("Project Schedule")),
                _match_tokens(_normalize("Timeline")),
            )
        )

    def test_a_superset_does_not_match(self) -> None:
        """One extra token anywhere breaks whole-set equality — this is the
        mechanism, not a coincidence, that keeps aliases from firing on a
        token buried inside a longer ask."""
        self.assertFalse(
            _alias_whole_concept_match(
                _match_tokens(_normalize("Detailed Project Schedule Narrative")),
                _match_tokens(_normalize("Timeline")),
            )
        )

    def test_empty_token_sets_never_match(self) -> None:
        self.assertFalse(_alias_whole_concept_match(set(), set()))
        self.assertFalse(_alias_whole_concept_match({"project", "schedule"}, set()))


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
# SAME-GROUP CROSS-PAIR ENUMERATION.
#
# This is the test class that would have caught both of Task 8's Criticals,
# and the reason they existed: an alias GROUP makes every member mutually
# equivalent, so adding an Nth phrase adds N-1 live equivalences, not one.
# The first cut measured only the four pairs the plan named and the
# cross-GROUP adversarial probes — never the same-group cross-pairs — so
# "staffing plan" (in the Key Personnel group) and "summary of approach" (in
# the Executive Summary group) shipped unmeasured and each hid a scored
# requirement end to end.
#
# Every same-group cross-pair must be enumerated here WITH a justification.
# test_every_same_group_cross_pair_is_enumerated fails if the table generates
# a pair this list does not contain, so a new group member cannot be added
# without consciously writing down what it now claims to be equivalent to.
# ---------------------------------------------------------------------------

_ENUMERATED_SAME_GROUP_CROSS_PAIRS: dict[tuple[str, str], str] = {
    # -- Cover letter group -------------------------------------------------
    ("cover letter", "letter of transmittal"): (
        "One artifact under two names; 'Letter of Transmittal' is the formal "
        "procurement term. A submission never contains both, and no RFP scores "
        "them as separate criteria."
    ),
    # -- Key personnel group ------------------------------------------------
    ("key personnel", "key staff"): (
        "Same ask (WHO is on the team: named individuals, roles, resumes) with "
        "'staff' substituted for 'personnel'. Note the group deliberately "
        "EXCLUDES 'staffing plan' — that is the HOW, and is routinely scored "
        "as a separate criterion with its own weight (Task 8 C1)."
    ),
    # -- Project schedule group ---------------------------------------------
    ("project schedule", "timeline"): (
        "One artifact; 'Timeline' is the most common modern shorthand for the "
        "proposed delivery schedule."
    ),
    ("project schedule", "project timeline"): (
        "Same artifact with 'timeline' substituted for 'schedule'; both name "
        "the proposed delivery plan and an RFP asks for only one of them."
    ),
    ("project timeline", "timeline"): (
        "Same artifact with and without the 'project' qualifier; in a proposal "
        "there is only one timeline being asked for."
    ),
    # -- Company overview group ---------------------------------------------
    ("about us", "company overview"): (
        "The firm's background/introduction section. NOTE: 'about us' reduces "
        "to {about} ('us' is filtered as <3 chars), so a bare 'About' section "
        "matches too — accepted deliberately, see SingleTokenAliasReductionTests."
    ),
    ("about us", "firm overview"): (
        "Same concept; 'firm' and 'company' are interchangeable in professional-"
        "services procurement."
    ),
    ("company overview", "firm overview"): (
        "'Firm' and 'company' are interchangeable in professional-services "
        "procurement; both name the background/introduction section and no "
        "RFP scores them separately."
    ),
}


def _all_same_group_cross_pairs() -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for group in PROPOSAL_SECTION_ALIAS_GROUPS:
        members = sorted(group)
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                pairs.add((a, b))
    return pairs


class SameGroupCrossPairTests(unittest.TestCase):
    def test_every_same_group_cross_pair_is_enumerated(self) -> None:
        """Adding a phrase to a group silently creates N-1 new equivalences.
        This forces each one to be written down and justified."""
        actual = _all_same_group_cross_pairs()
        enumerated = set(_ENUMERATED_SAME_GROUP_CROSS_PAIRS)
        self.assertEqual(
            actual - enumerated,
            set(),
            "alias group member(s) added without enumerating + justifying the "
            "new cross-pair(s) they create",
        )
        self.assertEqual(
            enumerated - actual,
            set(),
            "enumerated cross-pair(s) no longer exist in the table — stale entry",
        )

    def test_every_enumerated_cross_pair_actually_matches_both_directions(self) -> None:
        """The enumeration is a claim about behavior; verify it."""
        for (a, b), why in _ENUMERATED_SAME_GROUP_CROSS_PAIRS.items():
            with self.subTest(pair=(a, b)):
                self.assertTrue(_matches(a, b), f"{a!r}/{b!r} claimed equivalent: {why}")
                self.assertTrue(_matches(b, a), f"{b!r}/{a!r} claimed equivalent: {why}")

    def test_every_justification_is_non_trivial(self) -> None:
        """A one-word justification is not a justification."""
        for pair, why in _ENUMERATED_SAME_GROUP_CROSS_PAIRS.items():
            with self.subTest(pair=pair):
                self.assertGreater(len(why.split()), 8, f"{pair}: justification too thin")

    def test_no_group_is_a_singleton(self) -> None:
        """A one-member group can only ever match a text against itself, which
        _match_outline_sections' exact-title branch already covers — so it is
        dead weight that implies an equivalence the table does not deliver."""
        for group in PROPOSAL_SECTION_ALIAS_GROUPS:
            self.assertGreaterEqual(len(group), 2, f"singleton alias group: {set(group)}")


# ---------------------------------------------------------------------------
# The two Criticals, as end-to-end regressions.
# ---------------------------------------------------------------------------


class DeliberateAliasMissTests(unittest.TestCase):
    """Both pairs below were closed in Task 8's first cut and each was measured
    to hide a scored requirement end to end. They are now deliberate misses."""

    def test_c1_key_personnel_is_not_satisfied_by_a_staffing_plan_section(self) -> None:
        """WHO is on the team vs HOW the team is assembled — commonly two
        separately-weighted criteria."""
        self.assertFalse(_matches("Key Personnel", "Staffing Plan"))
        self.assertFalse(_matches("Staffing Plan", "Key Personnel"))

    def test_c2_executive_summary_is_not_satisfied_by_an_approach_summary_subsection(
        self,
    ) -> None:
        """"of" is a stopword, so "Summary of Approach" and "Approach Summary"
        are the same token set — and the latter is an ordinary sub-heading
        inside a Technical Approach section."""
        self.assertFalse(_matches("Executive Summary", "Approach Summary"))
        self.assertFalse(_matches("Executive Summary", "Summary of Approach"))
        self.assertFalse(_matches("Approach Summary", "Executive Summary"))

    def test_summary_of_approach_and_approach_summary_really_are_the_same_token_set(
        self,
    ) -> None:
        """Pins the stopword mechanic behind C2 so the reasoning above cannot
        silently stop being true if _MATCH_STOPWORDS changes."""
        self.assertEqual(
            _match_tokens(_normalize("Summary of Approach")),
            _match_tokens(_normalize("Approach Summary")),
        )


# ---------------------------------------------------------------------------
# I3 ruling: single-token alias reductions are ACCEPTED and pinned.
#
# _match_tokens drops tokens under 3 characters, so "about us" reduces to
# {about} — a bare "About" section therefore satisfies "Company Overview".
# Accepted deliberately: every English phrase that reduces to {about} in a
# proposal context ("About", "About Us") IS the company overview, so the
# broadening is real but not wrong. The alternative the review offered —
# requiring >= 2 surviving tokens for any alias match — was rejected by
# measurement: it would also disqualify {timeline}, killing Project
# Schedule/Timeline, one of only two pairs the alias channel still closes,
# and would take the hit rate to 6/10 (i.e. delete the entire feature).
#
# The real risk is not single-token aliases per se, it is a single-token
# alias whose token is GENERIC enough to name several different sections
# ("plan", "schedule", "summary", "cost"). So instead of a blanket token-count
# rule, the reductions are pinned below: any new single-token alias forces a
# deliberate edit here, and none may be a "boring" token.
# ---------------------------------------------------------------------------


class SingleTokenAliasReductionTests(unittest.TestCase):
    _EXPECTED_SINGLE_TOKEN_REDUCTIONS = {"timeline", "about"}

    def _single_token_reductions(self) -> set[str]:
        found = set()
        for group in PROPOSAL_SECTION_ALIAS_GROUPS:
            for phrase in group:
                tokens = _match_tokens(_normalize(phrase))
                if len(tokens) == 1:
                    found.add(next(iter(tokens)))
        return found

    def test_single_token_reductions_are_exactly_the_reviewed_set(self) -> None:
        self.assertEqual(
            self._single_token_reductions(),
            self._EXPECTED_SINGLE_TOKEN_REDUCTIONS,
            "a new single-token alias appeared — review it explicitly (I3)",
        )

    def test_no_alias_phrase_reduces_to_only_boring_tokens(self) -> None:
        """"summary"/"cost"/"pricing" etc. are too generic to identify a
        section on their own — the overlap channel already refuses to trust
        them, and the alias channel must not launder them back in."""
        for group in PROPOSAL_SECTION_ALIAS_GROUPS:
            for phrase in group:
                tokens = _match_tokens(_normalize(phrase))
                with self.subTest(phrase=phrase):
                    self.assertTrue(tokens, f"{phrase!r} reduces to no tokens at all")
                    self.assertFalse(
                        tokens <= _BORING_SHARED_TOKENS,
                        f"{phrase!r} reduces to boring-only tokens {tokens}",
                    )

    def test_the_accepted_about_broadening_is_real_and_bounded(self) -> None:
        """Document the accepted consequence, and show its bound: a bare
        "About" matches, but any longer About-something title does not."""
        self.assertTrue(_matches("Company Overview", "About"))
        self.assertFalse(_matches("Company Overview", "About Our Project Team"))
        self.assertFalse(_matches("Company Overview", "About the Subcontracting Plan"))


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
# The two Criticals reproduced END TO END through derive_legacy_fields — the
# real Phase 2 call site, not the matcher in isolation. Both of these FAILED
# against Task 8's first cut (7cdbeee): the requirement was marked satisfied,
# stayed out of missing(), and the now-live amendment therefore never gave it
# a section.
# ---------------------------------------------------------------------------


def _plan_key_personnel_and_staffing_plan_scored_separately() -> ProposalExecutionPlan:
    """C1: two scored criteria, one section. Common in federal and
    professional-services RFPs, which weight "who" and "how" separately."""
    plan = ProposalExecutionPlan(rfpId="rfp-c1")
    plan.opportunity.evaluation = EvaluationAnalysis(
        criteria=[
            EvaluationCriterion(name="Key Personnel", weight=15.0),
            EvaluationCriterion(name="Staffing Plan", weight=10.0),
        ],
        confidence=0.9,
    )
    plan.writing.proposal_outline = ProposalOutline(
        sections=[OutlineSection(id="sec-sp", title="Staffing Plan", order=1, required=True)],
        confidence=0.85,
    )
    plan.writing.section_plans = SectionPlans(plans=[], confidence=0.85)
    return plan


def _plan_executive_summary_vs_approach_summary() -> ProposalExecutionPlan:
    """C2: a scored Executive Summary against an outline whose only
    summary-ish section is a sub-heading of the technical approach."""
    plan = ProposalExecutionPlan(rfpId="rfp-c2")
    plan.opportunity.evaluation = EvaluationAnalysis(
        criteria=[EvaluationCriterion(name="Executive Summary", weight=20.0)],
        confidence=0.9,
    )
    plan.writing.proposal_outline = ProposalOutline(
        sections=[
            OutlineSection(id="sec-ta", title="Technical Approach", order=1, required=True),
            OutlineSection(id="sec-appsum", title="Approach Summary", order=2, required=True),
        ],
        confidence=0.85,
    )
    plan.writing.section_plans = SectionPlans(plans=[], confidence=0.85)
    return plan


class CriticalRegressionEndToEndTests(unittest.TestCase):
    """Post-incident correction: a live full-generation run duplicated 21
    sections because amend_outline_for_missing_requirements used to amend
    one new section per missing scored_criterion (a scoring CATEGORY name,
    not a deliverable) — see proposal_rfp_compliance.py's
    _ADD_ELIGIBLE_SOURCES module note. C1/C2 below still prove the alias
    table stops the two criteria from being silently merged into one
    section's satisfied_by; what changed is that the missing criterion now
    stays advisory (visible via ledger.missing(), never auto-amended) rather
    than getting its own duplicate stub section."""

    def test_c1_key_personnel_is_reported_missing_and_never_merged_or_amended(self) -> None:
        plan = _plan_key_personnel_and_staffing_plan_scored_separately()

        # Pre-amendment: the matcher alone must report it MISSING. Built
        # against the un-amended outline, exactly as derive_legacy_fields does
        # on its first pass.
        pre = build_requirement_ledger(
            [],
            list(plan.opportunity.evaluation.criteria),
            [RfpSectionMap(id="sec-sp", title="Staffing Plan")],
        )
        self.assertIn("Key Personnel", {r.text for r in pre.missing()})
        self.assertNotIn(
            "Staffing Plan",
            {r.text for r in pre.missing()},
            "the section that IS present must still be satisfied",
        )

        # A scored_criterion is never auto-amended — it stays advisory.
        legacy = derive_legacy_fields(plan)
        titles = [s.title for s in legacy["rfpSections"]]
        self.assertNotIn("Key Personnel", titles)
        self.assertIn("Staffing Plan", titles)

        ledger = legacy["requirementLedger"]
        key_personnel = next(r for r in ledger.requirements if r.text == "Key Personnel")
        staffing_plan = next(r for r in ledger.requirements if r.text == "Staffing Plan")
        self.assertNotEqual(
            key_personnel.satisfied_by,
            staffing_plan.satisfied_by,
            "two separately-scored criteria must not share one owning section",
        )
        self.assertEqual(staffing_plan.satisfied_by, ["sec-sp"])
        self.assertIn(
            "Key Personnel",
            {r.text for r in ledger.missing()},
            "stays advisory-missing for a human to judge, not silently added",
        )

    def test_c2_executive_summary_is_reported_missing_and_never_absorbed_or_amended(
        self,
    ) -> None:
        plan = _plan_executive_summary_vs_approach_summary()

        pre = build_requirement_ledger(
            [],
            list(plan.opportunity.evaluation.criteria),
            [
                RfpSectionMap(id="sec-ta", title="Technical Approach"),
                RfpSectionMap(id="sec-appsum", title="Approach Summary"),
            ],
        )
        self.assertIn("Executive Summary", {r.text for r in pre.missing()})

        legacy = derive_legacy_fields(plan)
        titles = [s.title for s in legacy["rfpSections"]]
        self.assertNotIn("Executive Summary", titles)
        self.assertIn("Approach Summary", titles, "must not disturb the existing subsection")

        ledger = legacy["requirementLedger"]
        exec_summary = next(r for r in ledger.requirements if r.text == "Executive Summary")
        self.assertNotIn(
            "sec-appsum",
            exec_summary.satisfied_by,
            "a 20-point criterion must not be absorbed by an approach subsection",
        )
        self.assertIn(
            "Executive Summary",
            {r.text for r in ledger.missing()},
            "stays advisory-missing for a human to judge, not silently added",
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
    def test_proof_a_a_missing_scored_technical_approach_is_never_auto_amended(
        self,
    ) -> None:
        """Post-incident correction: a scored_criterion (an evaluation-scoring
        CATEGORY name, not a deliverable) is never auto-amended into the
        outline — see amend_outline_for_missing_requirements' module note and
        proposal_rfp_compliance.py's _ADD_ELIGIBLE_SOURCES. It stays visible
        via ledger.missing() for a human to judge instead of becoming a
        duplicate stub section."""
        legacy = derive_legacy_fields(_plan_missing_technical_approach())
        titles = [s.title for s in legacy["rfpSections"]]
        self.assertNotIn("Technical Approach", titles)
        self.assertIn("Pricing Proposal", titles, "must not drop the existing section")

        ledger = legacy["requirementLedger"]
        technical_approach = next(r for r in ledger.requirements if r.text == "Technical Approach")
        self.assertFalse(technical_approach.satisfied_by)
        self.assertIn("Technical Approach", {r.text for r in ledger.missing()})

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
