"""Bug 1 fix: the reconciler must not no-op on every EXISTING proposal.

Root defect (verified against a real user's live Scan-RFP run —
scan-rfp:report ... ledger_added=0 ledger_added_titles=[] ledger_merged=0
ledger_cut=0):

``reconcile_requirement_ledger`` (proposal_rfp_compliance.py) reads
``research.requirement_ledger`` and no-ops when it is missing. The ledger is
only ever built during Phase 2 of GENERATION
(``build_requirement_ledger``, called from
``proposal_intelligence/assembler.py.derive_legacy_fields``) — nothing on the
scan path ever calls it. Every proposal generated before that field existed
(or whose ledger was wiped by a since-fixed regeneration bug) therefore has
no ``requirement_ledger`` at all, and the reconciler — whose entire purpose is
to fix EXISTING proposals — can never touch a single one of them.

Fix: ``_build_ledger_on_demand`` (proposal_rfp_compliance.py) rebuilds the
ledger from what Phase 2 already persisted independently of the ledger field
itself — ``research.proposal_execution_plan.opportunity.compliance.items``,
``.opportunity.evaluation.criteria``, and ``research.rfp_sections`` — reusing
``build_requirement_ledger`` and its matcher verbatim (zero LLM calls, pure
Python; no second ledger builder). When those inputs genuinely are not
persisted (a proposal from before the intelligence layer existed at all), the
reconcile stays a no-op but now says exactly why, both server-side (log) and
in the report the Scan-RFP banner reads from (``ledgerCheckSkippedReason``).

This file drives the REAL button entry point
(``run_fulfill_rfp_gaps(rfp_id, mode="verify_scrub_only")``) against a real
sqlite database — the same pattern as
``test_scan_rfp_reconciler_wiring.py`` — because this exact defect survived
two earlier rounds that only verified the reconciler in isolation with a
ledger handed to it directly, never a proposal shaped like a real one.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import config
from app.models.proposal import (
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
    RfpSectionMap,
)
from app.models.rfp import RfpRecord
from app.services import proposal_repository as repo
from app.services.proposal_intelligence.schemas import (
    ComplianceItem,
    ComplianceMatrix,
    EvaluationAnalysis,
    EvaluationCriterion,
    ProposalExecutionPlan,
)
from app.services.rfp_repository import upsert_rfp

SEC_A_CONTENT = "We carry $2M general liability insurance.\n\nMore narrative."
SEC_B_CONTENT = "Insurance coverage of $2M is maintained per 1.5.\n\nOther attachments."
SEC_C_CONTENT = "We acknowledge $2M insurance per contract terms.\n\nSignature block."


def _para(word: str, count: int) -> str:
    return " ".join([word] * count)


UNSCORED_CONTENT = _para("filler", 50) + "\n\n" + _para("filler", 50)  # 100w
SCORED_CONTENT = "\n\n".join(_para("technical", 50) for _ in range(6))  # 300w


def _rfp(rfp_id: str, **overrides) -> RfpRecord:
    fields = dict(
        id=rfp_id,
        title="Downtown Roadway Resurfacing Design Services",
        client="City of Rivergate",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-05",
        lastActivityNote="note",
        goNoGo="go",
        description=_para("background context sentence", 60),
        pageLimit=1,  # 1 page * 350 words/page = 350-word budget
    )
    fields.update(overrides)
    return RfpRecord(**fields)


def _execution_plan_with_compliance_and_evaluation(rfp_id: str) -> ProposalExecutionPlan:
    """A realistic PERSISTED Phase 2 plan — the exact shape a real proposal's
    research.proposal_execution_plan has — with no requirement_ledger anywhere.
    Mirrors a proposal generated before Task 1 shipped the ledger field."""
    plan = ProposalExecutionPlan(rfpId=rfp_id)
    plan.opportunity.compliance = ComplianceMatrix(
        items=[
            ComplianceItem(id="comp-cover", requirement="A signed cover letter", mandatory=True),
            ComplianceItem(id="comp-insurance", requirement="Proof of insurance", mandatory=True),
        ],
        confidence=0.9,
    )
    plan.opportunity.evaluation = EvaluationAnalysis(
        criteria=[EvaluationCriterion(name="Technical Approach", weight=30.0, priorityRank=1)],
        confidence=0.9,
    )
    return plan


class _RealDbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db = Path(self._tmpdir.name) / "scan-rfp-ledger-rebuild.db"
        self._patchers = [
            patch.object(config.settings, "database_path", self._db),
            patch.object(repo, "_use_supabase", return_value=False),
            patch("app.services.rfp_repository._use_supabase", return_value=False),
            patch("app.services.supabase_db.use_supabase_db", return_value=False),
            # Zero-spend / hermetic — no test in this file exercises the
            # ADD-drafting or truncation-repair LLM paths.
            patch("app.services.llm.is_configured", return_value=False),
        ]
        for p in self._patchers:
            p.start()
        repo.init_proposal_db()

    async def asyncTearDown(self) -> None:
        for p in reversed(self._patchers):
            p.stop()
        self._tmpdir.cleanup()

    async def _seed_pre_ledger_proposal(self, rfp_id: str) -> ProposalDraft:
        """A proposal shaped exactly like the user's real, EXISTING one: Phase
        2 ran (rfp_sections + proposal_execution_plan persisted, with real
        compliance items and a scored criterion), but requirement_ledger was
        never populated because this proposal predates it. Same
        ADD+MERGE+CUT combined scenario as
        test_scan_rfp_reconciler_wiring.py, but reached via the on-demand
        build instead of a hand-built ledger.
        """
        upsert_rfp(_rfp(rfp_id))

        rfp_sections = [
            RfpSectionMap(
                id="sec-a", title="Section 1.5", evaluationWeight=10,
                requirements=["Proof of insurance"],
            ),
            RfpSectionMap(
                id="sec-b", title="Attachments Checklist",
                requirements=["Proof of insurance"],
            ),
            RfpSectionMap(
                id="sec-c", title="Contract Acknowledgment",
                requirements=["Proof of insurance"],
            ),
            RfpSectionMap(id="sec-unscored", title="Company Overview"),
            RfpSectionMap(id="sec-scored", title="Technical Approach"),
        ]
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(id="sec-a", title="Section 1.5", content=SEC_A_CONTENT),
                ProposalSection(
                    id="sec-b", title="Attachments Checklist", content=SEC_B_CONTENT
                ),
                ProposalSection(
                    id="sec-c", title="Contract Acknowledgment", content=SEC_C_CONTENT
                ),
                ProposalSection(
                    id="sec-unscored", title="Company Overview", content=UNSCORED_CONTENT
                ),
                ProposalSection(
                    id="sec-scored", title="Technical Approach", content=SCORED_CONTENT
                ),
            ],
            updatedAt="2026-08-06T00:00:00Z",
        )
        await repo.asave_proposal_draft(draft)

        research = ProposalResearchCache(
            rfpId=rfp_id,
            rfpSections=rfp_sections,
            proposalExecutionPlan=_execution_plan_with_compliance_and_evaluation(rfp_id),
            # requirement_ledger deliberately omitted — this is the bug's
            # exact precondition: it defaults to None.
            updatedAt="2026-08-06T00:00:00Z",
        )
        self.assertIsNone(research.requirement_ledger, "precondition: no persisted ledger")
        await repo.asave_research_cache(research)
        return draft


class LedgerRebuildsOnDemandForExistingProposalsTests(_RealDbTestCase):
    async def test_ledger_added_merged_cut_are_no_longer_uniformly_zero(self) -> None:
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-ledger-rebuild-combined"
        await self._seed_pre_ledger_proposal(rfp_id)

        review, research_after, draft_after, report = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )

        print("\n=== Bug 1 — on-demand ledger rebuild through the real entry point ===")
        print(f"built ledger requirement count = {len(research_after.requirement_ledger.requirements)}")
        print(f"ledgerAdditionsApplied = {report.get('ledgerAdditionsApplied')}")
        print(f"ledgerAdditionsSectionTitles = {report.get('ledgerAdditionsSectionTitles')}")
        print(f"ledgerMergesApplied = {report.get('ledgerMergesApplied')}")
        print(f"ledgerCutsApplied = {report.get('ledgerCutsApplied')}")
        print(f"ledgerCheckSkippedReason = {report.get('ledgerCheckSkippedReason')}")
        for line in report.get("logs", []):
            print(f"  log: {line}")

        # The user's exact live symptom: these must no longer be uniformly 0
        # when the proposal genuinely has an unmatched mandatory requirement
        # (cover letter), a triplicated one (insurance), and a page overage.
        self.assertEqual(report.get("ledgerAdditionsApplied"), 1)
        self.assertIn("A signed cover letter"[:120], report.get("ledgerAdditionsSectionTitles") or [])
        self.assertEqual(report.get("ledgerMergesApplied"), 1)
        self.assertGreaterEqual(report.get("ledgerCutsApplied", 0), 1)
        self.assertIsNone(report.get("ledgerCheckSkippedReason"))

        added = next(s for s in draft_after.sections if s.id == "ledger-comp-cover")
        self.assertIn("[MANUAL FILL", added.content)

        sec_a = next(s for s in draft_after.sections if s.id == "sec-a")
        sec_b = next(s for s in draft_after.sections if s.id == "sec-b")
        sec_c = next(s for s in draft_after.sections if s.id == "sec-c")
        self.assertEqual(sec_a.content, SEC_A_CONTENT, "MERGE owner must not change")
        self.assertIn("[LEDGER-XREF:comp-insurance]", sec_b.content)
        self.assertIn("[LEDGER-XREF:comp-insurance]", sec_c.content)

        # Persistence: the built ledger must be written back so the next
        # click does not rebuild it. 3 requirements: cover letter, insurance,
        # and the Technical Approach scored criterion.
        self.assertIsNotNone(research_after.requirement_ledger)
        self.assertEqual(len(research_after.requirement_ledger.requirements), 3)

        # Re-read straight from the DB (not the in-memory return value) to
        # prove it actually persisted, not just that this call's return value
        # carried it.
        from_db = repo.get_research_cache(rfp_id)
        self.assertIsNotNone(from_db.requirement_ledger)
        self.assertEqual(len(from_db.requirement_ledger.requirements), 3)

    async def test_second_scan_is_idempotent_and_does_not_rebuild(self) -> None:
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-ledger-rebuild-idempotent"
        await self._seed_pre_ledger_proposal(rfp_id)

        _r1, research1, draft1, report1 = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )
        _r2, research2, draft2, report2 = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )

        print("\n=== Bug 1 — idempotence across two clicks ===")
        print(
            f"first:  added={report1.get('ledgerAdditionsApplied')} "
            f"merged={report1.get('ledgerMergesApplied')} cut={report1.get('ledgerCutsApplied')}"
        )
        print(
            f"second: added={report2.get('ledgerAdditionsApplied')} "
            f"merged={report2.get('ledgerMergesApplied')} cut={report2.get('ledgerCutsApplied')}"
        )

        self.assertGreaterEqual(report1.get("ledgerAdditionsApplied", 0), 1)

        # The second scan must not re-add, re-merge, re-cut, or rebuild —
        # the ledger persisted by the first call is read back as-is.
        self.assertEqual(report2.get("ledgerAdditionsApplied"), 0)
        self.assertEqual(report2.get("ledgerMergesApplied"), 0)
        self.assertEqual(report2.get("ledgerCutsApplied"), 0)
        self.assertIsNone(report2.get("ledgerCheckSkippedReason"))
        self.assertEqual(
            len(research1.requirement_ledger.requirements),
            len(research2.requirement_ledger.requirements),
            "the second pass must read the persisted ledger back, not rebuild a new one",
        )
        self.assertEqual(
            [s.content for s in draft2.sections],
            [s.content for s in draft1.sections],
            "second run must not change the draft at all",
        )

    async def test_proposal_with_no_execution_plan_at_all_stays_a_no_op_but_says_why(
        self,
    ) -> None:
        """Genuinely pre-intelligence-layer proposal: no proposal_execution_plan,
        no requirement_ledger. Must NOT fabricate a ledger — must stay a
        no-op, but the report must say exactly why instead of a silent 0."""
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-ledger-rebuild-no-plan"
        upsert_rfp(_rfp(rfp_id, pageLimit=None))
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(id="s1", title="Approach", content="Plain prose, no tags.")
            ],
            updatedAt="2026-08-06T00:00:00Z",
        )
        await repo.asave_proposal_draft(draft)
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                rfpSections=[RfpSectionMap(id="s1", title="Approach")],
                updatedAt="2026-08-06T00:00:00Z",
                # proposal_execution_plan and requirement_ledger both absent.
            )
        )

        _review, research_after, draft_after, report = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )

        print("\n=== Bug 1 — no execution plan persisted: honest no-op ===")
        print(f"ledgerCheckSkippedReason = {report.get('ledgerCheckSkippedReason')!r}")

        self.assertEqual(draft_after.sections[0].content, "Plain prose, no tags.")
        self.assertEqual(report.get("ledgerAdditionsApplied"), 0)
        self.assertEqual(report.get("ledgerMergesApplied"), 0)
        self.assertEqual(report.get("ledgerCutsApplied"), 0)
        self.assertIsNone(research_after.requirement_ledger, "must not fabricate a ledger")
        reason = report.get("ledgerCheckSkippedReason")
        self.assertIsNotNone(reason, "must say WHY, not silently no-op")
        self.assertIn("proposal execution plan", reason.casefold())


if __name__ == "__main__":
    unittest.main()
