"""Task 14: ADD must never auto-add a scored_criterion, and a reconcile pass
that would add too many sections in one click must decline and report
instead of silently applying.

Root defect (live incident, HEAD d8f0df2): a real user clicked Scan-RFP on a
23-section proposal with a 12-page limit. The reconciler added 21 new
sections — the banner read "Added 21 missing required section(s)
('Relevant Experience', 'Strategic Approach and Methodology', 'Personnel and
Project Management', 'Reporting and Performance Optimization', 'Cost and
Overall Value', +16 more)". Every one of those five names is the RFP's
EVALUATION CRITERION name (a scoring category), not a deliverable — the
proposal already covered each of them under a requirement-phrased section
title the lexical matcher shares no meaningful tokens with:

    MISS  Relevant Experience                    / Examples of similar work performed within the past five (5) years
    MISS  Personnel and Project Management       / Team members who will be assigned to MSU Denver
    MISS  Strategic Approach and Methodology     / Brief description of campaign planning methodology and competitor...
    MISS  Cost and Overall Value                 / Pricing structure
    MISS  Reporting and Performance Optimization / Sample reporting dashboard or campaign report

``RequirementLedger.missing()`` therefore reported every scored criterion as
uncovered, and the old ADD applied that signal unconditionally, duplicating
each one. Loosening the matcher was explicitly rejected (see
test_outline_coverage.py / test_section_aliases.py's false-positive
battery) — the fix instead restricts WHICH ledger sources ADD ever applies
to (proposal_rfp_compliance.py's module note, _ADD_ELIGIBLE_SOURCES), and
adds a blast-radius guard as defense in depth against a reconciler bug of
this shape ever silently ballooning a document again.
"""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.models.rfp import RfpRecord
from app.services.proposal_rfp_compliance import (
    _BLAST_RADIUS_MAX_ADDITIONS,
    reconcile_requirement_ledger,
)


def _rfp(**overrides) -> RfpRecord:
    fields = dict(
        id="rfp-1",
        title="Test RFP",
        client="Client",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-05",
        lastActivityNote="note",
    )
    fields.update(overrides)
    return RfpRecord(**fields)


def _req(rid: str, text: str, **kw) -> LedgerRequirement:
    kw.setdefault("source", "required_content")
    kw.setdefault("mandatory", True)
    kw.setdefault("satisfiedBy", [])
    return LedgerRequirement(id=rid, text=text, **kw)


def _section(sid: str, title: str, content: str, **kw) -> ProposalSection:
    return ProposalSection(id=sid, title=title, content=content, **kw)


def _draft(*sections: ProposalSection) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-1", sections=list(sections), updatedAt="2026-08-06T00:00:00Z"
    )


def _research(ledger: RequirementLedger | None) -> ProposalResearchCache:
    return ProposalResearchCache(
        rfpId="rfp-1", requirementLedger=ledger, updatedAt="2026-08-06T00:00:00Z"
    )


def _para(word: str, count: int) -> str:
    return " ".join([word] * count)


# The five real (criterion name, covering section title) pairs from the live
# incident banner, verified by execution to be 5-of-5 lexical misses.
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
        "Brief description of campaign planning methodology and competitor analysis",
    ),
    ("Cost and Overall Value", "Pricing structure"),
    (
        "Reporting and Performance Optimization",
        "Sample reporting dashboard or campaign report",
    ),
]


