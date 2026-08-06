"""Task 5 (+ Task 9 ADD wiring): Scan-RFP becomes a reconciler that fixes an
EXISTING draft.

Root defect (verified at HEAD 81e649b, proposal_rfp_compliance.py:197-204,
``scan_uncovered_requirement_gaps``)::

    for mapped in research.rfp_sections:
        uncovered = mapped.uncovered_requirements or []
        if not uncovered:
            continue
        section = _section_for_mapped_title(draft, mapped.title or "")
        if not section:
            continue          # <-- structurally cannot ADD anything

It iterates SECTIONS and silently drops any requirement with no matching
section — the exact same shape as the Phase 2 bug this whole plan exists to
fix (every verifier iterating sections instead of requirements).

``reconcile_requirement_ledger`` (app/services/proposal_rfp_compliance.py)
replaces that section-driven walk with a requirement-driven one, reading the
persisted ``RequirementLedger`` three ways:

    len(satisfied_by) == 0  -> ADD: applied. Task 5 shipped this surfaced-
                                only (matcher measured 6/10 on wording
                                variants — task-2-report.md); Task 9 wires it
                                to actually add a [MANUAL FILL] stub section
                                now that the matcher is measured 8/10 with
                                zero false positives (two unsafe aliases
                                removed — proposal_section_aliases.py).
    len(satisfied_by) == 1  -> correct, left alone
    len(satisfied_by) >  1  -> MERGE: applied. Cross-referenced instead of
                                restated, via resolve_duplicate_owners
                                (app/services/proposal_intelligence/assembler.py).
                                The MERGE owner is protected from CUT in the
                                same pass (Task 9): it just became the sole
                                bearer of that requirement's detail.
    over page budget        -> CUT: applied. Lowest-scoring content trimmed
                                first; content carrying evaluation points, or
                                designated a MERGE owner this pass, is never
                                cut below a usable floor / at all.

Pure and deterministic — makes zero LLM calls (net LLM delta for this pass
is zero; nothing here calls out to redraft_section_agent or chat_json). The
added stub section is a deterministic [MANUAL FILL] tag, not an LLM call.
"""

from __future__ import annotations

import inspect
import unittest

from app.models.proposal import (
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
    RfpSectionMap,
)
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.models.rfp import RfpRecord
from app.services.proposal_rfp_compliance import (
    AppliedCutAction,
    AppliedMergeAction,
    AppliedRequirementAddition,
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


def _research(
    ledger: RequirementLedger | None,
    rfp_sections: list[RfpSectionMap] | None = None,
) -> ProposalResearchCache:
    return ProposalResearchCache(
        rfpId="rfp-1",
        requirementLedger=ledger,
        rfpSections=rfp_sections or [],
        updatedAt="2026-08-06T00:00:00Z",
    )


def _para(word: str, count: int) -> str:
    """One paragraph of `count` repeated words — deterministic word counting."""
    return " ".join([word] * count)


class MissingRequirementIsAddedTests(unittest.TestCase):
    """0 satisfied_by -> ADD: a new [MANUAL FILL] stub section is applied."""

    def test_missing_mandatory_requirement_is_reported_as_an_applied_addition(self) -> None:
        ledger = RequirementLedger(
            requirements=[_req("r1", "A signed cover letter", satisfiedBy=[])]
        )
        draft = _draft(_section("s1", "Approach", "Our approach is..."))
        research = _research(ledger)
        rfp = _rfp()

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=rfp)

        self.assertEqual(len(result.applied_additions), 1)
        addition = result.applied_additions[0]
        self.assertIsInstance(addition, AppliedRequirementAddition)
        self.assertEqual(addition.requirement_id, "r1")
        self.assertEqual(addition.requirement_text, "A signed cover letter")
        self.assertEqual(addition.section_id, "ledger-r1")

    def test_draft_gains_a_new_manual_fill_stub_section_for_the_missing_requirement(
        self,
    ) -> None:
        ledger = RequirementLedger(
            requirements=[_req("r1", "A signed cover letter", satisfiedBy=[])]
        )
        draft = _draft(_section("s1", "Approach", "Our approach is..."))
        research = _research(ledger)
        rfp = _rfp()

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=rfp)

        self.assertTrue(result.changed)
        self.assertEqual(result.applied_merges, [])
        self.assertEqual(result.applied_cuts, [])
        self.assertEqual(len(result.draft.sections), 2)
        # The original section is completely untouched.
        original = next(s for s in result.draft.sections if s.id == "s1")
        self.assertEqual(original.content, "Our approach is...")
        # The new section names the requirement verbatim and never invents
        # an answer for it.
        added = next(s for s in result.draft.sections if s.id == "ledger-r1")
        self.assertIn("[MANUAL FILL", added.content)
        self.assertIn("A signed cover letter", added.content)
        self.assertTrue(added.required)

    def test_missing_but_non_mandatory_requirement_is_not_surfaced(self) -> None:
        ledger = RequirementLedger(
            requirements=[
                _req("r1", "Optional appendix", mandatory=False, satisfiedBy=[])
            ]
        )
        draft = _draft(_section("s1", "Approach", "text"))
        research = _research(ledger)
        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())
        self.assertEqual(result.applied_additions, [])
        self.assertFalse(result.changed)
        self.assertEqual(len(result.draft.sections), 1)

    def test_scored_missing_requirements_are_added_before_unscored_ones(self) -> None:
        ledger = RequirementLedger(
            requirements=[
                _req("r-unscored", "Appendix B", satisfiedBy=[]),
                _req(
                    "r-scored",
                    "Key Personnel Matrix",
                    source="scored_criterion",
                    points=15.0,
                    satisfiedBy=[],
                ),
            ]
        )
        draft = _draft(_section("s1", "Approach", "text"))
        research = _research(ledger)
        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        self.assertEqual(
            [a.requirement_id for a in result.applied_additions],
            ["r-scored", "r-unscored"],
        )


