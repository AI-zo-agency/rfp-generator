"""Whole-manuscript adversarial auditor regression tests."""

from __future__ import annotations

import unittest
from unittest import mock

from app.core.config import settings
from app.models.proposal import (
    AdversarialAuditFinding,
    PreSubmitReview,
    ProofPoint,
    ProposalAdversarialAudit,
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    RfpSectionMap,
)
from app.services.llm import LlmError
from app.services.llm_routing import is_quality_critical_node
from app.services.proposal_manuscript_auditor import (
    persist_manuscript_audit,
    run_manuscript_auditor,
)
from app.services.proposal_pipeline_status import collect_manuscript_blockers
from tests.fixtures.manuscripts.loader import load_fixture


def _ready_research(
    draft: ProposalDraft, research: ProposalResearchCache | None
) -> ProposalResearchCache:
    mapped = [
        RfpSectionMap(id=s.id, title=s.title, requirements=["x"]) for s in draft.sections
    ]
    review = PreSubmitReview(
        rfpId=draft.rfp_id,
        scannedAt="2026-01-01T00:00:00Z",
        summary="ok",
        readyToSubmit=True,
    )
    budget = research.budget if research and research.budget else None
    return ProposalResearchCache(
        rfpId=draft.rfp_id,
        updatedAt="2026-01-01T00:00:00Z",
        rfpSections=mapped,
        proofPoints=[
            ProofPoint(
                requirement="x",
                caseStudy="Example",
                kbSource="KB",
                narrativeHook="Delivered measurable outcomes",
            )
        ],
        proposalExecutionPlan={"validation": {"readinessStatus": "ready"}},
        presubmitReview=review,
        budget=budget,
    )


class ManuscriptAuditorTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_bad_fixture_returns_findings_without_mutation(self) -> None:
        draft, research, rfp, _expected = load_fixture(
            "cvvb_v2_truncation_orphan_commission"
        )
        ready_research = _ready_research(draft, research)
        before = [section.content for section in draft.sections]

        audit = await run_manuscript_auditor(
            draft=draft,
            research=ready_research,
            rfp=rfp,
            use_llm=False,
        )

        self.assertTrue(audit.findings, "expected deterministic findings")
        self.assertEqual(before, [section.content for section in draft.sections])
        codes = {finding.code for finding in audit.findings}
        self.assertTrue(
            any(code.startswith("t1.") for code in codes),
            msg=f"expected T1-derived finding, got {sorted(codes)}",
        )
        self.assertTrue(
            any(
                finding.category in {"budget", "truncation", "internal_note", "consistency"}
                for finding in audit.findings
            ),
            msg=[(f.category, f.code, f.message) for f in audit.findings],
        )

    async def test_known_good_fixture_has_no_deterministic_criticals(self) -> None:
        draft, research, rfp, _expected = load_fixture("known_good_clean")
        ready_research = _ready_research(draft, research)

        audit = await run_manuscript_auditor(
            draft=draft,
            research=ready_research,
            rfp=rfp,
            use_llm=False,
        )

        criticals = [finding for finding in audit.findings if finding.severity == "critical"]
        self.assertEqual(
            criticals,
            [],
            msg=[(f.category, f.code, f.message) for f in criticals],
        )

    async def test_llm_findings_are_mockable_and_use_quality_critical_node(self) -> None:
        draft, research, rfp, _expected = load_fixture("known_good_clean")
        ready_research = _ready_research(draft, research)

        with mock.patch(
            "app.services.proposal_manuscript_auditor.llm.chat_json",
            new=mock.AsyncMock(
                return_value=(
                    {
                        "findings": [
                            {
                                "severity": "critical",
                                "category": "fabrication",
                                "code": "llm.fabrication.residual_claim",
                                "message": "Residual unverified award claim detected.",
                                "sectionId": draft.sections[0].id,
                            }
                        ]
                    },
                    "openrouter",
                )
            ),
        ) as chat_json:
            audit = await run_manuscript_auditor(
                draft=draft,
                research=ready_research,
                rfp=rfp,
                use_llm=True,
            )

        chat_json.assert_awaited_once()
        _, kwargs = chat_json.await_args
        self.assertEqual(kwargs["node_name"], "manuscript_auditor")
        self.assertTrue(is_quality_critical_node("manuscript_auditor"))
        self.assertTrue(any(f.code == "llm.fabrication.residual_claim" for f in audit.findings))
        self.assertEqual(audit.provider, "openrouter")

    async def test_llm_failure_falls_back_to_deterministic_findings(self) -> None:
        draft, research, rfp, _expected = load_fixture(
            "cvvb_v2_truncation_orphan_commission"
        )
        ready_research = _ready_research(draft, research)

        with mock.patch(
            "app.services.proposal_manuscript_auditor.llm.chat_json",
            new=mock.AsyncMock(side_effect=LlmError("synthetic failure")),
        ):
            audit = await run_manuscript_auditor(
                draft=draft,
                research=ready_research,
                rfp=rfp,
                use_llm=True,
            )

        self.assertEqual(audit.provider, "deterministic")
        self.assertTrue(audit.findings)
        self.assertTrue(all(finding.source == "deterministic" for finding in audit.findings))

    async def test_persist_audit_attaches_to_research(self) -> None:
        draft, research, rfp, _expected = load_fixture("known_good_clean")
        ready_research = _ready_research(draft, research)
        audit = ProposalAdversarialAudit(
            rfpId=rfp.id,
            scannedAt="2026-01-01T00:00:00Z",
            findings=[
                AdversarialAuditFinding(
                    severity="warning",
                    category="duplication",
                    code="t6.overlap.warning",
                    message="Repeated opener language appears across sections.",
                    sectionId=draft.sections[0].id,
                )
            ],
            provider="deterministic",
        )

        updated = persist_manuscript_audit(ready_research, audit)

        self.assertIsNotNone(updated.adversarial_audit)
        self.assertEqual(audit.findings[0].code, updated.adversarial_audit.findings[0].code)