class ScoredCriteriaAreNeverAutoAddedTests(unittest.TestCase):
    """Required test 1: each of the five real criterion/section pairs from
    the incident is NOT auto-added, and is reported as advisory instead."""

    def _ledger_with_the_five_real_criteria(self) -> RequirementLedger:
        return RequirementLedger(
            requirements=[
                _req(
                    f"crit-{i}",
                    criterion_name,
                    source="scored_criterion",
                    points=20.0,
                    satisfiedBy=[],  # the matcher missed this in the real run
                )
                for i, (criterion_name, _covering_section) in enumerate(
                    REAL_SCORED_CRITERIA_FROM_INCIDENT
                )
            ]
        )

    def test_none_of_the_five_scored_criteria_are_auto_added(self) -> None:
        ledger = self._ledger_with_the_five_real_criteria()
        # The draft already covers all five under requirement-phrased
        # titles — exactly the live incident's proposal shape. None of
        # those covering sections satisfy the ledger (the matcher missed
        # them), which is exactly the defect condition.
        draft = _draft(
            *[
                _section(f"sec-{i}", covering_title, "Real proposal content here.")
                for i, (_c, covering_title) in enumerate(
                    REAL_SCORED_CRITERIA_FROM_INCIDENT
                )
            ]
        )
        research = _research(ledger)

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        self.assertEqual(
            result.applied_additions,
            [],
            "a scored_criterion must never be auto-added, however it is worded",
        )
        self.assertEqual(
            len(result.draft.sections),
            5,
            "no duplicate stub sections were created for the five criteria",
        )

    def test_each_missing_scored_criterion_is_reported_as_advisory(self) -> None:
        ledger = self._ledger_with_the_five_real_criteria()
        draft = _draft(_section("s1", "Cover Letter", "Dear Sir or Madam..."))
        research = _research(ledger)

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        advisory_texts = {a.requirement_text for a in result.advisory_scored_criteria}
        expected_texts = {name for name, _ in REAL_SCORED_CRITERIA_FROM_INCIDENT}
        self.assertEqual(
            advisory_texts,
            expected_texts,
            "every one of the five real criteria must be reported as advisory",
        )
        self.assertTrue(
            any(
                "5 scored criteria may not be covered" in line
                or "scored criteri" in line and "may not be covered" in line
                for line in result.logs
            ),
            f"expected an advisory banner line, got: {result.logs}",
        )


class GenuinelyMissingRequiredContentIsStillAddedTests(unittest.TestCase):
    """Required test 2: a genuinely missing required_content item (e.g. a
    cover letter absent entirely) is still auto-added — the fix narrows WHO
    gets ADD, not whether ADD still works at all."""

    def test_missing_required_content_cover_letter_is_added(self) -> None:
        ledger = RequirementLedger(
            requirements=[
                _req(
                    "r-cover-letter",
                    "A signed cover letter",
                    source="required_content",
                    satisfiedBy=[],
                )
            ]
        )
        draft = _draft(_section("s1", "Approach", "Our approach is sound."))
        research = _research(ledger)

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        self.assertEqual(len(result.applied_additions), 1)
        self.assertEqual(result.applied_additions[0].requirement_id, "r-cover-letter")
        added = next(s for s in result.draft.sections if s.id == "ledger-r-cover-letter")
        self.assertIn("[MANUAL FILL", added.content)
        self.assertIn("A signed cover letter", added.content)

    def test_missing_form_item_is_also_added(self) -> None:
        ledger = RequirementLedger(
            requirements=[
                _req(
                    "r-form-1",
                    "Standard Form 330",
                    source="form",
                    satisfiedBy=[],
                )
            ]
        )
        draft = _draft(_section("s1", "Approach", "Our approach is sound."))
        research = _research(ledger)

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        self.assertEqual(len(result.applied_additions), 1)
        self.assertEqual(result.applied_additions[0].requirement_id, "r-form-1")


