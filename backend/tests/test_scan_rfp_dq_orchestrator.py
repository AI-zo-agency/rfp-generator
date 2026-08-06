"""Scan RFP DQ / gov-policy gate + coverage orchestrator."""

from __future__ import annotations

import asyncio
import inspect
import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_rfp_compliance import LedgerReconcileResult
from app.services.proposal_scan_dq_orchestrator import (
    collect_go_no_go_dq_risks,
    collect_rfp_text_dq_risks,
    run_scan_coverage_orchestrator,
    run_scan_dq_gate_pass,
)


def _rfp(**kw) -> RfpRecord:
    base = dict(
        id="rfp-dq",
        title="T",
        client="C",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-05",
        lastActivityNote="n",
    )
    base.update(kw)
    return RfpRecord(**base)


def _draft(*sections: ProposalSection) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-dq",
        sections=list(sections),
        updatedAt="2026-08-05T00:00:00+00:00",
    )


class GoNoGoDqRiskTests(unittest.TestCase):
    def test_no_go_and_critical_gaps_surface(self) -> None:
        rfp = _rfp(
            goNoGo="no_go",
            goNoGoAnalysis={
                "recommendation": "no_go",
                "criticalGaps": ["Missing required WBE certification."],
                "compliance": {
                    "flags": [
                        {
                            "severity": "critical",
                            "category": "certification",
                            "message": "WBENC required but not evidenced",
                        }
                    ]
                },
                "deadline": {
                    "isPast": True,
                    "lateSubmissionDisqualifies": True,
                    "dueDate": "2026-08-01",
                },
            },
        )
        risks = collect_go_no_go_dq_risks(rfp)
        joined = " ".join(risks).casefold()
        self.assertIn("no-go", joined)
        self.assertIn("wbe", joined)
        self.assertIn("deadline passed", joined)


class RfpTextDqRiskTests(unittest.TestCase):
    def test_altered_form_and_eligibility_advisory(self) -> None:
        from app.services.proposal_rfp_compliance import AdvisorySubmissionInstruction

        draft = _draft(
            ProposalSection(
                id="s1",
                title="Approach",
                content="We will deliver.",
                status="generated",
            )
        )
        ledger = LedgerReconcileResult(
            draft=draft,
            changed=False,
            applied_additions=[],
            applied_merges=[],
            applied_cuts=[],
            logs=[],
            advisory_submission_instructions=[
                AdvisorySubmissionInstruction(
                    requirement_id="e1",
                    requirement_text="Must be a certified WBE set-aside contractor",
                )
            ],
        )
        with patch(
            "app.services.proposal_rfp_excerpt.rfp_forbids_quotation_form_changes",
            return_value=True,
        ):
            risks = collect_rfp_text_dq_risks(
                rfp=_rfp(),
                draft=draft,
                rfp_text="Do not alter the quotation form.",
                ledger_result=ledger,
            )
        joined = " ".join(risks).casefold()
        self.assertIn("altered quotation", joined)
        self.assertIn("wbe", joined)


class DqGatePassTests(unittest.TestCase):
    def test_attestation_and_risks_combined(self) -> None:
        draft = _draft(
            ProposalSection(
                id="s1",
                title="Compliance",
                content="We are E-Verify enrolled.",
                status="generated",
            )
        )
        rfp = _rfp(
            goNoGo="review",
            goNoGoAnalysis={
                "recommendation": "review",
                "criticalGaps": ["Confirm registration on SAM.gov"],
            },
        )

        gated_draft = draft.model_copy(
            update={
                "sections": [
                    draft.sections[0].model_copy(
                        update={
                            "content": "[VERIFY: E-Verify — confirm enrollment]\nWe pursue enrollment."
                        }
                    )
                ]
            }
        )

        class _Rep:
            everify_flags = 1
            conflict_flags = 0
            hours_flags = 0
            filler_flags = 0
            rno_flags = 0
            logs = ["everify gated"]

        with patch(
            "app.services.evidence_trust.legal_attestation_gate.apply_legal_attestation_gates",
            return_value=(gated_draft, _Rep()),
        ):
            result = run_scan_dq_gate_pass(
                draft=draft,
                research=None,
                rfp=rfp,
                rfp_text="gov RFP",
                ledger_result=None,
            )
        self.assertTrue(result.changed)
        self.assertTrue(result.disqualification_risks)
        self.assertTrue(any("legal-attestation" in g for g in result.human_decision_gaps))


class OrchestratorLoopTests(unittest.TestCase):
    def test_second_pass_when_additions_applied(self) -> None:
        draft = _draft(
            ProposalSection(
                id="s1", title="Approach", content="x", status="generated"
            )
        )
        research = ProposalResearchCache(
            rfpId="rfp-dq", updatedAt="2026-08-05T00:00:00+00:00"
        )
        empty = LedgerReconcileResult(
            draft=draft,
            changed=False,
            applied_additions=[],
            applied_merges=[],
            applied_cuts=[],
            logs=["ledger: noop"],
        )
        # First pass reports an addition; second is stable.
        added = LedgerReconcileResult(
            draft=draft,
            changed=True,
            applied_additions=[],
            applied_merges=[],
            applied_cuts=[],
            logs=["ledger: added"],
        )
        # Use a real AppliedRequirementAddition-shaped object via empty list
        # but force second pass with applied_cuts.
        from app.services.proposal_rfp_compliance import AppliedCutAction

        first = LedgerReconcileResult(
            draft=draft,
            changed=True,
            applied_additions=[],
            applied_merges=[],
            applied_cuts=[
                AppliedCutAction(
                    section_id="x",
                    section_title="Fluff",
                    words_removed=10,
                    had_evaluation_points=False,
                )
            ],
            logs=["ledger: cut"],
        )

        calls = {"n": 0}

        async def _fake_ledger(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return draft, research, first, []
            return draft, research, empty, []

        with patch(
            "app.services.proposal_rfp_compliance.apply_scan_ledger_pass",
            new=AsyncMock(side_effect=_fake_ledger),
        ), patch(
            "app.services.proposal_repository.asave_proposal_draft",
            new=AsyncMock(),
        ), patch(
            "app.services.proposal_scan_dq_orchestrator.run_scan_dq_gate_pass",
            side_effect=lambda **kw: __import__(
                "app.services.proposal_scan_dq_orchestrator", fromlist=["ScanDqGateResult"]
            ).ScanDqGateResult(
                draft=kw["draft"],
                research=kw["research"],
                disqualification_risks=[],
                human_decision_gaps=[],
                logs=["dq ok"],
                changed=False,
            ),
        ):
            result = asyncio.run(
                run_scan_coverage_orchestrator(
                    rfp_id="rfp-dq",
                    draft=draft,
                    research=research,
                    rfp=_rfp(),
                    rfp_text="text",
                )
            )
        self.assertEqual(result.loop_passes, 2)
        self.assertEqual(calls["n"], 2)


class FullBodyWiringTests(unittest.TestCase):
    def test_fulfill_body_calls_orchestrator(self) -> None:
        from app.services import proposal_fulfill_rfp_gaps as fulfill_mod

        source = inspect.getsource(fulfill_mod._run_fulfill_rfp_gaps_body)
        self.assertIn("run_scan_coverage_orchestrator", source)
        self.assertIn("DQ & gov-policy gate (agentic loop)", source)
        self.assertIn("disqualificationRisks", source)


if __name__ == "__main__":
    unittest.main()