class DuplicatedRequirementIsMergedTests(unittest.TestCase):
    """>1 satisfied_by -> merged to a single owner; the rest cross-reference."""

    def _triplicated_draft_and_research(self):
        rfp_sections = [
            RfpSectionMap(id="sec-a", title="Section 1.5", evaluationWeight=10),
            RfpSectionMap(id="sec-b", title="Attachments Checklist"),
            RfpSectionMap(id="sec-c", title="Contract Acknowledgment"),
        ]
        ledger = RequirementLedger(
            requirements=[
                _req(
                    "r1",
                    "Proof of insurance",
                    satisfiedBy=["sec-a", "sec-b", "sec-c"],
                )
            ]
        )
        sec_a_content = (
            "Our commercial general liability insurance provides $2M coverage "
            "per occurrence, carried by Acme Surety.\n\n"
            "Additional narrative about our qualifications."
        )
        sec_b_content = (
            "We carry $2M general liability insurance as required by "
            "Section 1.5.\n\nOther attachments listed here."
        )
        sec_c_content = (
            "We acknowledge and carry $2M insurance coverage per the "
            "contract terms.\n\nSignature block here."
        )
        draft = _draft(
            _section("sec-a", "Section 1.5", sec_a_content),
            _section("sec-b", "Attachments Checklist", sec_b_content),
            _section("sec-c", "Contract Acknowledgment", sec_c_content),
        )
        research = _research(ledger, rfp_sections)
        return draft, research, sec_a_content, sec_b_content, sec_c_content

    def test_merged_to_the_single_highest_weighted_owner(self) -> None:
        draft, research, sec_a_content, _, _ = self._triplicated_draft_and_research()
        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        self.assertTrue(result.changed)
        self.assertEqual(len(result.applied_merges), 1)
        merge = result.applied_merges[0]
        self.assertIsInstance(merge, AppliedMergeAction)
        self.assertEqual(merge.owner_section_id, "sec-a")
        self.assertEqual(sorted(merge.cross_reference_section_ids), ["sec-b", "sec-c"])

        owner_after = next(s for s in result.draft.sections if s.id == "sec-a")
        self.assertEqual(owner_after.content, sec_a_content, "owner section must not change")

    def test_non_owner_sections_carry_a_cross_reference_and_do_not_restate(self) -> None:
        draft, research, _, sec_b_content, sec_c_content = self._triplicated_draft_and_research()
        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        sec_b_after = next(s for s in result.draft.sections if s.id == "sec-b")
        sec_c_after = next(s for s in result.draft.sections if s.id == "sec-c")

        # Original prose is preserved (the merge never silently deletes content)...
        self.assertIn(sec_b_content, sec_b_after.content)
        self.assertIn(sec_c_content, sec_c_after.content)
        # ...but a cross-reference marker naming the owner is appended...
        self.assertIn("[LEDGER-XREF:r1]", sec_b_after.content)
        self.assertIn("[LEDGER-XREF:r1]", sec_c_after.content)
        self.assertIn("Section 1.5", sec_b_after.content)
        self.assertIn("Section 1.5", sec_c_after.content)
        # ...and the reconciler's OWN addition never restates the specifics
        # (coverage amounts, carrier names) — only the two originals (which
        # predate this pass) mention them.
        added_b = sec_b_after.content[len(sec_b_content) :]
        added_c = sec_c_after.content[len(sec_c_content) :]
        for added in (added_b, added_c):
            self.assertNotIn("$2M", added)
            self.assertNotIn("Acme Surety", added)