class BlastRadiusGuardTests(unittest.TestCase):
    """Required test 3: a ledger that would add many sections in one pass
    declines and reports instead of silently applying."""

    def test_too_many_eligible_additions_are_declined_not_applied(self) -> None:
        # 7 missing required_content requirements, one more than
        # _BLAST_RADIUS_MAX_ADDITIONS (5) — must trip the absolute cap even
        # on a small draft where the growth-fraction check doesn't engage.
        requirement_count = _BLAST_RADIUS_MAX_ADDITIONS + 2
        ledger = RequirementLedger(
            requirements=[
                _req(f"r-{i}", f"Required deliverable {i}", satisfiedBy=[])
                for i in range(requirement_count)
            ]
        )
        draft = _draft(
            _section("s1", "Approach", "text"),
            _section("s2", "Overview", "text"),
        )
        research = _research(ledger)

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        self.assertEqual(
            result.applied_additions,
            [],
            "the whole pass's eligible additions must be declined, not partially applied",
        )
        self.assertEqual(len(result.draft.sections), 2, "no stub sections were added")
        self.assertEqual(result.declined_addition_count, requirement_count)
        self.assertEqual(len(result.declined_addition_titles), requirement_count)
        self.assertIsNotNone(result.declined_addition_reason)
        self.assertIn("blast-radius guard", result.declined_addition_reason)
        self.assertTrue(
            any("declined" in line for line in result.logs),
            f"expected a declined-additions log line, got: {result.logs}",
        )

    def test_growth_fraction_guard_trips_on_a_larger_draft_even_under_the_absolute_cap(
        self,
    ) -> None:
        # 3 additions is under the absolute cap (5) but is 30% growth on a
        # 10-section draft — over the 25% growth-fraction threshold, and the
        # draft is large enough (>= the absolute cap) for the fraction check
        # to engage.
        ledger = RequirementLedger(
            requirements=[
                _req(f"r-{i}", f"Required deliverable {i}", satisfiedBy=[])
                for i in range(3)
            ]
        )
        draft = _draft(*[_section(f"s{i}", f"Section {i}", "text") for i in range(10)])
        research = _research(ledger)

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        self.assertEqual(result.applied_additions, [])
        self.assertEqual(result.declined_addition_count, 3)

    def test_a_handful_of_additions_under_both_thresholds_still_applies_normally(
        self,
    ) -> None:
        # Guard rail: the guard must not become so strict it blocks the
        # ordinary case. 2 additions on a 10-section draft (20% growth,
        # under both the absolute cap and the fraction) must apply.
        ledger = RequirementLedger(
            requirements=[
                _req(f"r-{i}", f"Required deliverable {i}", satisfiedBy=[])
                for i in range(2)
            ]
        )
        draft = _draft(*[_section(f"s{i}", f"Section {i}", "text") for i in range(10)])
        research = _research(ledger)

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        self.assertEqual(len(result.applied_additions), 2)
        self.assertEqual(result.declined_addition_count, 0)
        self.assertIsNone(result.declined_addition_reason)

    def test_a_single_missing_requirement_on_a_near_empty_draft_is_not_declined(
        self,
    ) -> None:
        # Guard rail: a 1-section draft gaining its one missing required
        # section is 100% "growth" by naive fraction math but must not trip
        # the guard — the fraction check only engages once the draft already
        # has at least _BLAST_RADIUS_MAX_ADDITIONS sections (see the
        # constant's comment in proposal_rfp_compliance.py).
        ledger = RequirementLedger(
            requirements=[_req("r1", "A signed cover letter", satisfiedBy=[])]
        )
        draft = _draft(_section("s1", "Approach", "text"))
        research = _research(ledger)

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        self.assertEqual(len(result.applied_additions), 1)
        self.assertEqual(result.declined_addition_count, 0)


class AddThenCutOrderingTests(unittest.TestCase):
    """Required test 4: a draft at its page limit that gains a required
    section must end up back WITHIN the limit — ADD must run before CUT so
    the newly added section's words are included in the trim calculation,
    not appended after CUT already balanced the budget."""

    def test_draft_at_the_page_limit_stays_within_it_after_gaining_a_required_section(
        self,
    ) -> None:
        # 1 page * 350 words/page = 350-word budget. The existing section
        # sits exactly at that budget, as 350 one-word trimmable paragraphs
        # so CUT's whole-paragraph trimming can land exactly on the budget
        # regardless of the added stub's exact word count (trimming only
        # removes WHOLE trailing paragraphs — coarser paragraphs would let
        # rounding, not the ADD/CUT ordering itself, decide the outcome).
        at_limit_content = "\n\n".join(_para("filler", 1) for _ in range(350))  # 350w
        self.assertEqual(len(at_limit_content.split()), 350)

        ledger = RequirementLedger(
            requirements=[
                _req(
                    "r-cover-letter",
                    "A signed cover letter",
                    source="required_content",
                    satisfiedBy=[],
                )
            ]
        )
        draft = _draft(_section("s1", "Approach", at_limit_content))
        research = _research(ledger)
        rfp = _rfp(pageLimit=1)

        before_words = sum(len(s.content.split()) for s in draft.sections)
        self.assertEqual(before_words, 350, "the draft starts exactly at the budget")

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=rfp)

        # ADD applied — the new section exists.
        self.assertEqual(len(result.applied_additions), 1)
        added = next(
            s for s in result.draft.sections if s.id == "ledger-r-cover-letter"
        )
        self.assertIn("[MANUAL FILL", added.content)

        # CUT ran AFTER add, against the post-add total — proven by the
        # final draft (original section + new stub) landing back within
        # budget, not by the new section alone happening to fit.
        after_words = sum(len(s.content.split()) for s in result.draft.sections)
        self.assertLessEqual(
            after_words,
            350,
            "ADD must run before CUT so the added section is included in the "
            "trim calculation, not pushed over the limit after trimming",
        )
        self.assertTrue(result.applied_cuts, "the original section must have been trimmed")
        # The original section absorbed the cut, not the freshly-added stub
        # (both are unscored; the original is far larger so it sorts first).
        cut_ids = {c.section_id for c in result.applied_cuts}
        self.assertIn("s1", cut_ids)
        self.assertNotIn("ledger-r-cover-letter", cut_ids)


if __name__ == "__main__":
    unittest.main()
