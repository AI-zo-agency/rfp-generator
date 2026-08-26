"""Every scored evaluation criterion must reach the proposal as its own section.

Observed on a live RFP (CNM P-472, 1,000 points across seven scored sections):
the outline emitted ONE "EXHIBIT 1: Evaluation Criteria Response Form" tab and
unscored filler (a restatement of the buyer's Scope of Work, an insurance
acknowledgment for a document the RFP says to send only on request), while
Strategic Planning (160 pts), Media (120 pts) and Public Relations (120 pts) had
no section at all — 400 points unanswered.

Three defects, covered here:
  1. Evaluation criteria were extracted as flat {name, weight}, so item codes,
     the buyer's own asks, and per-field character limits never reached anyone.
  2. Nothing verified, after the lean filter and the hard cap, that each scored
     criterion still had a home. Every pass in that chain is subtractive.
  3. The response-form wrapper tab absorbed criteria by title overlap — its
     title contained "Public Relations", so a 120-point criterion looked covered.

Also covers the two follow-ups: an ALREADY-GENERATED proposal must gain its
missing scored sections through Complete & clean without regenerating (and
without disturbing sections that already answer a criterion), and the character
limit must come from each RFP rather than a constant.
"""

from __future__ import annotations

import unittest

from app.models.proposal import (
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.services.proposal_evaluation_coverage import (
    char_limit_to_word_budget,
    clean_criterion_name,
    criterion_char_limit,
    criterion_writer_directive,
    ensure_scored_criteria_coverage,
    evaluation_extraction_looks_degenerate,
    evaluation_is_published_response_form,
    find_response_char_limit,
    min_outline_sections_for_evaluation,
    rfp_publishes_a_points_table,
    uncovered_scored_criteria,
)
from app.services.proposal_fulfill_rfp_structure import (
    ensure_missing_scored_section_stubs,
    specs_from_scored_criteria,
)
from app.services.proposal_intelligence.schemas import (
    EvaluationAnalysis,
    EvaluationCriterion,
    EvaluationCriterionItem,
    OutlineSection,
)
from app.services.proposal_outline_dedup import (
    enforce_outline_section_cap,
    max_rfp_outline_sections,
    stamp_outline_evaluation_weights,
)
from app.services.proposal_presubmit_review import (
    _response_blocks,
    _scan_response_char_limits,
)

TIMESTAMP = "2026-08-26T00:00:00Z"


def _build_section(raw: dict) -> OutlineSection:
    return OutlineSection.model_validate(raw)


def cnm_evaluation() -> EvaluationAnalysis:
    """The real CNM P-472 scoreboard: seven scored sections, 1,000 points."""
    return EvaluationAnalysis(
        scoredResponseForm=True,
        totalPoints=1000,
        responseCharLimit=4000,
        criteria=[
            EvaluationCriterion(
                name="Background and Qualifications",
                itemCode="SECTION I",
                weight=200,
                items=[
                    EvaluationCriterionItem(
                        itemCode=f"I.{n}", ask=f"ask {n}", weight=40
                    )
                    for n in range(1, 6)
                ],
            ),
            EvaluationCriterion(
                name="Relevant Experience", itemCode="SECTION II", weight=120
            ),
            EvaluationCriterion(
                name="Strategic Planning", itemCode="SECTION III", weight=160
            ),
            EvaluationCriterion(name="Creativity", itemCode="SECTION IV", weight=80),
            EvaluationCriterion(name="Media", itemCode="SECTION V", weight=120),
            EvaluationCriterion(
                name="Public Relations", itemCode="SECTION VI", weight=120
            ),
            EvaluationCriterion(
                name="Economy and Price", itemCode="SECTION VII", weight=200
            ),
        ],
    )


def observed_cnm_outline() -> list[OutlineSection]:
    """What the pipeline actually produced — 400 scored points with no section."""
    return [
        OutlineSection(
            id="a",
            title="EXHIBIT 3: DEBARMENT/SUSPENSION STATUS & NON-COLLUSION AFFIDAVIT",
            order=1,
            submissionInstrument="form",
        ),
        OutlineSection(
            id="b",
            title=(
                "EXHIBIT 1: Evaluation Criteria Response Form — Integrated "
                "Communications, Marketing, Branding & Public Relations Capabilities"
            ),
            order=2,
        ),
        OutlineSection(
            id="c",
            title="Price/Cost Proposal — Professional Services Rates and Fee Structure",
            order=3,
            submissionInstrument="cost",
        ),
        OutlineSection(
            id="d", title="REFERENCES", order=4, submissionInstrument="references"
        ),
    ]


class ScoredCriteriaReachTheOutlineTests(unittest.TestCase):
    def test_every_scored_criterion_ends_with_a_section(self) -> None:
        evaluation = cnm_evaluation()
        sections = observed_cnm_outline()

        before = uncovered_scored_criteria(sections, evaluation)
        self.assertTrue(
            before, "sanity: the observed outline really did leave criteria uncovered"
        )

        kept, added, _dropped = ensure_scored_criteria_coverage(
            sections, evaluation, section_factory=_build_section
        )

        self.assertEqual(
            uncovered_scored_criteria(kept, evaluation),
            [],
            "no scored criterion may leave this pass without a section",
        )
        titles = [s.title for s in kept]
        for expected in ("Strategic Planning", "Media", "Public Relations"):
            self.assertTrue(
                any(expected in t for t in titles),
                f"{expected} was scored but got no tab",
            )
        self.assertTrue(added)

    def test_injected_tabs_carry_points_and_survive_later_hygiene(self) -> None:
        """Injected tabs must be un-droppable — every pass downstream subtracts."""
        evaluation = cnm_evaluation()
        kept, _added, _dropped = ensure_scored_criteria_coverage(
            observed_cnm_outline(), evaluation, section_factory=_build_section
        )
        injected = [s for s in kept if s.id.startswith("rfp-eval-")]
        self.assertTrue(injected)
        for section in injected:
            self.assertTrue(section.protect_from_cap, section.title)
            self.assertTrue(
                section.evaluation_weight and section.evaluation_weight > 0,
                section.title,
            )

    def test_a_criterion_already_answered_is_not_duplicated(self) -> None:
        """"Economy and Price" is answered by the existing Price/Cost tab."""
        evaluation = cnm_evaluation()
        kept, added, _dropped = ensure_scored_criteria_coverage(
            observed_cnm_outline(), evaluation, section_factory=_build_section
        )
        pricing_tabs = [
            s for s in kept if "price" in s.title.casefold() or "cost" in s.title.casefold()
        ]
        self.assertEqual(
            len(pricing_tabs), 1, f"pricing was duplicated: {[s.title for s in pricing_tabs]}"
        )
        self.assertNotIn(
            "SECTION VII — Economy and Price", [line.split(" (")[0] for line in added]
        )

    def test_the_response_form_wrapper_never_counts_as_coverage(self) -> None:
        """The wrapper's title contains "Public Relations" — that is not an answer."""
        evaluation = EvaluationAnalysis(
            scoredResponseForm=True,
            criteria=[
                EvaluationCriterion(
                    name="Public Relations", itemCode="SECTION VI", weight=120
                )
            ],
        )
        wrapper = [
            OutlineSection(
                id="w",
                title=(
                    "EXHIBIT 1: Evaluation Criteria Response Form — Integrated "
                    "Communications, Marketing, Branding & Public Relations Capabilities"
                ),
                order=1,
            )
        ]
        kept, added, dropped = ensure_scored_criteria_coverage(
            wrapper, evaluation, section_factory=_build_section
        )
        self.assertTrue(added, "the wrapper must not satisfy a scored criterion")
        self.assertTrue(dropped, "the wrapper is redundant once criteria have tabs")
        self.assertFalse(
            any("Response Form" in s.title for s in kept),
            "wrapper survived alongside the criteria it was standing in for",
        )

    def test_scored_body_sections_are_ordered_ahead_of_the_forms_package(self) -> None:
        evaluation = cnm_evaluation()
        kept, _added, _dropped = ensure_scored_criteria_coverage(
            observed_cnm_outline(), evaluation, section_factory=_build_section
        )
        first_form = next(
            i
            for i, s in enumerate(kept)
            if s.submission_instrument in {"form", "references"}
        )
        last_injected = max(
            i for i, s in enumerate(kept) if s.id.startswith("rfp-eval-")
        )
        self.assertLess(
            last_injected,
            first_form,
            "scored body sections belong ahead of the signed-forms package",
        )
        self.assertEqual([s.order for s in kept], list(range(1, len(kept) + 1)))

    def test_no_criteria_means_no_change(self) -> None:
        sections = observed_cnm_outline()
        kept, added, dropped = ensure_scored_criteria_coverage(
            sections, EvaluationAnalysis(), section_factory=_build_section
        )
        self.assertEqual(added, [])
        self.assertEqual(dropped, [])
        self.assertEqual([s.title for s in kept], [s.title for s in sections])


class SectionCapNeverSqueezesOutScoredWorkTests(unittest.TestCase):
    def test_cap_floor_rises_to_the_rfp_s_own_criteria_count(self) -> None:
        """The buyer decides how many scored sections exist, not our page math."""
        self.assertEqual(min_outline_sections_for_evaluation(cnm_evaluation()), 7)

        # The real squeeze: an RFP scoring more sections than the 18-tab ceiling.
        wide = EvaluationAnalysis(
            criteria=[
                EvaluationCriterion(name=f"Scored Area {n}", weight=25)
                for n in range(1, 23)
            ]
        )
        floor = min_outline_sections_for_evaluation(wide)
        self.assertEqual(floor, 22)
        self.assertLess(
            max_rfp_outline_sections(40),
            floor,
            "sanity: the page-budget ceiling would drop four scored sections",
        )
        self.assertGreaterEqual(max_rfp_outline_sections(40, min_sections=floor), floor)

    def test_the_floor_never_shrinks_the_existing_cap(self) -> None:
        """A scoreboard smaller than the cap must not tighten it."""
        floor = min_outline_sections_for_evaluation(cnm_evaluation())
        self.assertEqual(
            max_rfp_outline_sections(30, min_sections=floor),
            max_rfp_outline_sections(30),
        )

    def test_an_unscored_rfp_keeps_the_existing_cap(self) -> None:
        self.assertEqual(
            max_rfp_outline_sections(20, min_sections=0), max_rfp_outline_sections(20)
        )


class ResponseCharLimitIsReadFromEachRfpTests(unittest.TestCase):
    def test_the_limit_is_whatever_this_rfp_states(self) -> None:
        cases = {
            "Each response form field allows a maximum of 4,000 characters.": 4000,
            "Responses are limited to 2500 characters per field.": 2500,
            "A 1,000 character limit applies to each answer.": 1000,
            "Each narrative response may not exceed 10,000 characters.": 10000,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(find_response_char_limit(text), expected)

    def test_an_rfp_stating_no_limit_yields_none(self) -> None:
        self.assertIsNone(
            find_response_char_limit(
                "Proposals shall be submitted through the online portal by 3:00 PM MT."
            )
        )

    def test_page_counts_and_small_numbers_are_not_mistaken_for_limits(self) -> None:
        self.assertIsNone(find_response_char_limit("Limit responses to 30 characters"))
        self.assertIsNone(find_response_char_limit("Proposals may not exceed 20 pages."))

    def test_the_tightest_stated_limit_governs(self) -> None:
        text = (
            "Narrative fields allow a maximum of 8,000 characters. "
            "Summary fields allow a maximum of 2,000 characters."
        )
        self.assertEqual(find_response_char_limit(text), 2000)

    def test_word_budget_scales_with_the_number_of_capped_responses(self) -> None:
        one = char_limit_to_word_budget(4000, responses=1)
        five = char_limit_to_word_budget(4000, responses=5)
        self.assertIsNotNone(one)
        self.assertIsNotNone(five)
        assert one and five
        self.assertGreater(five, one * 4, "five capped fields hold ~5x one field")
        self.assertIsNone(char_limit_to_word_budget(None))

    def test_item_level_limits_win_over_the_package_default(self) -> None:
        evaluation = EvaluationAnalysis(responseCharLimit=4000)
        criterion = EvaluationCriterion(
            name="Strategic Planning",
            weight=160,
            items=[EvaluationCriterionItem(itemCode="III.1", responseCharLimit=1500)],
        )
        self.assertEqual(criterion_char_limit(criterion, evaluation), 1500)
        self.assertEqual(
            criterion_char_limit(EvaluationCriterion(name="x", weight=1), evaluation),
            4000,
        )


class WriterLearnsTheScoredAsksTests(unittest.TestCase):
    def test_the_brief_names_every_numbered_ask_and_the_limit(self) -> None:
        evaluation = cnm_evaluation()
        directive = criterion_writer_directive(evaluation.criteria[0], evaluation)
        for code in ("I.1", "I.2", "I.3", "I.4", "I.5"):
            self.assertIn(code, directive)
        self.assertIn("200", directive)
        self.assertIn("4000", directive)

    def test_no_stated_limit_means_no_limit_language_in_the_brief(self) -> None:
        evaluation = EvaluationAnalysis(
            criteria=[EvaluationCriterion(name="Media", weight=120)]
        )
        directive = criterion_writer_directive(evaluation.criteria[0], evaluation)
        self.assertNotIn("HARD LIMIT", directive)


class OverLongResponsesAreBlockedTests(unittest.TestCase):
    def _draft(self, content: str) -> ProposalDraft:
        return ProposalDraft(
            rfpId="r",
            updatedAt=TIMESTAMP,
            sections=[
                ProposalSection(
                    id="s1",
                    title="SECTION I — Background and Qualifications",
                    content=content,
                    status="generated",
                )
            ],
        )

    def test_each_capped_field_is_measured_separately(self) -> None:
        """A tab holding five answers fills five fields, not one."""
        under = "word " * 700
        over = "word " * 1200
        draft = self._draft(f"## I.1 Position\n{under}\n\n## I.2 Capabilities\n{over}\n")

        blocks = _response_blocks(draft.sections[0].content)
        self.assertEqual(len(blocks), 2)

        issues = _scan_response_char_limits(
            draft=draft,
            rfp_text="Each response form field allows a maximum of 4,000 characters.",
        )
        self.assertEqual(len(issues), 1, "only the over-limit answer should be flagged")
        self.assertEqual(issues[0].severity, "critical")
        self.assertIn("I.2", issues[0].message)

    def test_an_rfp_with_no_stated_limit_flags_nothing(self) -> None:
        draft = self._draft("## I.1 Position\n" + "word " * 5000)
        self.assertEqual(
            _scan_response_char_limits(draft=draft, rfp_text="No caps stated."), []
        )

    def test_a_different_rfp_limit_moves_the_threshold(self) -> None:
        draft = self._draft("## I.1 Position\n" + "word " * 400)  # ~2,000 chars
        self.assertEqual(
            _scan_response_char_limits(
                draft=draft, rfp_text="a maximum of 4,000 characters"
            ),
            [],
        )
        self.assertEqual(
            len(
                _scan_response_char_limits(
                    draft=draft, rfp_text="limited to 1,000 characters"
                )
            ),
            1,
        )


class ExistingProposalsAreRepairedNotRegeneratedTests(unittest.TestCase):
    """Complete & clean must recover missing scored sections from the persisted
    ledger — no Phase 2 re-run, no LLM call, and no damage to good sections."""

    def _research(self) -> ProposalResearchCache:
        return ProposalResearchCache(
            rfpId="r",
            updatedAt=TIMESTAMP,
            requirementLedger=RequirementLedger(
                requirements=[
                    LedgerRequirement(
                        id="c1",
                        text="Strategic Planning",
                        source="scored_criterion",
                        points=160,
                    ),
                    LedgerRequirement(
                        id="c2", text="Media", source="scored_criterion", points=120
                    ),
                    LedgerRequirement(
                        id="c3",
                        text="Public Relations",
                        source="scored_criterion",
                        points=120,
                    ),
                    LedgerRequirement(
                        id="c4",
                        text="Economy and Price",
                        source="scored_criterion",
                        points=200,
                    ),
                    LedgerRequirement(id="f1", text="Signed W-9", source="form"),
                ]
            ),
        )

    def test_specs_come_from_the_persisted_scoreboard_only(self) -> None:
        specs = specs_from_scored_criteria(self._research())
        titles = [s.rfp_title for s in specs]
        self.assertEqual(
            titles,
            ["Strategic Planning", "Media", "Public Relations", "Economy and Price"],
        )
        self.assertNotIn("Signed W-9", titles, "forms are not narrative sections")
        self.assertTrue(all(s.evaluation_weight for s in specs))

    def test_no_ledger_means_no_specs(self) -> None:
        self.assertEqual(specs_from_scored_criteria(None), [])
        self.assertEqual(
            specs_from_scored_criteria(
                ProposalResearchCache(rfpId="r", updatedAt=TIMESTAMP)
            ),
            [],
        )

    def test_an_already_generated_draft_gains_only_what_it_is_missing(self) -> None:
        draft = ProposalDraft(
            rfpId="r",
            updatedAt=TIMESTAMP,
            sections=[
                ProposalSection(
                    id="s1",
                    title="Section 1 — Company Overview",
                    content="Real drafted company content. " * 30,
                    status="generated",
                ),
                ProposalSection(
                    id="s2",
                    title="Price/Cost Proposal — Professional Services Rates",
                    content="Real drafted pricing content. " * 30,
                    status="generated",
                ),
            ],
        )
        before = {s.id: s.content for s in draft.sections}

        out, logs = ensure_missing_scored_section_stubs(
            draft, specs_from_scored_criteria(self._research())
        )

        titles = [s.title for s in out.sections]
        for expected in ("Strategic Planning", "Media", "Public Relations"):
            self.assertIn(expected, titles, f"{expected} was scored but never added")

        # The pricing tab already answers "Economy and Price" — no twin.
        pricing = [t for t in titles if "Price" in t or "Cost" in t]
        self.assertEqual(len(pricing), 1, f"pricing duplicated: {pricing}")

        for section in out.sections:
            if section.id in before:
                self.assertEqual(
                    section.content,
                    before[section.id],
                    "Complete & clean must not rewrite sections that are already good",
                )
        self.assertTrue(logs)


class CoverageRunsBeforeTheCapTests(unittest.TestCase):
    """Order matters: coverage must precede the hard cap.

    Run the cap first and it budgets its free slots against an outline the
    scored tabs are still missing from — unscored filler claims slots the
    buyer's own scored sections are about to take, and the outline finishes
    OVER cap while still carrying padding the RFP never asked for. Measured on
    the CNM outline: cap-then-coverage gave 14 sections with 4 filler tabs
    surviving; coverage-then-cap gives 11 with every filler tab dropped.
    """

    def _cnm_outline_with_filler(self) -> list[OutlineSection]:
        def mk(i: int, title: str, instrument: str | None = None) -> OutlineSection:
            return OutlineSection(
                id=f"s{i}", title=title, order=i, submissionInstrument=instrument
            )

        return [
            mk(1, "EXHIBIT 3: DEBARMENT/SUSPENSION STATUS & NON-COLLUSION AFFIDAVIT", "form"),
            mk(2, "EXHIBIT 1: Evaluation Criteria Response Form — Public Relations Capabilities"),
            mk(3, "EXHIBIT 6: OFFEROR'S REQUESTED CHANGES TO RFP TERMS AND CONDITIONS", "form"),
            # Filler: restates the buyer's own Scope of Work back at them.
            mk(4, "SECTION C: Scope of Work/Specifications — Communications, Marketing, Branding"),
            mk(5, "Price/Cost Proposal — Professional Services Rates and Fee Structure", "cost"),
            mk(6, "REFERENCES", "references"),
            # Filler: real content, but not a section this RFP scores.
            mk(7, "Higher Education, Government, and Large Institutional Experience"),
            mk(8, "EXHIBIT 2: Offeror's Acceptance of the RFP Amendments, Terms and Conditions", "form"),
            # Filler: the RFP says send these only on request.
            mk(9, "Insurance Requirements Acknowledgment"),
            mk(10, "Sample Services Agreement Acknowledgment"),
        ]

    def _run(self, *, coverage_first: bool) -> tuple[list[OutlineSection], list[str]]:
        evaluation = cnm_evaluation()
        sections = self._cnm_outline_with_filler()
        cap = max_rfp_outline_sections(
            None, min_sections=min_outline_sections_for_evaluation(evaluation)
        )
        stamp_outline_evaluation_weights(sections, evaluation.criteria)
        dropped: list[str] = []
        if coverage_first:
            sections, _added, wrapper_dropped = ensure_scored_criteria_coverage(
                sections, evaluation, section_factory=_build_section
            )
            sections, cap_dropped = enforce_outline_section_cap(sections, cap)
        else:
            sections, cap_dropped = enforce_outline_section_cap(sections, cap)
            sections, _added, wrapper_dropped = ensure_scored_criteria_coverage(
                sections, evaluation, section_factory=_build_section
            )
        dropped = list(wrapper_dropped) + list(cap_dropped)
        return sections, dropped

    def test_coverage_first_drops_every_unscored_filler_tab(self) -> None:
        kept, _dropped = self._run(coverage_first=True)
        titles = " | ".join(s.title for s in kept)
        for filler in (
            "Scope of Work",
            "Higher Education",
            "Insurance Requirements Acknowledgment",
            "Sample Services Agreement",
            "Response Form",
        ):
            self.assertNotIn(filler, titles, f"unscored filler survived: {filler}")

    def test_coverage_first_keeps_every_scored_criterion_and_required_form(self) -> None:
        evaluation = cnm_evaluation()
        kept, _dropped = self._run(coverage_first=True)
        self.assertEqual(uncovered_scored_criteria(kept, evaluation), [])
        titles = " | ".join(s.title for s in kept)
        for required in ("EXHIBIT 2", "EXHIBIT 3", "EXHIBIT 6", "REFERENCES", "Price/Cost"):
            self.assertIn(required, titles, f"required submittal dropped: {required}")

    def test_the_wrong_order_is_measurably_worse(self) -> None:
        """Guards the ordering itself — not just today's output."""
        good, _ = self._run(coverage_first=True)
        bad, _ = self._run(coverage_first=False)
        self.assertLess(
            len(good),
            len(bad),
            "capping before coverage must not produce the leaner outline",
        )
        bad_titles = " | ".join(s.title for s in bad)
        self.assertIn(
            "Scope of Work",
            bad_titles,
            "sanity: the wrong order really does admit filler",
        )


class DegenerateExtractionIsDetectedTests(unittest.TestCase):
    """A silent extraction collapse is how 1,000 points disappear.

    Real run (rfp-jw-3300d3eb, 2026-08-26): the merged opportunity pass carries
    understanding + compliance + scope + evaluation + success in ONE response.
    Run long, and the evaluation block is what degrades. CNM's seven-section,
    1,000-point criteria form came back as a single criterion named
    "Evaluation Criteria Response Form" with points=None.

    Everything downstream then behaved CORRECTLY on garbage: no criterion
    carried points, so coverage found nothing to guarantee, and the outline
    finished with six exhibit tabs and no scored section at all. The guarantee
    is only as strong as the extraction, so the collapse must be detectable.
    """

    RFP_WITH_POINTS = (
        "SECTION III Strategic Planning - UP TO 160 POINTS POSSIBLE\n"
        "III.1 Describe your approach. UP TO 40 POINTS POSSIBLE\n"
    )

    def test_the_real_collapse_is_flagged(self) -> None:
        collapsed = EvaluationAnalysis(
            criteria=[
                EvaluationCriterion(name="Evaluation Criteria Response Form", weight=None)
            ]
        )
        self.assertTrue(
            evaluation_extraction_looks_degenerate(collapsed, self.RFP_WITH_POINTS)
        )

    def test_empty_and_pointless_criteria_are_flagged(self) -> None:
        for label, evaluation in (
            ("no criteria", EvaluationAnalysis()),
            (
                "criteria carrying no points",
                EvaluationAnalysis(criteria=[EvaluationCriterion(name="Strategic Planning")]),
            ),
        ):
            with self.subTest(label):
                self.assertTrue(
                    evaluation_extraction_looks_degenerate(
                        evaluation, self.RFP_WITH_POINTS
                    )
                )

    def test_a_healthy_extraction_is_not_flagged(self) -> None:
        self.assertFalse(
            evaluation_extraction_looks_degenerate(
                cnm_evaluation(), self.RFP_WITH_POINTS
            )
        )

    def test_a_genuine_single_scored_criterion_is_not_flagged(self) -> None:
        """One scored criterion is a real shape — only the wrapper name is suspect."""
        real = EvaluationAnalysis(
            criteria=[EvaluationCriterion(name="Technical Approach", weight=100)]
        )
        self.assertFalse(
            evaluation_extraction_looks_degenerate(real, self.RFP_WITH_POINTS)
        )

    def test_an_unscored_rfp_never_triggers_a_re_extraction(self) -> None:
        """No points table means nothing was lost — never pay for a retry."""
        self.assertFalse(rfp_publishes_a_points_table("Submit your quote by Friday."))
        self.assertFalse(
            evaluation_extraction_looks_degenerate(
                EvaluationAnalysis(), "Submit your quote by Friday."
            )
        )


class OnlyPublishedSectionListsAreAutoCreatedTests(unittest.TestCase):
    """The line between a scoring CATEGORY and a published SECTION LIST.

    Conflating them caused a documented incident: auto-creating a section for
    a bare category name ("Technical Approach", 30 pts) minted duplicate stubs
    beside tabs that already answered them — the matcher was wrong 5 times out
    of 5 on real RFP wording. Those stay advisory for a human to judge.

    A buyer-published response form is the opposite case: numbered sections,
    the buyer's own codes, points per item. There is no judgment call to
    defer, and a missing one is simply forfeited points.

    I broke this invariant once by gating on "has points" instead of "has
    structure". These tests pin both sides.
    """

    def test_bare_scoring_categories_are_never_auto_created(self) -> None:
        categories = EvaluationAnalysis(
            criteria=[
                EvaluationCriterion(name="Technical Approach", weight=30),
                EvaluationCriterion(name="Management Plan", weight=20),
            ]
        )
        self.assertFalse(evaluation_is_published_response_form(categories))
        kept, added, dropped = ensure_scored_criteria_coverage(
            [], categories, section_factory=_build_section
        )
        self.assertEqual(added, [], "a scoring category must stay advisory")
        self.assertEqual(kept, [])
        self.assertEqual(dropped, [])

    def test_a_published_response_form_is_auto_created(self) -> None:
        evaluation = cnm_evaluation()
        self.assertTrue(evaluation_is_published_response_form(evaluation))
        _kept, added, _dropped = ensure_scored_criteria_coverage(
            [], evaluation, section_factory=_build_section
        )
        self.assertEqual(len(added), len(evaluation.criteria))

    def test_item_codes_alone_signal_a_published_list(self) -> None:
        """No explicit flag, but the buyer numbered the sections."""
        coded = EvaluationAnalysis(
            criteria=[
                EvaluationCriterion(name="Approach", itemCode="Tab 3", weight=30),
                EvaluationCriterion(name="Experience", itemCode="Tab 4", weight=20),
            ]
        )
        self.assertTrue(evaluation_is_published_response_form(coded))

    def test_a_single_criterion_is_never_a_published_list(self) -> None:
        one = EvaluationAnalysis(
            criteria=[EvaluationCriterion(name="Technical Approach", weight=100)]
        )
        self.assertFalse(evaluation_is_published_response_form(one))


class TitlesDropWrappedPointsTextTests(unittest.TestCase):
    """A PDF wrapping a scoring row mid-phrase leaks into the section title.

    Real output before this: "SECTION VII ECONOMY AND PRICE - UP",
    "SECTION III Strategic Planning - UP TO 160". The extractor copies what it
    sees, and the fragment rides into the heading an evaluator reads.
    """

    def test_wrapped_points_fragments_are_stripped(self) -> None:
        cases = {
            "SECTION VII ECONOMY AND PRICE - UP": "SECTION VII ECONOMY AND PRICE",
            "SECTION III Strategic Planning - UP TO 160": "SECTION III Strategic Planning",
            "SECTION V Media - UP TO 120 POINTS": "SECTION V Media",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(clean_criterion_name(raw), expected)

    def test_clean_titles_are_left_alone(self) -> None:
        for name in (
            "SECTION I Background and Qualifications",
            "Economy and Price",
            "Media",
            "Follow - up Services",
        ):
            with self.subTest(name=name):
                self.assertEqual(clean_criterion_name(name), name)

    def test_planner_emitted_titles_are_cleaned_in_place(self) -> None:
        """Not just titles this pass creates — the planner copies them too."""
        dirty = [
            OutlineSection(id="a", title="SECTION III Strategic Planning - UP TO 160", order=1)
        ]
        kept, _added, _dropped = ensure_scored_criteria_coverage(
            dirty, cnm_evaluation(), section_factory=_build_section
        )
        self.assertIn(
            "SECTION III Strategic Planning", [s.title for s in kept]
        )
        self.assertFalse(any("UP TO" in s.title for s in kept))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