class OverBudgetCutsLowestScoringContentTests(unittest.TestCase):
    """Over page budget -> lowest-scoring content is cut first; scored content
    is never cut below a usable floor."""

    def test_unscored_content_absorbs_the_overage_before_scored_content_is_touched(
        self,
    ) -> None:
        # unscored: 2 paragraphs x 50 words = 100 words, floor 50 -> max cut 50
        unscored_content = _para("filler", 50) + "\n\n" + _para("filler", 50)
        # scored: 6 paragraphs x 50 words = 300 words, points=30, floor 150
        scored_content = "\n\n".join(_para("technical", 50) for _ in range(6))

        ledger = RequirementLedger(
            requirements=[
                _req(
                    "r1",
                    "Technical Approach",
                    source="scored_criterion",
                    points=30.0,
                    satisfiedBy=["sec-scored"],
                )
            ]
        )
        draft = _draft(
            _section("sec-unscored", "Company Overview", unscored_content),
            _section("sec-scored", "Technical Approach", scored_content),
        )
        research = _research(ledger)
        rfp = _rfp(pageLimit=1)  # 1 page * 350 words/page = 350-word budget

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=rfp)

        self.assertTrue(result.changed)
        self.assertEqual(len(result.applied_cuts), 1)
        cut = result.applied_cuts[0]
        self.assertIsInstance(cut, AppliedCutAction)
        self.assertEqual(cut.section_id, "sec-unscored")
        self.assertFalse(cut.had_evaluation_points)

        scored_after = next(s for s in result.draft.sections if s.id == "sec-scored")
        self.assertEqual(
            scored_after.content, scored_content, "scored section must not be touched"
        )
        unscored_after = next(s for s in result.draft.sections if s.id == "sec-unscored")
        self.assertEqual(len(unscored_after.content.split()), 50, "cut down to its floor")

        total_words_after = sum(
            len(s.content.split()) for s in result.draft.sections
        )
        self.assertLessEqual(total_words_after, 350)

    def test_scored_content_is_trimmed_when_necessary_but_never_below_its_floor(
        self,
    ) -> None:
        unscored_content = _para("filler", 50) + "\n\n" + _para("filler", 50)  # 100w
        scored_content = "\n\n".join(_para("technical", 50) for _ in range(20))  # 1000w

        ledger = RequirementLedger(
            requirements=[
                _req(
                    "r1",
                    "Technical Approach",
                    source="scored_criterion",
                    points=30.0,
                    satisfiedBy=["sec-scored"],
                )
            ]
        )
        draft = _draft(
            _section("sec-unscored", "Company Overview", unscored_content),
            _section("sec-scored", "Technical Approach", scored_content),
        )
        research = _research(ledger)
        rfp = _rfp(pageLimit=1)  # 350-word budget; total is 1100 -> overage 750

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=rfp)

        self.assertTrue(result.changed)
        section_ids_cut = {c.section_id for c in result.applied_cuts}
        self.assertIn("sec-scored", section_ids_cut)

        scored_after = next(s for s in result.draft.sections if s.id == "sec-scored")
        words_after = len(scored_after.content.split())
        self.assertLess(words_after, 1000, "real trimming must have happened")
        self.assertGreaterEqual(words_after, 150, "never below the scored floor")

    def test_no_page_limit_means_no_cut(self) -> None:
        big_content = "\n\n".join(_para("word", 50) for _ in range(20))
        draft = _draft(_section("s1", "Approach", big_content))
        ledger = RequirementLedger(requirements=[])
        research = _research(ledger)
        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())
        self.assertFalse(result.changed)
        self.assertEqual(result.applied_cuts, [])


