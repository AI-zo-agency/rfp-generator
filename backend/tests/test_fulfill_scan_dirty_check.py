"""Complete & clean 'nothing changed' short-circuit — must never skip a real change."""

from __future__ import annotations

import unittest

from app.models.proposal import (
    ProposalDraft,
    ProposalPipelineCheckpoint,
    ProposalResearchCache,
    ProposalSection,
)
from app.services.proposal_pipeline_checkpoint import (
    compute_fulfill_scan_hash,
    fulfill_scan_is_already_clean,
)


def _draft(*sections: tuple[str, str]) -> ProposalDraft:
    return ProposalDraft(
        rfpId="r1",
        updatedAt="2026-08-13T00:00:00Z",
        sections=[
            ProposalSection(id=sid, title=sid, content=content) for sid, content in sections
        ],
    )


def _cp(**kw) -> ProposalPipelineCheckpoint:
    return ProposalPipelineCheckpoint(updatedAt="2026-08-13T00:00:00Z", **kw)


def _research(cp: ProposalPipelineCheckpoint | None) -> ProposalResearchCache:
    return ProposalResearchCache(rfpId="r1", updatedAt="2026-08-13T00:00:00Z", pipelineCheckpoint=cp)


class ComputeFulfillScanHashTests(unittest.TestCase):
    def test_stable_for_identical_input(self):
        d = _draft(("s1", "hello"))
        self.assertEqual(
            compute_fulfill_scan_hash(d, "rfp text"),
            compute_fulfill_scan_hash(d, "rfp text"),
        )

    def test_changes_when_section_content_changes(self):
        before = compute_fulfill_scan_hash(_draft(("s1", "hello")), "rfp text")
        after = compute_fulfill_scan_hash(_draft(("s1", "hello world")), "rfp text")
        self.assertNotEqual(before, after)

    def test_changes_when_rfp_text_changes(self):
        d = _draft(("s1", "hello"))
        self.assertNotEqual(
            compute_fulfill_scan_hash(d, "rfp text v1"),
            compute_fulfill_scan_hash(d, "rfp text v2"),
        )

    def test_changes_when_a_section_is_added(self):
        before = compute_fulfill_scan_hash(_draft(("s1", "hello")), "rfp text")
        after = compute_fulfill_scan_hash(
            _draft(("s1", "hello"), ("s2", "new")), "rfp text"
        )
        self.assertNotEqual(before, after)


class FulfillScanIsAlreadyCleanTests(unittest.TestCase):
    def test_true_when_hash_matches_and_fresh_run(self):
        cp = _cp(lastCleanFulfillScanHash="abc123")
        self.assertTrue(
            fulfill_scan_is_already_clean(
                research=_research(cp), resume_at=1, current_hash="abc123"
            )
        )

    def test_false_when_hash_differs_real_change_present(self):
        cp = _cp(lastCleanFulfillScanHash="abc123")
        self.assertFalse(
            fulfill_scan_is_already_clean(
                research=_research(cp), resume_at=1, current_hash="different"
            )
        )

    def test_false_when_resuming_a_stopped_run_even_if_hash_matches(self):
        """A resume must always go through the tested step-skip logic —
        the dirty-check only short-circuits a brand-new invocation."""
        cp = _cp(lastCleanFulfillScanHash="abc123", resumeFulfillStep=12)
        self.assertFalse(
            fulfill_scan_is_already_clean(
                research=_research(cp), resume_at=12, current_hash="abc123"
            )
        )

    def test_false_when_no_saved_hash_yet(self):
        self.assertFalse(
            fulfill_scan_is_already_clean(
                research=_research(_cp()), resume_at=1, current_hash="abc123"
            )
        )

    def test_false_when_no_research_at_all(self):
        self.assertFalse(
            fulfill_scan_is_already_clean(research=None, resume_at=1, current_hash="abc123")
        )


if __name__ == "__main__":
    unittest.main()
