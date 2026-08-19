"""Regression tests for the bounded adversarial repair loop."""

from __future__ import annotations

import unittest
from unittest import mock

from app.models.proposal import (
    AdversarialAuditFinding,
    AdversarialRepairReport,
    ProposalAdversarialAudit,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
    RfpSectionMap,
)
from app.services.proposal_adversarial_repair import (
    _append_manual_fill,
    _dedupe_actionable_findings,
    _escalate_remaining_findings,
    run_adversarial_repair_loop,
)
from app.services.proposal_manual_flags import extract_manual_fill_tags
from tests.fixtures.manuscripts.loader import load_fixture
from tests.test_manuscript_auditor import _ready_research


def _sibling_defect_draft() -> tuple[ProposalDraft, ProposalResearchCache, object]:
    draft, research, rfp, _expected = load_fixture("cvvb_v2_truncation_orphan_commission")
    ready = _ready_research(draft, research)
    sections = [
        ProposalSection(
            id="s-hours",
            title="Staffing Plan and Hours",
            content=(
                "Our Year 1 staffing allocation anticipates intensive launch support "
                "followed by steady-state delivery. Proposed hours by role: Account "
                "Director 400 hours, Creative Director 320 hours, Senior Strategist "
                "280 hours, Designer 200 hours, and Project Manager 160 hours. "
                "These figures are planning estimates invented for this synthetic "
                "fixture and are not grounded in a rate card."
            ),
            status="generated",
            source="generated",
            mode="write",
        ),
        ProposalSection(
            id="s-commission",
            title="Pass-Through and Commission",
            content=(
                "Commission on media placements is $16,875.00 (15% of planned buys). "
                "The commission line is presented without a corresponding $112,500 "
                "media base line item in the budget table."
            ),
            status="generated",
            source="generated",
            mode="write",
        ),
    ]
    draft = draft.model_copy(update={"sections": sections})
    ready = ready.model_copy(
        update={
            "rfp_sections": [
                RfpSectionMap(id=section.id, title=section.title, requirements=["x"])
                for section in sections
            ]
        }
    )
    return draft, ready, rfp


def _manuscript_manual_fills(draft: ProposalDraft) -> list[str]:
    tags: list[str] = []
    for section in draft.sections:
        tags.extend(tag.text for tag in extract_manual_fill_tags(section.content or ""))
    return tags


class AdversarialRepairLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_sibling_defect_remaining_blocks_resolution(self) -> None:
        draft, research, rfp = _sibling_defect_draft()

        updated_draft, updated_research, audit, report = await run_adversarial_repair_loop(
            rfp=rfp,
            draft=draft,
            research=research,
            use_llm_audit=False,
            use_llm_repair=False,
        )

        staffing = next(section for section in updated_draft.sections if section.id == "s-hours")
        self.assertIn("[VERIFY: staffing hours", staffing.content or "")
        self.assertTrue(_manuscript_manual_fills(updated_draft))
        self.assertFalse(report.resolved)
        self.assertNotEqual(report.stopped_reason, "resolved")
        self.assertTrue(any(finding.severity == "critical" for finding in audit.findings))
        self.assertIsNotNone(updated_research.adversarial_repair_report)

    async def test_no_progress_escalates_remaining_to_manual_fill(self) -> None:
        draft, research, rfp = _sibling_defect_draft()
        # Strip inventable hours so only unfixable commission criticals remain.
        sections = [
            ProposalSection(
                id="s-commission",
                title="Pass-Through and Commission",
                content=(
                    "Commission on media placements is $16,875.00 (15% of planned buys). "
                    "The commission line is presented without a corresponding $112,500 "
                    "media base line item in the budget table."
                ),
                status="generated",
                source="generated",
                mode="write",
            )
        ]
        draft = draft.model_copy(update={"sections": sections})
        research = research.model_copy(
            update={
                "rfp_sections": [
                    RfpSectionMap(id="s-commission", title="Pass-Through and Commission", requirements=["x"])
                ]
            }
        )

        updated_draft, _research, _audit, report = await run_adversarial_repair_loop(
            rfp=rfp,
            draft=draft,
            research=research,
            use_llm_audit=False,
            use_llm_repair=False,
            max_attempts_per_finding=1,
        )

        self.assertFalse(report.resolved)
        self.assertIn(
            report.stopped_reason,
            {"no_progress", "attempts_exhausted", "manual_fill_required", "max_rounds"},
        )
        fills = _manuscript_manual_fills(updated_draft)
        self.assertTrue(fills, msg="expected MANUAL FILL after non-convergence")
        self.assertTrue(report.escalations or fills)

    async def test_sectionless_finding_writes_manual_fill_somewhere(self) -> None:
        draft, research, rfp = _sibling_defect_draft()
        draft = draft.model_copy(
            update={
                "sections": [
                    ProposalSection(
                        id="s-narrative",
                        title="Approach",
                        content="We will deliver a phased communications plan.",
                        status="generated",
                        source="generated",
                        mode="write",
                    )
                ]
            }
        )
        updated, tag = _append_manual_fill(
            draft,
            section_id=None,
            issue="orphan commission $16,875.00 with no client media pass-through",
        )
        self.assertIsNotNone(tag)
        self.assertTrue(_manuscript_manual_fills(updated))
        self.assertIn("[MANUAL FILL:", "\n".join(s.content or "" for s in updated.sections))

    def test_manuscript_lock_handoff_skips_pricing_tab(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-lock",
            sections=[
                ProposalSection(
                    id="letter",
                    title="1. Letter of Interest",
                    content="We are pleased to submit this proposal.",
                    status="generated",
                ),
                ProposalSection(
                    id="pricing",
                    title="14. Request for Qualifications Pricing Form",
                    content="## Pricing\n\n**Total: $150,000**",
                    status="generated",
                ),
            ],
            updatedAt="2026-08-05T00:00:00+00:00",
        )
        updated, tag = _append_manual_fill(
            draft,
            section_id="pricing",
            issue=(
                "Primary contact lock is Ron Comer, but this section names "
                "Sonja Anderson as primary/liaison."
            ),
            finding_code=(
                "deterministic.manuscript_locks.primary_contact_lock_is_ron_comer_"
                "but_this_section_names_sonja"
            ),
        )
        self.assertIsNotNone(tag)
        pricing = next(s for s in updated.sections if s.id == "pricing")
        letter = next(s for s in updated.sections if s.id == "letter")
        self.assertNotIn("MANUAL FILL", pricing.content or "")
        self.assertIn("MANUAL FILL", letter.content or "")

    def test_append_manual_fill_dedupes_code_family_suffix(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-dedupe",
            sections=[
                ProposalSection(
                    id="letter",
                    title="1.1 — Who We Are",
                    content="We submit this proposal.",
                    status="generated",
                ),
            ],
            updatedAt="2026-08-05T00:00:00+00:00",
        )
        updated, first = _append_manual_fill(
            draft,
            section_id="letter",
            issue="Physical signed attachment required (bid form).",
            finding_code=(
                "deterministic.compliance.disqualification_risk_rfp_requires_"
                "physical_signed_attachment_bi"
            ),
        )
        self.assertIsNotNone(first)
        updated, second = _append_manual_fill(
            updated,
            section_id="letter",
            issue="Physical signed attachment required (affidavit).",
            finding_code=(
                "deterministic.compliance.disqualification_risk_rfp_requires_"
                "physical_signed_attachment_au"
            ),
        )
        self.assertIsNone(second)
        letter = updated.sections[0].content or ""
        self.assertEqual(letter.upper().count("[MANUAL FILL"), 1)

    def test_dedupe_collapses_integrity_and_audit_siblings(self) -> None:
        findings = [
            AdversarialAuditFinding(
                severity="critical",
                category="consistency",
                code="deterministic.integrity.staffing_hours",
                message="Unverified staffing hours must be converted to [VERIFY: staffing hours].",
                sectionId="s-hours",
            ),
            AdversarialAuditFinding(
                severity="critical",
                category="consistency",
                code="llm.fabrication.staffing_hours",
                message="Staffing hours appear invented.",
                sectionId="s-hours",
            ),
            AdversarialAuditFinding(
                severity="critical",
                category="consistency",
                code="t5.free_currency",
                message="Dollar amount $112,500 does not match canonical budget",
                sectionId="s-commission",
            ),
        ]
        deduped = _dedupe_actionable_findings(findings)
        self.assertEqual(len(deduped), 2)
        families = {(f.section_id, f.code) for f in deduped}
        self.assertIn(("s-hours", "deterministic.integrity.staffing_hours"), families)

    async def test_phase4_attach_respects_use_llm_false_after_repair(self) -> None:
        from app.core.config import settings
        from app.services import proposal_generator as generator

        draft, research, rfp = _sibling_defect_draft()
        review_stub = research.presubmit_review
        assert review_stub is not None

        with (
            mock.patch.object(settings, "adversarial_repair_loop", True),
            mock.patch(
                "app.services.proposal_generator.get_rfp",
                return_value=rfp,
            ),
            mock.patch(
                "app.services.proposal_generator.aget_proposal_draft",
                new=mock.AsyncMock(return_value=draft),
            ),
            mock.patch(
                "app.services.proposal_generator.aget_research_cache",
                new=mock.AsyncMock(return_value=research),
            ),
            mock.patch(
                "app.services.proposal_generator.asave_proposal_draft",
                new=mock.AsyncMock(),
            ),
            mock.patch(
                "app.services.proposal_generator.asave_research_cache",
                new=mock.AsyncMock(),
            ),
            mock.patch(
                "app.services.proposal_generator.run_presubmit_review",
                return_value=review_stub,
            ),
            mock.patch(
                "app.services.proposal_generator.run_presubmit_autofix_loop",
                new=mock.AsyncMock(
                    return_value=(draft, review_stub, [], "ready", 0, research, 0)
                ),
            ),
            mock.patch(
                "app.services.proposal_generator.run_adversarial_repair_loop",
                new=mock.AsyncMock(
                    return_value=(
                        draft,
                        research.model_copy(
                            update={
                                "adversarial_audit": ProposalAdversarialAudit(
                                    rfpId=rfp.id,
                                    scannedAt="2026-01-01T00:00:00Z",
                                    findings=[],
                                    provider="deterministic",
                                ),
                                "adversarial_repair_report": AdversarialRepairReport(
                                    roundsRun=1,
                                    stoppedReason="resolved",
                                    resolved=True,
                                ),
                            }
                        ),
                        ProposalAdversarialAudit(
                            rfpId=rfp.id,
                            scannedAt="2026-01-01T00:00:00Z",
                            findings=[],
                            provider="deterministic",
                        ),
                        AdversarialRepairReport(
                            roundsRun=1,
                            stoppedReason="resolved",
                            resolved=True,
                        ),
                    )
                ),
            ) as repair,
            mock.patch(
                "app.services.proposal_generator._attach_phase4_manuscript_audit",
                new=mock.AsyncMock(return_value=research),
            ) as attach,
            mock.patch(
                "app.services.proposal_generator.run_presubmit_review_with_manual_flags",
                return_value=review_stub,
            ),
        ):
            await generator.run_phase4_presubmit_autofix(rfp.id, use_llm=False)

        repair.assert_awaited_once()
        self.assertFalse(repair.await_args.kwargs["use_llm_audit"])
        attach.assert_awaited_once()
        self.assertFalse(attach.await_args.kwargs["use_llm"])

    async def test_phase4_presubmit_review_runs_adversarial_repair_when_enabled(self) -> None:
        from app.core.config import settings
        from app.services import proposal_generator as generator

        draft, research, rfp = _sibling_defect_draft()
        review_stub = research.presubmit_review
        assert review_stub is not None
        repaired_research = research.model_copy(
            update={
                "adversarial_audit": ProposalAdversarialAudit(
                    rfpId=rfp.id,
                    scannedAt="2026-01-01T00:00:00Z",
                    findings=[],
                    provider="deterministic",
                ),
                "adversarial_repair_report": AdversarialRepairReport(
                    roundsRun=1,
                    stoppedReason="resolved",
                    resolved=True,
                ),
            }
        )

        with (
            mock.patch.object(settings, "adversarial_repair_loop", True),
            mock.patch(
                "app.services.proposal_generator.get_rfp",
                return_value=rfp,
            ),
            mock.patch(
                "app.services.proposal_generator.aget_proposal_draft",
                new=mock.AsyncMock(return_value=draft),
            ),
            mock.patch(
                "app.services.proposal_generator.aget_research_cache",
                new=mock.AsyncMock(return_value=research),
            ),
            mock.patch(
                "app.services.proposal_generator.asave_proposal_draft",
                new=mock.AsyncMock(),
            ),
            mock.patch(
                "app.services.proposal_generator.asave_research_cache",
                new=mock.AsyncMock(),
            ) as save_research,
            mock.patch(
                "app.services.proposal_generator.run_adversarial_repair_loop",
                new=mock.AsyncMock(
                    return_value=(
                        draft,
                        repaired_research,
                        repaired_research.adversarial_audit,
                        repaired_research.adversarial_repair_report,
                    )
                ),
            ) as repair,
            mock.patch(
                "app.services.proposal_generator._attach_phase4_manuscript_audit",
                new=mock.AsyncMock(return_value=repaired_research),
            ),
            mock.patch(
                "app.services.proposal_generator.run_presubmit_review_with_manual_flags",
                return_value=review_stub,
            ),
        ):
            _review, updated = await generator.run_phase4_presubmit_review(rfp.id)

        repair.assert_awaited_once()
        self.assertIsNotNone(updated.adversarial_repair_report)
        self.assertTrue(save_research.await_count >= 1)

    def test_sibling_manual_fill_not_suppressed_by_existing_tag(self) -> None:
        """A MANUAL FILL for issue A must not skip escalation of distinct issue B."""
        draft, _research, _rfp = _sibling_defect_draft()
        hours = next(section for section in draft.sections if section.id == "s-hours")
        draft = draft.model_copy(
            update={
                "sections": [
                    hours.model_copy(
                        update={
                            "content": (
                                (hours.content or "")
                                + "\n[MANUAL FILL: confirm staffing hours before submission]"
                            )
                        }
                    ),
                    *[section for section in draft.sections if section.id != "s-hours"],
                ]
            }
        )
        findings = [
            AdversarialAuditFinding(
                severity="critical",
                category="consistency",
                code="t5.orphan_commission",
                message="Orphan commission $16,875.00 with no client media pass-through.",
                sectionId="s-hours",
                sectionTitle="Staffing Plan and Hours",
            )
        ]
        updated, escalations = _escalate_remaining_findings(draft, findings, [])
        fills = _manuscript_manual_fills(updated)
        self.assertTrue(escalations, msg="expected sibling MANUAL FILL escalation")
        self.assertGreaterEqual(len(fills), 2)
        self.assertTrue(
            any("orphan commission" in tag.casefold() or "16,875" in tag for tag in fills),
            msg=fills,
        )

    async def test_generate_full_proposal_runs_repair_when_flag_enabled(self) -> None:
        from contextlib import ExitStack

        from app.core.config import settings
        from app.models.proposal import ProposalBrandVoice
        from app.services import proposal_generator as generator

        draft, research, rfp = _sibling_defect_draft()
        brand = ProposalBrandVoice()
        edit_report = mock.Mock(section_logs=[])
        review_stub = research.presubmit_review
        assert review_stub is not None
        ending_stub = mock.Mock(requirements_covered=0, requirements_total=0)
        repair = mock.AsyncMock(
            return_value=(
                draft,
                research,
                ProposalAdversarialAudit(
                    rfpId=rfp.id,
                    scannedAt="2026-01-01T00:00:00Z",
                    findings=[],
                    provider="deterministic",
                ),
                AdversarialRepairReport(
                    roundsRun=1,
                    stoppedReason="resolved",
                    resolved=True,
                ),
            )
        )

        patches = [
            mock.patch.object(settings, "adversarial_repair_loop", True),
            mock.patch.object(settings, "budget_before_drafting", False),
            mock.patch(
                "app.services.proposal_generator.llm.is_configured",
                return_value=True,
            ),
            mock.patch(
                "app.services.proposal_generator.generate_sections_1_3",
                new=mock.AsyncMock(return_value=(draft, brand, research)),
            ),
            mock.patch(
                "app.services.proposal_generator.run_phase2_retrieval",
                new=mock.AsyncMock(return_value=research),
            ),
            mock.patch(
                "app.services.proposal_generator.run_phase3_drafting",
                new=mock.AsyncMock(return_value=(draft, research)),
            ),
            mock.patch(
                "app.services.proposal_generator.run_phase3_5_budget",
                new=mock.AsyncMock(return_value=(draft, research, research.budget)),
            ),
            mock.patch(
                "app.services.proposal_generator.get_rfp",
                return_value=rfp,
            ),
            mock.patch(
                "app.services.proposal_generator.run_phase3_6_self_edit",
                new=mock.AsyncMock(return_value=(draft, research, edit_report)),
            ),
            mock.patch(
                "app.services.proposal_generator.self_edit_exhausted_issues",
                return_value=[],
            ),
            mock.patch(
                "app.services.proposal_generator.run_adversarial_repair_loop",
                new=repair,
            ),
            mock.patch(
                "app.services.proposal_generator.run_presubmit_review",
                return_value=review_stub,
            ),
            mock.patch(
                "app.services.proposal_generator._attach_phase4_manuscript_audit",
                new=mock.AsyncMock(return_value=research),
            ),
            mock.patch(
                "app.services.proposal_generator.asave_proposal_draft",
                new=mock.AsyncMock(),
            ),
            mock.patch(
                "app.services.proposal_generator.asave_research_cache",
                new=mock.AsyncMock(),
            ),
            mock.patch(
                "app.services.proposal_ending_report.build_proposal_ending_report",
                return_value=ending_stub,
            ),
            mock.patch(
                "app.services.proposal_ending_report.ending_report_as_dict",
                return_value={},
            ),
            mock.patch("app.services.proposal_generator.assert_manuscript_ready"),
            mock.patch(
                "app.services.proposal_generator._assert_proposal_not_reset",
                new=mock.AsyncMock(),
            ),
            mock.patch(
                "app.services.llm_call_log.get_run_cost_breakdown",
                side_effect=RuntimeError("skip"),
            ),
            mock.patch(
                "app.services.proposal_fulfill_rfp_gaps.ensure_closing_sections",
                new=mock.AsyncMock(return_value=(draft, [], [], None)),
            ),
            mock.patch(
                "app.services.proposal_fulfill_rfp_gaps._merge_closing_into_research_map",
                side_effect=lambda research, *_a, **_k: research,
            ),
            mock.patch(
                "app.services.proposal_rfp_submission_requirements.ensure_all_rfp_submission_requirements",
                new=mock.AsyncMock(return_value=(draft, [], [], None)),
            ),
            mock.patch(
                "app.services.proposal_rfp_submission_requirements.merge_deliverables_into_research",
                side_effect=lambda research, *_a, **_k: research,
            ),
            mock.patch(
                "app.services.rfp_content.load_local_rfp_text",
                return_value=("desc", "x" * 250, True, [], 1, False),
            ),
            mock.patch(
                "app.services.rfp_content.combine_rfp_text",
                return_value="x" * 250,
            ),
        ]
        with ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            await generator.generate_full_proposal(rfp.id)

        repair.assert_awaited_once()
        self.assertEqual(repair.await_args.kwargs["rfp"], rfp)


class RepairSectionForFindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_repair_section_for_finding_includes_failure_reason(self) -> None:
        from app.models.proposal import RepairPlan
        from app.services.proposal_adversarial_repair import repair_section_for_finding

        finding = AdversarialAuditFinding(
            severity="critical",
            category="fabrication",
            code="llm.fabrication.staffing_hours",
            message="Staffing hours appear invented.",
            sectionId="s1",
            sectionTitle="Staffing Plan",
        )
        plan = RepairPlan(
            findingCode=finding.code,
            findingCategory=finding.category,
            sectionId="s1",
            attemptNumber=2,
            previousOutcome="no_change",
            failureReason="evidence_missing",
            repairMode="targeted_retrieval_then_rewrite",
            requiresTargetedRetrieval=True,
        )
        fake_section = ProposalSection(
            id="s1",
            title="T",
            content="fixed",
            status="generated",
            source="generated",
            mode="write",
        )
        fake_draft = ProposalDraft(
            rfpId="r1",
            sections=[fake_section],
            updatedAt="t",
            generatedAt="t",
        )

        with mock.patch(
            "app.services.proposal_adversarial_repair.improve_proposal_section",
            new_callable=mock.AsyncMock,
            return_value=(fake_section, fake_draft, None, "test", "ok", True),
        ) as improve:
            section, draft, research, provider = await repair_section_for_finding(
                rfp_id="r1",
                section_id="s1",
                finding=finding,
                repair_plan=plan,
                failure_reason="evidence_missing",
                prior_attempt_summary="no_change",
                use_strong_model=False,
            )

        self.assertEqual(section.id, "s1")
        self.assertEqual(draft.rfp_id, "r1")
        self.assertIsNone(research)
        self.assertEqual(provider, "test")
        improve.assert_awaited_once()
        msg = improve.await_args.args[2]
        self.assertIn("Staffing hours appear invented.", msg)
        self.assertIn("Previous attempt failed because: evidence_missing", msg)
        self.assertIn("Previous outcome: no_change", msg)
        self.assertIn("Repair mode: targeted_retrieval_then_rewrite", msg)

    async def test_repair_section_for_finding_rejects_missing_section_id(self) -> None:
        from app.models.proposal import RepairPlan
        from app.services.proposal_adversarial_repair import repair_section_for_finding

        finding = AdversarialAuditFinding(
            severity="critical",
            category="fabrication",
            code="llm.fabrication.staffing_hours",
            message="Staffing hours appear invented.",
        )
        plan = RepairPlan(
            findingCode=finding.code,
            findingCategory=finding.category,
            sectionId=None,
            attemptNumber=1,
            repairMode="targeted_retrieval_then_rewrite",
        )

        with mock.patch(
            "app.services.proposal_adversarial_repair.improve_proposal_section",
            new_callable=mock.AsyncMock,
        ) as improve:
            with self.assertRaises(ValueError):
                await repair_section_for_finding(
                    rfp_id="r1",
                    section_id="",
                    finding=finding,
                    repair_plan=plan,
                    failure_reason=None,
                    prior_attempt_summary="",
                )
        improve.assert_not_called()


class IntelligentRepairLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_intelligent_repair_calls_section_repair_with_plan(self) -> None:
        """use_llm_repair=True routes a non-protected empty methodology finding to
        repair_section_for_finding with a failure-aware RepairPlan, and the loop
        converges + records the attempt in the report."""
        draft, research, rfp = _sibling_defect_draft()
        section_id = "s-approach"
        empty_section = ProposalSection(
            id=section_id,
            title="Technical Approach & Methodology",
            content="",
            status="generated",
            source="generated",
            mode="write",
        )
        draft = draft.model_copy(update={"sections": [empty_section]})
        research = research.model_copy(
            update={
                "rfp_sections": [
                    RfpSectionMap(id=section_id, title=empty_section.title, requirements=["x"])
                ]
            }
        )

        finding = AdversarialAuditFinding(
            severity="critical",
            category="other",
            code="deterministic.coverage.empty_technical_approach",
            message="Required methodology section is empty.",
            sectionId=section_id,
            sectionTitle=empty_section.title,
            source="deterministic",
        )
        improved_section = empty_section.model_copy(
            update={"content": "We will execute a four-phase methodology with weekly checkpoints."}
        )

        async def fake_audit(*, draft, research, rfp, use_llm):
            target = next((s for s in draft.sections if s.id == section_id), None)
            findings = [] if (target and (target.content or "").strip()) else [finding]
            return ProposalAdversarialAudit(
                rfpId=rfp.id,
                scannedAt="2026-01-01T00:00:00Z",
                findings=findings,
                provider="deterministic",
            )

        async def fake_repair_section(
            *,
            rfp_id,
            section_id: str,
            finding,
            repair_plan,
            failure_reason,
            prior_attempt_summary,
            use_strong_model,
        ):
            updated_draft = draft.model_copy(update={"sections": [improved_section]})
            return improved_section, updated_draft, research, "test-provider"

        with (
            mock.patch(
                "app.services.proposal_adversarial_repair.run_manuscript_auditor",
                side_effect=fake_audit,
            ),
            mock.patch(
                "app.services.proposal_adversarial_repair.asave_proposal_draft",
                new=mock.AsyncMock(),
            ),
            mock.patch(
                "app.services.proposal_adversarial_repair.asave_research_cache",
                new=mock.AsyncMock(),
            ),
            mock.patch(
                "app.services.proposal_adversarial_repair.repair_section_for_finding",
                side_effect=fake_repair_section,
            ) as repair_mock,
        ):
            updated_draft, _updated_research, _audit, report = await run_adversarial_repair_loop(
                rfp=rfp,
                draft=draft,
                research=research,
                use_llm_audit=False,
                use_llm_repair=True,
            )

        repair_mock.assert_called_once()
        _args, kwargs = repair_mock.call_args
        self.assertEqual(kwargs["section_id"], section_id)
        self.assertEqual(kwargs["finding"].code, finding.code)
        plan = kwargs["repair_plan"]
        self.assertIn(
            plan.repair_mode,
            {"plan_driven_rewrite", "targeted_retrieval_then_rewrite", "strong_model_rewrite"},
        )
        self.assertEqual(plan.attempt_number, 1)

        approach = next(s for s in updated_draft.sections if s.id == section_id)
        self.assertTrue((approach.content or "").strip())
        self.assertTrue(report.resolved)

        matching_attempts = [a for a in report.attempts if a.finding_code == finding.code]
        self.assertTrue(matching_attempts, msg="expected attempt history for the finding")
        self.assertEqual(matching_attempts[0].outcome, "resolved")
        self.assertEqual(matching_attempts[0].strategy, plan.repair_mode)

    async def test_budget_finding_escalates_without_freeform_rewrite(self) -> None:
        """budget_canonical_repair findings must never trigger repair_section_for_finding —
        deterministic cleanup or MANUAL FILL handoff only."""
        draft, research, rfp = _sibling_defect_draft()
        section_id = "s-commission"
        finding = AdversarialAuditFinding(
            severity="critical",
            category="budget_money",
            code="deterministic.budget.mismatch",
            message="Commission total contradicts canonical budget.",
            sectionId=section_id,
            sectionTitle="Pass-Through and Commission",
            source="deterministic",
        )

        async def fake_audit(*, draft, research, rfp, use_llm):
            return ProposalAdversarialAudit(
                rfpId=rfp.id,
                scannedAt="2026-01-01T00:00:00Z",
                findings=[finding],
                provider="deterministic",
            )

        with (
            mock.patch(
                "app.services.proposal_adversarial_repair.run_manuscript_auditor",
                side_effect=fake_audit,
            ),
            mock.patch(
                "app.services.proposal_adversarial_repair.repair_section_for_finding"
            ) as repair_mock,
        ):
            updated_draft, _updated_research, _audit, report = await run_adversarial_repair_loop(
                rfp=rfp,
                draft=draft,
                research=research,
                use_llm_audit=False,
                use_llm_repair=True,
                max_rounds=1,
            )

        repair_mock.assert_not_called()
        budget_attempts = [a for a in report.attempts if a.strategy == "budget_canonical_repair"]
        self.assertTrue(budget_attempts, msg="expected a budget_canonical_repair attempt entry")
        self.assertTrue(any(a.outcome == "manual_fill_escalated" for a in budget_attempts))
        self.assertTrue(_manuscript_manual_fills(updated_draft))
        self.assertFalse(report.resolved)


if __name__ == "__main__":
    unittest.main()