class IdempotenceTests(unittest.TestCase):
    """Running the reconciler twice must change nothing the second time."""

    def test_running_twice_is_a_no_op_the_second_time(self) -> None:
        rfp_sections = [
            RfpSectionMap(id="sec-a", title="Section 1.5", evaluationWeight=10),
            RfpSectionMap(id="sec-b", title="Attachments Checklist"),
        ]
        ledger = RequirementLedger(
            requirements=[
                _req("r-missing", "A signed cover letter", satisfiedBy=[]),
                _req(
                    "r-dup",
                    "Proof of insurance",
                    satisfiedBy=["sec-a", "sec-b"],
                ),
                _req(
                    "r-scored",
                    "Technical Approach",
                    source="scored_criterion",
                    points=30.0,
                    satisfiedBy=["sec-scored"],
                ),
            ]
        )
        sec_a_content = "We carry $2M general liability insurance.\n\nMore text."
        sec_b_content = "Insurance coverage of $2M is maintained.\n\nOther text."
        unscored_content = _para("filler", 50) + "\n\n" + _para("filler", 50)
        scored_content = "\n\n".join(_para("technical", 50) for _ in range(6))

        draft = _draft(
            _section("sec-a", "Section 1.5", sec_a_content),
            _section("sec-b", "Attachments Checklist", sec_b_content),
            _section("sec-unscored", "Company Overview", unscored_content),
            _section("sec-scored", "Technical Approach", scored_content),
        )
        research = _research(ledger, rfp_sections)
        rfp = _rfp(pageLimit=1)

        first = reconcile_requirement_ledger(draft=draft, research=research, rfp=rfp)
        self.assertTrue(first.changed)
        self.assertEqual(
            [a.requirement_id for a in first.applied_additions], ["r-missing"]
        )

        second = reconcile_requirement_ledger(
            draft=first.draft, research=research, rfp=rfp
        )
        self.assertFalse(second.changed, "second run must be a no-op")
        self.assertEqual(second.applied_merges, [])
        self.assertEqual(second.applied_cuts, [])
        # The second run must not add a second stub section for the same
        # requirement — the added section's id already exists on the draft.
        self.assertEqual(second.applied_additions, [])
        self.assertEqual(
            len([s for s in second.draft.sections if s.id == "ledger-r-missing"]),
            1,
            "must not duplicate the added section",
        )
        self.assertEqual(
            [s.content for s in second.draft.sections],
            [s.content for s in first.draft.sections],
        )


class CompliantDraftIsUntouchedTests(unittest.TestCase):
    def test_fully_satisfied_under_budget_draft_has_no_changes(self) -> None:
        ledger = RequirementLedger(
            requirements=[
                _req("r1", "A signed cover letter", satisfiedBy=["s1"]),
            ]
        )
        draft = _draft(_section("s1", "Cover Letter", "Dear Sir or Madam..."))
        research = _research(ledger)
        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        self.assertFalse(result.changed)
        self.assertEqual(result.applied_additions, [])
        self.assertEqual(result.applied_merges, [])
        self.assertEqual(result.applied_cuts, [])
        self.assertEqual(result.draft.sections[0].content, "Dear Sir or Madam...")