class AdversarialAuditBlockerTests(unittest.TestCase):
    def test_settings_default_is_off(self) -> None:
        self.assertFalse(settings.adversarial_audit_block)

    def test_critical_audit_findings_block_only_when_flag_enabled(self) -> None:
        draft, research, rfp, _expected = load_fixture("known_good_clean")
        ready_research = _ready_research(draft, research)
        ready_research = ready_research.model_copy(
            update={
                "adversarial_audit": ProposalAdversarialAudit(
                    rfpId=rfp.id,
                    scannedAt="2026-01-01T00:00:00Z",
                    findings=[
                        AdversarialAuditFinding(
                            severity="critical",
                            category="fabrication",
                            code="llm.fabrication.fake_case_study",
                            message="Case study appears unsupported by the manuscript evidence.",
                            sectionId=draft.sections[0].id,
                            sectionTitle=draft.sections[0].title,
                        )
                    ],
                    provider="openrouter",
                )
            }
        )

        with mock.patch("app.services.proposal_pipeline_status.settings") as mocked_settings:
            mocked_settings.t1_gates_block = False
            mocked_settings.consistency_criticals_block = False
            mocked_settings.money_slots_block = False
            mocked_settings.overlap_gates_block = False
            mocked_settings.adversarial_audit_block = False
            blockers_off = collect_manuscript_blockers(
                draft=draft,
                research=ready_research,
                rfp=rfp,
            )
        self.assertFalse(any("Adversarial audit critical" in b for b in blockers_off))

        with mock.patch("app.services.proposal_pipeline_status.settings") as mocked_settings:
            mocked_settings.t1_gates_block = False
            mocked_settings.consistency_criticals_block = False
            mocked_settings.money_slots_block = False
            mocked_settings.overlap_gates_block = False
            mocked_settings.adversarial_audit_block = True
            blockers_on = collect_manuscript_blockers(
                draft=draft,
                research=ready_research,
                rfp=rfp,
            )
        self.assertTrue(
            any("Adversarial audit critical" in b for b in blockers_on),
            msg=blockers_on,
        )

    def test_stale_deterministic_audit_does_not_block_after_draft_fix(self) -> None:
        """Readiness re-scans live draft; persisted deterministic criticals go stale."""
        draft, research, rfp, _expected = load_fixture("known_good_clean")
        ready_research = _ready_research(draft, research)
        stale = ready_research.model_copy(
            update={
                "adversarial_audit": ProposalAdversarialAudit(
                    rfpId=rfp.id,
                    scannedAt="2026-01-01T00:00:00Z",
                    findings=[
                        AdversarialAuditFinding(
                            severity="critical",
                            category="consistency",
                            code="deterministic.integrity.staffing_hours",
                            message="Stale staffing hours finding that no longer applies.",
                            sectionId=draft.sections[0].id,
                            sectionTitle=draft.sections[0].title,
                            source="deterministic",
                        ),
                        AdversarialAuditFinding(
                            severity="critical",
                            category="fabrication",
                            code="llm.fabrication.fake_case_study",
                            message="LLM residual still relevant.",
                            sectionId=draft.sections[0].id,
                            sectionTitle=draft.sections[0].title,
                            source="llm",
                        ),
                    ],
                    provider="deterministic",
                )
            }
        )

        with mock.patch("app.services.proposal_pipeline_status.settings") as mocked_settings:
            mocked_settings.t1_gates_block = False
            mocked_settings.consistency_criticals_block = False
            mocked_settings.money_slots_block = False
            mocked_settings.overlap_gates_block = False
            mocked_settings.adversarial_audit_block = True
            with mock.patch(
                "app.services.proposal_manuscript_auditor._build_deterministic_findings",
                return_value=[],
            ):
                blockers = collect_manuscript_blockers(
                    draft=draft,
                    research=stale,
                    rfp=rfp,
                )

        self.assertFalse(
            any("deterministic.integrity.staffing_hours" in b for b in blockers),
            msg=blockers,
        )
        self.assertTrue(
            any("llm.fabrication.fake_case_study" in b for b in blockers),
            msg=blockers,
        )


if __name__ == "__main__":
    unittest.main()
