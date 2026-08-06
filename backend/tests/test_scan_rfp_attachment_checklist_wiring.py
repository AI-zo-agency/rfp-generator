"""Task 15 (scan-report half), wiring proof: the REAL Scan-RFP button entry
point (``run_fulfill_rfp_gaps(rfp_id, mode="verify_scrub_only")`` — the
frontend's only mode; see test_scan_rfp_reconciler_wiring.py's docstring for
the original defect this pattern guards against) must actually surface the
submission-attachment checklist split, not just the isolated helper
function tested in test_scan_rfp_attachment_checklist.py.

Drives the real entry point end to end against a local (non-Supabase)
database. The only things stubbed out are the database backend and the LLM
(``llm.is_configured`` -> False) — outstanding_submission_checklist_for_scan
itself makes zero LLM calls by construction, so this stub only guards the
OTHER passes this entry point runs (ledger reconcile drafting, truncation
repair) from making live calls, exactly as test_scan_rfp_reconciler_wiring.py
already established for this same entry point.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import config
from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services import proposal_repository as repo
from app.services.rfp_repository import upsert_rfp


def _rfp(rfp_id: str, **overrides) -> RfpRecord:
    fields = dict(
        id=rfp_id,
        title="Downtown Marketing Services",
        client="City of Rivergate",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-05",
        lastActivityNote="note",
        goNoGo="go",
        description=(
            "The City of Rivergate seeks a qualified marketing agency to design and "
            "execute a comprehensive downtown revitalization campaign across print, "
            "digital, and social channels for the next fiscal year. "
            "Describe the firm's financial stability in the proposal narrative. "
            "Vendor must submit a completed IRS Form W-9. "
            "A Certificate of Insurance is required with the submission."
        ),
    )
    fields.update(overrides)
    return RfpRecord(**fields)


class _RealDbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db = Path(self._tmpdir.name) / "scan-rfp-attachment-wiring.db"
        self._patchers = [
            patch.object(config.settings, "database_path", self._db),
            patch.object(repo, "_use_supabase", return_value=False),
            patch("app.services.rfp_repository._use_supabase", return_value=False),
            patch("app.services.supabase_db.use_supabase_db", return_value=False),
            patch("app.services.llm.is_configured", return_value=False),
        ]
        for p in self._patchers:
            p.start()
        repo.init_proposal_db()

    async def asyncTearDown(self) -> None:
        for p in reversed(self._patchers):
            p.stop()
        self._tmpdir.cleanup()


class RealScanPathSurfacesAttachmentChecklistTests(_RealDbTestCase):
    async def test_scan_reports_drafting_and_attachment_items_separately(self) -> None:
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-attachment-wiring-1"
        upsert_rfp(_rfp(rfp_id))
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(id="s1", title="Approach", content="Our approach is sound and complete."),
            ],
            updatedAt="2026-08-06T00:00:00Z",
        )
        await repo.asave_proposal_draft(draft)

        _review, _research, _draft_after, report = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )

        self.assertEqual(report.get("mode"), "verify_scrub_only")
        self.assertIn(
            "Financial stability narrative (in proposal body)",
            report.get("submissionNeedsDraftingTitles") or [],
        )
        self.assertIn("IRS Form W-9", report.get("submissionNeedsAttachmentTitles") or [])
        self.assertIn(
            "Certificate(s) of Insurance", report.get("submissionNeedsAttachmentTitles") or []
        )
        self.assertGreaterEqual(report.get("submissionNeedsAttachmentCount") or 0, 2)
        # Never mixed into the same list.
        self.assertNotIn(
            "IRS Form W-9", report.get("submissionNeedsDraftingTitles") or []
        )

    async def test_a_second_scan_after_a_human_attaches_the_document_stops_flagging_it(
        self,
    ) -> None:
        """Idempotence through the real path: once a human replaces a
        [MANUAL FILL] stub with real confirmation, the next Scan-RFP click
        must not re-flag that attachment."""
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-attachment-wiring-2"
        upsert_rfp(_rfp(rfp_id))
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(id="s1", title="Approach", content="Our approach is sound and complete."),
                ProposalSection(
                    id="w9",
                    title="IRS Form W-9",
                    content="Signed IRS Form W-9 is attached to this submission as Exhibit C.",
                ),
            ],
            updatedAt="2026-08-06T00:00:00Z",
        )
        await repo.asave_proposal_draft(draft)

        _review, _research, _draft_after, report = await run_fulfill_rfp_gaps(
            rfp_id, mode="verify_scrub_only"
        )

        self.assertNotIn("IRS Form W-9", report.get("submissionNeedsAttachmentTitles") or [])
        # The still-unresolved insurance certificate must still be flagged.
        self.assertIn(
            "Certificate(s) of Insurance", report.get("submissionNeedsAttachmentTitles") or []
        )


if __name__ == "__main__":
    unittest.main()