class GracefulDegradationTests(unittest.TestCase):
    def test_no_research_at_all(self) -> None:
        draft = _draft(_section("s1", "Approach", "text"))
        result = reconcile_requirement_ledger(draft=draft, research=None, rfp=_rfp())
        self.assertFalse(result.changed)
        self.assertEqual(result.applied_additions, [])

    def test_research_with_no_ledger(self) -> None:
        draft = _draft(_section("s1", "Approach", "text"))
        research = _research(None)
        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())
        self.assertFalse(result.changed)

    def test_research_with_empty_ledger(self) -> None:
        draft = _draft(_section("s1", "Approach", "text"))
        research = _research(RequirementLedger(requirements=[]))
        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())
        self.assertFalse(result.changed)
        self.assertEqual(result.applied_cuts, [])
        self.assertEqual(result.applied_merges, [])

    def test_no_page_limit_anywhere(self) -> None:
        big_content = "\n\n".join(_para("word", 60) for _ in range(30))
        ledger = RequirementLedger(
            requirements=[_req("r1", "Technical Approach", points=30.0, satisfiedBy=["s1"])]
        )
        draft = _draft(_section("s1", "Technical Approach", big_content))
        research = _research(ledger)
        rfp = _rfp(pageLimit=None)
        # Must not raise, even though the manuscript is huge and there is no
        # limit to check it against.
        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=rfp)
        self.assertFalse(result.changed)


