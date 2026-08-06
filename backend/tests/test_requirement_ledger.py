"""The ledger is the spine: one requirement, exactly one section.

Observed: an RFP listing a cover letter first in its required content, and naming
Technical Approach as a scored criterion, produced a proposal with neither. The
parsed requirement matrix existed and was passed to the outline planner as
f"Compliance item count: {len(...)}".
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import config
from app.models.proposal import ProposalResearchCache, RfpSectionMap
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.services import proposal_repository as repo
from app.services.proposal_intelligence.assembler import (
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
    RetrievalEntry,
    RetrievalPlan,
    SectionPlan,
    SectionPlans,
)


def _req(rid: str, text: str, **kw) -> LedgerRequirement:
    kw.setdefault("source", "required_content")
    kw.setdefault("mandatory", True)
    return LedgerRequirement(id=rid, text=text, **kw)


class LedgerReadingsTests(unittest.TestCase):
    def test_a_requirement_with_no_section_is_missing(self) -> None:
        ledger = RequirementLedger(requirements=[_req("r1", "A cover letter", satisfied_by=[])])
        self.assertEqual([r.id for r in ledger.missing()], ["r1"])

    def test_a_requirement_with_one_section_is_satisfied(self) -> None:
        ledger = RequirementLedger(requirements=[_req("r1", "A cover letter", satisfied_by=["sec-1"])])
        self.assertEqual(ledger.missing(), [])
        self.assertEqual(ledger.duplicated(), [])

    def test_a_requirement_with_three_sections_is_duplicated(self) -> None:
        """Insurance appeared in 1.5, the attachments checklist and the contract ack."""
        ledger = RequirementLedger(requirements=[
            _req("r1", "Proof of insurance", satisfied_by=["sec-1-5", "sec-attach", "sec-contract"]),
        ])
        self.assertEqual([r.id for r in ledger.duplicated()], ["r1"])

    def test_optional_requirements_are_not_missing(self) -> None:
        ledger = RequirementLedger(requirements=[
            _req("r1", "Optional appendix", mandatory=False, satisfied_by=[]),
        ])
        self.assertEqual(ledger.missing(), [])

    def test_scored_requirements_are_reported_with_points(self) -> None:
        ledger = RequirementLedger(requirements=[
            _req("r1", "Technical Approach", source="scored_criterion", points=30.0, satisfied_by=[]),
            _req("r2", "A cover letter", satisfied_by=["sec-1"]),
        ])
        scored = ledger.scored()
        self.assertEqual([r.id for r in scored], ["r1"])
        self.assertEqual(scored[0].points, 30.0)

    def test_a_missing_scored_criterion_is_reported_as_missing(self) -> None:
        """The single most expensive defect: an unscoreable criterion."""
        ledger = RequirementLedger(requirements=[
            _req("r1", "Technical Approach", source="scored_criterion", points=30.0, satisfied_by=[]),
        ])
        self.assertEqual([r.id for r in ledger.missing()], ["r1"])

    def test_empty_ledger_is_clean(self) -> None:
        ledger = RequirementLedger(requirements=[])
        self.assertEqual(ledger.missing(), [])
        self.assertEqual(ledger.duplicated(), [])


def _sample_plan_with_requirements() -> ProposalExecutionPlan:
    """A realistic Phase 2 plan reproducing both observed defects: a cover letter
    named first in required content, and Technical Approach scored at 30 pts —
    neither mapped to an outline section."""
    plan = ProposalExecutionPlan(rfpId="rfp-e2e")
    plan.opportunity.understanding.client = "City of Test"
    plan.opportunity.understanding.confidence = 0.9
    plan.opportunity.strategy.confidence = 0.9
    plan.delivery.delivery_model.confidence = 0.85
    plan.delivery.methodology.confidence = 0.8
    plan.delivery.budget.confidence = 0.8
    plan.delivery.timeline.confidence = 0.8

    plan.opportunity.compliance = ComplianceMatrix(
        items=[
            ComplianceItem(id="comp-1", requirement="A cover letter", mandatory=True),
            ComplianceItem(
                id="comp-2",
                requirement="W-9 tax form",
                mandatory=True,
                targetSection="Attachments",
            ),
        ],
        confidence=0.9,
    )
    plan.opportunity.evaluation = EvaluationAnalysis(
        criteria=[EvaluationCriterion(name="Technical Approach", weight=30.0, priorityRank=1)],
        confidence=0.9,
    )

    plan.writing.proposal_outline = ProposalOutline(
        sections=[OutlineSection(id="rfp-sec-1", title="Attachments", order=1, required=True)],
        confidence=0.85,
    )
    plan.writing.section_plans = SectionPlans(
        plans=[
            SectionPlan(
                sectionId="rfp-sec-1",
                title="Attachments",
                purpose="Include required forms",
                keyMessages=["W-9 tax form"],
                evidenceNeeded=["Signed W-9"],
                retrievalGoal="attachments",
                writerInstructions="",
                successDefinition="All required forms attached",
            )
        ],
        confidence=0.85,
    )
    plan.writing.retrieval_plan = RetrievalPlan(
        entries=[
            RetrievalEntry(
                sectionId="rfp-sec-1",
                requiredAssets=["W-9"],
                queries=["w9 form"],
                expectedSources=["forms"],
                whyNeeded="Attachments",
            )
        ],
        confidence=0.85,
    )
    return plan


class RequirementLedgerEndToEndTests(unittest.TestCase):
    """Proves the ledger is actually populated, not just modeled.

    This is the check fact_ledger never got: build_and_attach_ledger has zero
    callers and research.fact_ledger is None forever. A ledger that exists as a
    model but is never wired into ProposalResearchCache is the same failure.
    """

    def test_assembler_populates_the_persisted_requirement_ledger(self) -> None:
        plan = _sample_plan_with_requirements()
        legacy = derive_legacy_fields(plan)

        ledger = legacy.get("requirementLedger")
        self.assertIsInstance(ledger, RequirementLedger)
        self.assertTrue(ledger.requirements, "ledger must not be empty")

        scored = ledger.scored()
        self.assertTrue(scored, "ledger must retain the scored criterion")
        self.assertEqual(scored[0].text, "Technical Approach")
        self.assertEqual(scored[0].points, 30.0)

        # Task 8: neither requirement was mapped to an outline section (the
        # real defect), and derive_legacy_fields now wires
        # amend_outline_for_missing_requirements at this exact point — so
        # instead of merely surfacing as missing for a human to notice later,
        # the genuinely missing required_content item ("A cover letter") gets
        # its own section appended to the outline BEFORE drafting, and is
        # satisfied by it once the ledger is rebuilt against the amended
        # outline.
        #
        # Post-incident correction (Task 15): "Technical Approach" is a
        # scored_criterion (an evaluation-scoring CATEGORY name, not a
        # deliverable) — see proposal_rfp_compliance.py's
        # _ADD_ELIGIBLE_SOURCES module note and
        # amend_outline_for_missing_requirements' docstring. A live
        # full-generation run duplicated 21 sections this way, so it now
        # stays advisory (visible via ledger.missing()) rather than being
        # auto-amended into the outline.
        missing_texts = {r.text for r in ledger.missing()}
        self.assertNotIn("A cover letter", missing_texts)
        self.assertIn("Technical Approach", missing_texts)

        cover_letter = next(r for r in ledger.requirements if r.text == "A cover letter")
        technical_approach = next(r for r in ledger.requirements if r.text == "Technical Approach")
        self.assertTrue(cover_letter.satisfied_by, "must be satisfied by the amended section")
        self.assertFalse(
            technical_approach.satisfied_by,
            "a scored_criterion is never auto-amended, so nothing satisfies it",
        )

        amended_titles = {s.title for s in legacy["rfpSections"]}
        self.assertIn("A cover letter", amended_titles)
        self.assertNotIn("Technical Approach", amended_titles)

        # The matched, form-carried requirement should NOT be reported missing.
        self.assertNotIn("W-9 tax form", missing_texts)

        research = ProposalResearchCache(
            rfpId="rfp-e2e",
            requirementLedger=ledger,
            updatedAt="2026-08-05T00:00:00Z",
        )
        self.assertIsNotNone(research.requirement_ledger)
        self.assertTrue(research.requirement_ledger.requirements)
        self.assertTrue(research.requirement_ledger.scored())


def _ledger_with(text: str) -> RequirementLedger:
    return RequirementLedger(
        requirements=[LedgerRequirement(id="r1", text=text, satisfiedBy=["sec-1"])]
    )


class LedgerSurvivesRoutineResavesTests(unittest.IsolatedAsyncioTestCase):
    """C1: a persisted ledger must survive a Sections 1-3 regeneration.

    _generate_sections_1_3_inner (proposal_generator.py:1743) and
    _persist_sections_1_3_partial (:970) rebuild ProposalResearchCache from a
    hand-written whitelist of prior fields. rfpSections is copied forward;
    requirementLedger was not, and merge_research_preserve_audit_fields only
    protected the three audit fields — so a routine "regenerate the company
    section" wiped the ledger and Task 2's coverage gate would silently no-op.

    This is a real sqlite round trip, not a mock: the defect lives in the
    save path, so a mocked store would not see it.
    """

    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db = Path(self._tmpdir.name) / "ledger.db"
        self._patchers = [
            patch.object(config.settings, "database_path", self._db),
            patch.object(repo, "_use_supabase", return_value=False),
            patch("app.services.rfp_repository._use_supabase", return_value=False),
            patch("app.services.supabase_db.use_supabase_db", return_value=False),
        ]
        for p in self._patchers:
            p.start()
        repo.init_proposal_db()

    async def asyncTearDown(self) -> None:
        for p in reversed(self._patchers):
            p.stop()
        self._tmpdir.cleanup()

    async def test_sections_1_3_regeneration_does_not_wipe_the_ledger(self) -> None:
        rfp_id = "rfp-c1"

        # Phase 2 save: ledger persisted alongside rfpSections.
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                rfpSections=[RfpSectionMap(id="sec-1", title="Attachments")],
                requirementLedger=_ledger_with("A cover letter"),
                updatedAt="2026-08-05T00:00:00Z",
            )
        )
        after_phase2 = await repo.aget_research_cache(rfp_id)
        self.assertIsNotNone(after_phase2.requirement_ledger)

        # Sections 1-3 regeneration: rebuilt from the prior-field whitelist,
        # which forwards rfpSections but leaves requirementLedger null.
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                rfpSections=after_phase2.rfp_sections,
                questions=after_phase2.questions,
                evidenceCorpus=after_phase2.evidence_corpus,
                retrievalRounds=after_phase2.retrieval_rounds,
                coverageThreshold=after_phase2.coverage_threshold,
                updatedAt="2026-08-05T01:00:00Z",
            )
        )

        after_regen = await repo.aget_research_cache(rfp_id)
        self.assertTrue(after_regen.rfp_sections, "rfp_sections must survive")
        self.assertIsNotNone(
            after_regen.requirement_ledger,
            "ledger was wiped by a routine sections-1-3 regeneration",
        )
        self.assertEqual(
            [r.text for r in after_regen.requirement_ledger.requirements],
            ["A cover letter"],
        )

    async def test_a_freshly_built_ledger_still_overwrites_the_stored_one(self) -> None:
        """Preservation must not freeze the ledger: a real Phase 2 re-run wins."""
        rfp_id = "rfp-c1-refresh"
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                requirementLedger=_ledger_with("stale requirement"),
                updatedAt="2026-08-05T00:00:00Z",
            )
        )
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                requirementLedger=_ledger_with("fresh requirement"),
                updatedAt="2026-08-05T02:00:00Z",
            )
        )
        reloaded = await repo.aget_research_cache(rfp_id)
        self.assertEqual(
            [r.text for r in reloaded.requirement_ledger.requirements],
            ["fresh requirement"],
        )


class ComplianceSourceClassificationTests(unittest.TestCase):
    """I2: 'form' was matched as a bare substring, so "in-form-ation" and
    "per-form-ance" — two of the most common words in a compliance matrix —
    were mislabelled as forms.

    These tests assert the SUBSTRING bug stays fixed: none of these four
    strings may ever classify as "form". They originally spelled that as
    ``== "required_content"`` because, at the time, the only two outcomes
    after the administrative check were "form" or "required_content", so
    "not a form" and "required_content" were the same assertion by
    elimination. That is no longer true: _classify_compliance_source is now
    fail-closed (task 19 — see assembler.py's module note), so a string that
    is neither a form NOR a positively-recognised narrative deliverable
    lands on "submission_instruction" instead. Each test below therefore
    asserts the real intent — not a form — and additionally pins the exact
    non-form source it does get, so the test stays meaningful rather than
    merely non-vacuous."""

    def _source_for(self, requirement: str) -> str:
        ledger = build_requirement_ledger(
            [ComplianceItem(id="c1", requirement=requirement)], [], []
        )
        return ledger.requirements[0].source

    def test_information_is_not_a_form(self) -> None:
        source = self._source_for("Provide information about your firm")
        self.assertNotEqual(source, "form", '"in-form-ation" is not a form')
        # A named narrative deliverable ("information ABOUT your firm" — a
        # section, unlike the bare field list "contact information").
        self.assertEqual(source, "required_content")

    def test_performance_is_not_a_form(self) -> None:
        source = self._source_for("Performance metrics and KPIs")
        self.assertNotEqual(source, "form", '"per-form-ance" is not a form')
        # Not a form, and not positively recognisable as a narrative
        # deliverable either — fail-closed puts it on the compliance
        # checklist, where a human reads it, rather than guessing it into a
        # client-facing section.
        self.assertEqual(source, "submission_instruction")

    def test_conforming_is_not_a_form(self) -> None:
        source = self._source_for("Describe conforming products")
        self.assertNotEqual(source, "form", '"con-form-ing" is not a form')
        # "Describe" is an authoring verb — a genuine narrative deliverable.
        self.assertEqual(source, "required_content")

    def test_platform_is_not_a_form(self) -> None:
        source = self._source_for("Platform uptime guarantee")
        self.assertNotEqual(source, "form", '"plat-form" is not a form')
        # Same fail-closed reasoning as test_performance_is_not_a_form.
        self.assertEqual(source, "submission_instruction")

    def test_a_real_form_is_still_classified_as_a_form(self) -> None:
        self.assertEqual(self._source_for("Submit the attached W-9 form"), "form")
        self.assertEqual(self._source_for("Return all required forms"), "form")


class AdministrativeSubmissionInstructionClassificationTests(unittest.TestCase):
    """Task 16: the third instance of the same defect — the ledger tried to
    ADD a section for administrative submission mechanics, not just scored
    criteria (Task 14) or over-broad matcher hits (I2/I3/I4). Verbatim from a
    real KVCC scan: 8 compliance-matrix items the reconciler wanted to add as
    sections were all submission instructions ("Proposal must be received no
    later than August 3, 2026 by 3:00 P.M. (ET)", "Include contractor's
    name(s)", ...), never deliverables. Nobody writes a proposal section
    titled with a deadline; "Include contact information (Address, phone,
    Fax, Email)" is a FIELD inside a section, not a section."""

    def _source_for(self, requirement: str) -> str:
        ledger = build_requirement_ledger(
            [ComplianceItem(id="c1", requirement=requirement)], [], []
        )
        return ledger.requirements[0].source

    # --- The five real KVCC items: all administrative, never addable. -----

    def test_a_hard_deadline_is_administrative(self) -> None:
        self.assertEqual(
            self._source_for(
                "Proposal must be received no later than August 3, 2026 by "
                "3:00 P.M. (ET)"
            ),
            "submission_instruction",
        )

    def test_a_labelling_and_delivery_instruction_is_administrative(self) -> None:
        self.assertEqual(
            self._source_for(
                "Proposal must be marked 'Marketing Plan' and submitted to "
                "specified address or email"
            ),
            "submission_instruction",
        )

    def test_a_bare_contractor_name_field_is_administrative(self) -> None:
        self.assertEqual(
            self._source_for("Include contractor's name(s)"), "submission_instruction"
        )

    def test_a_bare_contact_information_field_is_administrative(self) -> None:
        self.assertEqual(
            self._source_for(
                "Include contact information (Address, phone, Fax, Email)"
            ),
            "submission_instruction",
        )

    def test_a_proposal_validity_window_is_administrative(self) -> None:
        self.assertEqual(
            self._source_for(
                "Proposal must be valid for at least thirty (30) days after "
                "proposal due date"
            ),
            "submission_instruction",
        )

    # --- Obvious siblings: still administrative. ---------------------------

    def test_a_copy_count_instruction_is_administrative(self) -> None:
        self.assertEqual(
            self._source_for("Submit one original and three copies of the proposal"),
            "submission_instruction",
        )

    def test_a_font_and_margin_format_rule_is_administrative(self) -> None:
        self.assertEqual(
            self._source_for(
                "Proposals must use 11-point font and 1-inch margins throughout"
            ),
            "submission_instruction",
        )

    def test_a_page_count_instruction_is_administrative(self) -> None:
        self.assertEqual(
            self._source_for("The technical proposal shall not exceed 20 pages"),
            "submission_instruction",
        )

    def test_a_hand_delivery_instruction_is_administrative(self) -> None:
        self.assertEqual(
            self._source_for(
                "Proposals must be hand-delivered in a sealed envelope"
            ),
            "submission_instruction",
        )

    # --- The hard boundary: these must stay addable deliverables. ---------

    def test_a_project_schedule_deliverable_is_not_administrative(self) -> None:
        self.assertEqual(
            self._source_for("Provide a detailed project schedule with milestones"),
            "required_content",
        )

    def test_a_signed_w9_is_still_classified_as_a_form_not_administrative(
        self,
    ) -> None:
        self.assertEqual(
            self._source_for("Submit a completed and signed W-9 form"), "form"
        )

    def test_a_narrative_approach_description_is_not_administrative(self) -> None:
        self.assertEqual(
            self._source_for("Describe your approach to content migration"),
            "required_content",
        )

    def test_references_with_contact_information_is_a_deliverable_not_administrative(
        self,
    ) -> None:
        """The hard boundary named in the incident report: "include contact
        information" is administrative, but "provide references WITH contact
        information" is a real deliverable (three references) that merely
        carries contact details as part of a larger narrative ask — it must
        never be swallowed by the bare-field-list pattern just because it
        shares the words "contact information"."""
        self.assertEqual(
            self._source_for(
                "Provide three client references with contact information"
            ),
            "required_content",
        )

    def test_a_signed_cover_letter_is_a_deliverable_not_a_bare_field_list(
        self,
    ) -> None:
        self.assertEqual(
            self._source_for(
                "Include a cover letter signed by an authorized representative"
            ),
            "required_content",
        )


class MatcherPrecisionTests(unittest.TestCase):
    """I3/I4: the matcher produced silent false positives on short generic
    titles, and never consulted section.requirements at all."""

    def _satisfied_by(
        self, requirement: str, sections: list[RfpSectionMap], hint: str = ""
    ) -> list[str]:
        ledger = build_requirement_ledger(
            [ComplianceItem(id="c1", requirement=requirement, targetSection=hint)],
            [],
            sections,
        )
        return ledger.requirements[0].satisfied_by

    def test_a_section_titled_summary_does_not_satisfy_an_insurance_requirement(
        self,
    ) -> None:
        """"Summary" is a substring of the requirement text — a false positive
        that makes the requirement invisible to missing()."""
        sections = [RfpSectionMap(id="s3", title="Summary")]
        self.assertEqual(
            self._satisfied_by("Provide a summary of your insurance coverage", sections),
            [],
        )

    def test_a_section_titled_cost_does_not_satisfy_a_cost_proposal_requirement(
        self,
    ) -> None:
        sections = [RfpSectionMap(id="s4", title="Cost")]
        self.assertEqual(
            self._satisfied_by("Provide a detailed cost proposal", sections), []
        )

    def test_a_matching_requirements_bullet_satisfies_the_requirement(self) -> None:
        """I4: the section carries the requirement verbatim in its bullets and
        must count as covering it, regardless of its title."""
        sections = [
            RfpSectionMap(
                id="s5",
                title="Technical Approach",
                requirements=["Proof of general liability insurance"],
            )
        ]
        self.assertEqual(
            self._satisfied_by("Proof of general liability insurance", sections), ["s5"]
        )

    def test_an_exact_title_match_still_satisfies_the_requirement(self) -> None:
        sections = [RfpSectionMap(id="s6", title="Cover Letter")]
        self.assertEqual(self._satisfied_by("Cover Letter", sections), ["s6"])

    def test_a_target_section_hint_still_matches_its_section(self) -> None:
        sections = [RfpSectionMap(id="s7", title="Attachments")]
        self.assertEqual(
            self._satisfied_by("W-9 tax form", sections, hint="Attachments"), ["s7"]
        )


class LedgerIdentityTests(unittest.TestCase):
    """Minor: ids must be unique, and scored ids must survive reordering."""

    def test_two_compliance_items_sharing_an_id_get_distinct_ledger_ids(self) -> None:
        ledger = build_requirement_ledger(
            [
                ComplianceItem(id="c1", requirement="A cover letter"),
                ComplianceItem(id="c1", requirement="Proof of insurance"),
            ],
            [],
            [],
        )
        ids = [r.id for r in ledger.requirements]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2, f"duplicate ledger ids: {ids}")

    def test_scored_ids_survive_reordering_of_the_criteria(self) -> None:
        first = build_requirement_ledger(
            [],
            [
                EvaluationCriterion(name="Technical Approach", weight=30.0),
                EvaluationCriterion(name="Cost", weight=20.0),
            ],
            [],
        )
        second = build_requirement_ledger(
            [],
            [
                EvaluationCriterion(name="Cost", weight=20.0),
                EvaluationCriterion(name="Technical Approach", weight=30.0),
            ],
            [],
        )
        by_text_first = {r.text: r.id for r in first.requirements}
        by_text_second = {r.text: r.id for r in second.requirements}
        self.assertEqual(by_text_first, by_text_second)


if __name__ == "__main__":
    unittest.main()
