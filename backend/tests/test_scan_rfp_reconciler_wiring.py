"""Task 5b/9: the REAL Scan-RFP button entry point must reach the reconciler,
and ADD must actually add a section, not just report one.

Root defect (verified at HEAD 9dce731):

    frontend/src/components/ProposalDraftWorkspace.tsx:1349 hardcodes
        mode: "verify_scrub_only"
    backend/app/services/proposal_fulfill_rfp_gaps.py:315-321 returns EARLY
        for that mode into run_verify_scrub_only_scan(rfp_id)

Task 5 built ``reconcile_requirement_ledger`` and wired it into
``proposal_rfp_compliance.py`` / ``proposal_submission_gap_finalizer.py`` —
but nothing on the button's actual code path (``mode="verify_scrub_only"``,
the ONLY mode the frontend ever sends) ever called it. Clicking "SCAN RFP &
ADD MISSING PIECES" never reached the reconciler. Task 5 also shipped ADD
surfaced-only (matcher measured 6/10 on wording variants) — so even once the
button reached the reconciler, "ADD MISSING PIECES" never actually added
anything. Task 9 wires ADD to apply now that the matcher is measured 8/10
with zero false positives (two unsafe aliases removed this round).

``test_scan_rfp_reconciler.py`` already proves the reconciler is correct in
isolation (calling ``reconcile_requirement_ledger`` directly). This file
proves something different and stronger: that driving the REAL,
button-shaped entry point — ``run_fulfill_rfp_gaps(rfp_id,
mode="verify_scrub_only")`` — end to end against a local (non-Supabase)
database reaches the same result. No mocking of the reconciler itself; the
only thing stubbed out is the database backend (sqlite instead of Supabase)
and the LLM (never configured in tests, and no draft section carries a
[VERIFY] tag, so the optional-scrub LLM path never fires).
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
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.models.rfp import RfpRecord
from app.services import proposal_repository as repo
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


def _req(rid: str, text: str, **kw) -> LedgerRequirement:
    kw.setdefault("source", "required_content")
    kw.setdefault("mandatory", True)
    kw.setdefault("satisfiedBy", [])
    return LedgerRequirement(id=rid, text=text, **kw)


class _RealDbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db = Path(self._tmpdir.name) / "scan-rfp-wiring.db"
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

    async def _seed_missing_dup_and_over_budget(self, rfp_id: str) -> ProposalDraft:
        """A draft with all three signals together: a mandatory requirement
        with no matching section (ADD), a requirement claimed by three
        sections (MERGE), and a manuscript over the 350-word page budget
        (CUT) — the same combined scenario as
        ``test_scan_rfp_reconciler.CombinedEndToEndProofTests``, but seeded
        into a real database and driven through the real entry point.
        """
        upsert_rfp(_rfp(rfp_id))

        rfp_sections = [
            RfpSectionMap(id="sec-a", title="Section 1.5", evaluationWeight=10),
            RfpSectionMap(id="sec-b", title="Attachments Checklist"),
            RfpSectionMap(id="sec-c", title="Contract Acknowledgment"),
        ]
        ledger = RequirementLedger(
            requirements=[
                # Mandatory AND scored — matches the task-9 verification
                # scenario ("missing a mandatory scored requirement"), and
                # exercises the scored-first ADD ordering.
                _req(
                    "r-missing",
                    "A signed cover letter",
                    source="scored_criterion",
                    points=5.0,
                    satisfiedBy=[],
                ),
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
            requirementLedger=ledger,
            rfpSections=rfp_sections,
            updatedAt="2026-08-06T00:00:00Z",
        )
        await repo.asave_research_cache(research)
        return draft


class RealVerifyScrubOnlyPathReachesReconcilerTests(_RealDbTestCase):
    async def test_triplicated_requirement_is_merged_through_the_real_path(self) -> None:
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-scan-wiring-merge"
        draft_before = await self._seed_missing_dup_and_over_budget(rfp_id)
        before_total_words = sum(len(s.content.split()) for s in draft_before.sections)

        review, research_after, draft_after, report = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )

        after_total_words = sum(len(s.content.split()) for s in draft_after.sections)

        print("\n=== Task 9 — REAL verify_scrub_only entry point, first run ===")
        print(f"report.mode = {report.get('mode')!r}")
        print(f"before: {before_total_words}w across {len(draft_before.sections)} sections")
        print(f"after:  {after_total_words}w across {len(draft_after.sections)} sections")
        print(f"ledgerAdditionsApplied={report.get('ledgerAdditionsApplied')}")
        print(f"ledgerMergesApplied={report.get('ledgerMergesApplied')}")
        print(f"ledgerCutsApplied={report.get('ledgerCutsApplied')}")
        for line in report.get("logs", []):
            print(f"  log: {line}")
        for gap in report.get("humanDecisionGaps", []):
            print(f"  human-decision-gap: {gap}")

        self.assertEqual(report.get("mode"), "verify_scrub_only")

        # ADD: applied. A new [MANUAL FILL] stub section is created for the
        # missing, mandatory, SCORED cover-letter requirement — it can no
        # longer be silently dropped.
        self.assertEqual(
            len(draft_after.sections), 6, "ADD must create exactly one new section"
        )
        added = next(s for s in draft_after.sections if s.id == "ledger-r-missing")
        self.assertIn("[MANUAL FILL", added.content)
        self.assertIn("A signed cover letter", added.content)
        self.assertEqual(report.get("ledgerAdditionsApplied"), 1)
        self.assertTrue(
            any(
                "cover letter" in g.casefold()
                for g in report.get("humanDecisionGaps", [])
            ),
            "the added section must still be surfaced as needing human content",
        )

        # MERGE: applied. The insurance requirement (claimed by sec-a/b/c)
        # collapses to a single owner; the others get a cross-reference, not
        # a restatement.
        sec_a = next(s for s in draft_after.sections if s.id == "sec-a")
        sec_b = next(s for s in draft_after.sections if s.id == "sec-b")
        sec_c = next(s for s in draft_after.sections if s.id == "sec-c")
        self.assertEqual(sec_a.content, SEC_A_CONTENT, "owner section must not change")
        self.assertIn("[LEDGER-XREF:r-dup]", sec_b.content)
        self.assertIn("[LEDGER-XREF:r-dup]", sec_c.content)
        self.assertEqual(report.get("ledgerMergesApplied"), 1)

        # CUT: applied. scored content is trimmed only after unscored
        # content, never below its protected floor, and the MERGE owner
        # (sec-a) is never cut in the same pass — the C2 fix.
        scored_after = next(s for s in draft_after.sections if s.id == "sec-scored")
        self.assertGreaterEqual(len(scored_after.content.split()), 150)
        self.assertGreaterEqual(report.get("ledgerCutsApplied", 0), 1)
        self.assertEqual(sec_a.content, SEC_A_CONTENT, "MERGE owner must never be cut")

    async def test_running_the_real_entry_point_twice_is_a_no_op_the_second_time(
        self,
    ) -> None:
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-scan-wiring-idempotent"
        await self._seed_missing_dup_and_over_budget(rfp_id)

        _r1, _rc1, draft1, report1 = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )
        _r2, _rc2, draft2, report2 = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )

        print("\n=== Task 9 — REAL verify_scrub_only entry point, idempotence ===")
        print(
            f"first:  additions={report1.get('ledgerAdditionsApplied')} "
            f"merges={report1.get('ledgerMergesApplied')} "
            f"cuts={report1.get('ledgerCutsApplied')}"
        )
        print(
            f"second: additions={report2.get('ledgerAdditionsApplied')} "
            f"merges={report2.get('ledgerMergesApplied')} "
            f"cuts={report2.get('ledgerCutsApplied')}"
        )

        self.assertGreaterEqual(report1.get("ledgerAdditionsApplied", 0), 1)
        self.assertGreaterEqual(report1.get("ledgerMergesApplied", 0), 1)
        self.assertGreaterEqual(report1.get("ledgerCutsApplied", 0), 1)

        # Second run must be a genuine no-op: it must NOT add a second copy
        # of the same section (the hard idempotence requirement for ADD).
        self.assertEqual(report2.get("ledgerAdditionsApplied"), 0)
        self.assertEqual(report2.get("ledgerMergesApplied"), 0)
        self.assertEqual(report2.get("ledgerCutsApplied"), 0)
        self.assertEqual(
            len(draft2.sections), len(draft1.sections), "must not duplicate the added section"
        )
        self.assertEqual(
            [s.content for s in draft2.sections],
            [s.content for s in draft1.sections],
            "second run must not change the draft at all",
        )

    async def test_missing_requirement_is_added_through_the_real_path(self) -> None:
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-scan-wiring-add-only"
        upsert_rfp(_rfp(rfp_id, pageLimit=None))
        ledger = RequirementLedger(
            requirements=[_req("r1", "A signed cover letter", satisfiedBy=[])]
        )
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(id="s1", title="Approach", content="Our approach is sound.")
            ],
            updatedAt="2026-08-06T00:00:00Z",
        )
        await repo.asave_proposal_draft(draft)
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id, requirementLedger=ledger, updatedAt="2026-08-06T00:00:00Z"
            )
        )

        _review, _research, draft_after, report = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )

        self.assertEqual(len(draft_after.sections), 2, "ADD must create exactly one new section")
        original = next(s for s in draft_after.sections if s.id == "s1")
        self.assertEqual(
            original.content,
            "Our approach is sound.",
            "the existing section content must be untouched",
        )
        added = next(s for s in draft_after.sections if s.id == "ledger-r1")
        self.assertIn("[MANUAL FILL", added.content)
        self.assertIn("A signed cover letter", added.content)
        self.assertEqual(report.get("ledgerAdditionsApplied"), 1)

        # Idempotence: running it again on the now-added draft must not
        # create a second copy.
        _review2, _research2, draft_after2, report2 = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )
        self.assertEqual(len(draft_after2.sections), 2)
        self.assertEqual(report2.get("ledgerAdditionsApplied"), 0)

    async def test_missing_ledger_page_limit_and_empty_ledger_degrade_gracefully(
        self,
    ) -> None:
        """No ledger / no page limit -> no exception, no change."""
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-scan-wiring-degrade"
        upsert_rfp(_rfp(rfp_id, pageLimit=None))
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(id="s1", title="Approach", content="Plain prose, no tags.")
            ],
            updatedAt="2026-08-06T00:00:00Z",
        )
        await repo.asave_proposal_draft(draft)
        # No research cache saved at all -> no ledger to reconcile.

        _review, _research, draft_after, report = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )

        self.assertEqual(draft_after.sections[0].content, "Plain prose, no tags.")
        self.assertEqual(report.get("ledgerMergesApplied"), 0)
        self.assertEqual(report.get("ledgerCutsApplied"), 0)
        self.assertEqual(report.get("ledgerAdditionsApplied"), 0)


if __name__ == "__main__":
    unittest.main()