class CombinedEndToEndProofTests(unittest.TestCase):
    """The lesson from all prior tasks: green tests + a green suite is
    not proof the defect stopped reproducing. Build ONE draft with a missing
    requirement, a triplicated one, AND an over-budget manuscript together,
    run the real reconciler, and print what it added, merged and cut.
    """

    def _combined_draft_and_research(self):
        rfp_sections = [
            RfpSectionMap(id="sec-a", title="Section 1.5", evaluationWeight=10),
            RfpSectionMap(id="sec-b", title="Attachments Checklist"),
            RfpSectionMap(id="sec-c", title="Contract Acknowledgment"),
        ]
        ledger = RequirementLedger(
            requirements=[
                _req("r-missing", "A signed cover letter", satisfiedBy=[]),
                _req(
                    "r-dup",
                    "Proof of insurance",
                    satisfiedBy=["sec-a", "sec-b", "sec-c"],
                ),
                _req(
                    "r-scored",
                    "Technical Approach",
                    source="scored_criterion",
                    points=30.0,
                    satisfiedBy=["sec-scored"],
                ),
            ]
        )
        sec_a_content = "We carry $2M general liability insurance.\n\nMore narrative."
        sec_b_content = "Insurance coverage of $2M is maintained per 1.5.\n\nOther attachments."
        sec_c_content = "We acknowledge $2M insurance per contract terms.\n\nSignature block."
        unscored_content = _para("filler", 50) + "\n\n" + _para("filler", 50)  # 100w
        scored_content = "\n\n".join(_para("technical", 50) for _ in range(6))  # 300w

        draft = _draft(
            _section("sec-a", "Section 1.5", sec_a_content),
            _section("sec-b", "Attachments Checklist", sec_b_content),
            _section("sec-c", "Contract Acknowledgment", sec_c_content),
            _section("sec-unscored", "Company Overview", unscored_content),
            _section("sec-scored", "Technical Approach", scored_content),
        )
        research = _research(ledger, rfp_sections)
        rfp = _rfp(pageLimit=1)  # 350-word budget
        return draft, research, rfp

    def test_missing_plus_triplicated_plus_over_budget_together(self) -> None:
        draft, research, rfp = self._combined_draft_and_research()
        before_total_words = sum(len(s.content.split()) for s in draft.sections)

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=rfp)

        after_total_words = sum(len(s.content.split()) for s in result.draft.sections)

        print("\n=== Task 5/9 reconciler — combined end-to-end proof ===")
        print(f"Draft before: {before_total_words} words across {len(draft.sections)} sections")
        print(f"Draft after:  {after_total_words} words across {len(result.draft.sections)} sections")
        print(f"changed={result.changed}")
        print("APPLIED — additions:")
        for a in result.applied_additions:
            print(f"  - [{a.requirement_id}] {a.requirement_text!r} -> {a.section_id!r}")
        print("APPLIED — merges:")
        for m in result.applied_merges:
            print(
                f"  - [{m.requirement_id}] owner={m.owner_section_id!r} "
                f"cross_refs={m.cross_reference_section_ids}"
            )
        print("APPLIED — cuts:")
        for c in result.applied_cuts:
            print(
                f"  - {c.section_id!r} removed={c.words_removed}w "
                f"had_points={c.had_evaluation_points}"
            )
        for line in result.logs:
            print(f"  log: {line}")

        # ADD: applied — the cover letter requirement gets a new, honest
        # [MANUAL FILL] stub section rather than being silently dropped.
        self.assertEqual([a.requirement_id for a in result.applied_additions], ["r-missing"])
        added = next(s for s in result.draft.sections if s.id == "ledger-r-missing")
        self.assertIn("[MANUAL FILL", added.content)
        self.assertIn("A signed cover letter", added.content)

        # MERGE: applied — insurance resolved to a single owner.
        self.assertEqual(len(result.applied_merges), 1)
        merge = result.applied_merges[0]
        self.assertEqual(merge.requirement_id, "r-dup")
        self.assertEqual(merge.owner_section_id, "sec-a")
        self.assertEqual(set(merge.cross_reference_section_ids), {"sec-b", "sec-c"})
        owner_after = next(s for s in result.draft.sections if s.id == "sec-a")
        self.assertEqual(owner_after.content, "We carry $2M general liability insurance.\n\nMore narrative.")

        # CUT: applied — manuscript is smaller after reconcile than the raw
        # before+added total, nothing carrying evaluation points went below
        # its floor, and the MERGE owner (sec-a, zero points) was never cut
        # even though it's the cheapest-scoring section in the draft — the
        # C2 fix: it just became the sole bearer of the insurance detail.
        self.assertTrue(result.applied_cuts, "over-budget draft must produce at least one cut")
        self.assertNotIn(
            "sec-a", {c.section_id for c in result.applied_cuts},
            "the MERGE owner must never be cut in the same pass",
        )
        scored_after = next(s for s in result.draft.sections if s.id == "sec-scored")
        self.assertGreaterEqual(len(scored_after.content.split()), 150)

        # The defect this task fixes: the OLD scan_uncovered_requirement_gaps
        # would have silently dropped "A signed cover letter" (no matching
        # section) with `if not section: continue` — it is not in this
        # result at all under the old code path. The reconciler instead adds
        # a real section for it.
        self.assertTrue(
            any(a.requirement_text == "A signed cover letter" for a in result.applied_additions)
        )

    def test_running_the_combined_scenario_twice_is_idempotent(self) -> None:
        draft, research, rfp = self._combined_draft_and_research()
        first = reconcile_requirement_ledger(draft=draft, research=research, rfp=rfp)
        second = reconcile_requirement_ledger(draft=first.draft, research=research, rfp=rfp)

        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertEqual(second.applied_additions, [])
        self.assertEqual(second.applied_merges, [])
        self.assertEqual(second.applied_cuts, [])
        self.assertEqual(len(second.draft.sections), len(first.draft.sections))
        self.assertEqual(
            [s.content for s in second.draft.sections],
            [s.content for s in first.draft.sections],
        )


class ExistingGuardsStillWiredElsewhereTests(unittest.TestCase):
    """This reconciler is deliberately deterministic (see module docstring) —
    it does not itself call claim_validator / the fabrication guard /
    truncation repair. Those already run inside
    app.services.proposal_fulfill_rfp_gaps._run_fulfill_rfp_gaps_body's
    mode="full" path. This test only proves this task did not regress that
    existing wiring while touching neighboring gap-remediation code.
    """

    def test_fabrication_guard_still_wired_into_fulfill_rfp_gaps(self) -> None:
        import app.services.proposal_fulfill_rfp_gaps as mod

        src = inspect.getsource(mod)
        self.assertIn("repair_fabricated_qualifications_async", src)

    def test_truncation_repair_still_wired_into_fulfill_rfp_gaps(self) -> None:
        import app.services.proposal_fulfill_rfp_gaps as mod

        src = inspect.getsource(mod)
        self.assertIn("repair_truncated_manuscript_sections", src)

    def test_claim_validator_still_reachable_from_the_fabrication_guard(self) -> None:
        import app.services.proposal_fulfill_fabrication_guard as mod

        src = inspect.getsource(mod)
        self.assertIn("validate_and_flag_section", src)


if __name__ == "__main__":
    unittest.main()
