"""Review & Fix (targeted_fix) per-section checkpoint: stop, restart, skip done."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalPipelineCheckpoint, ProposalResearchCache
from app.services.proposal_pipeline_checkpoint import (
    complete_fulfill_scan,
    fulfill_resume_step,
    record_generation_stopped,
    record_phase_started,
    record_targeted_fix_section_done,
    targeted_fix_done_sections,
)


def _cp(**kw) -> ProposalPipelineCheckpoint:
    return ProposalPipelineCheckpoint(updatedAt="2026-09-03T00:00:00Z", **kw)


def _research(cp: ProposalPipelineCheckpoint | None) -> ProposalResearchCache:
    return ProposalResearchCache(
        rfpId="r1", updatedAt="2026-09-03T00:00:00Z", pipelineCheckpoint=cp
    )


class _SaveHarness:
    """Patches the cache read/write pair and records every saved checkpoint."""

    def __init__(self, research: ProposalResearchCache):
        self.research = research
        self.saved: list[ProposalResearchCache] = []

    async def _save(self, updated):
        self.saved.append(updated)
        self.research = updated
        return updated

    async def _get(self, *_a, **_kw):
        return self.research

    def patches(self):
        mod = "app.services.proposal_pipeline_checkpoint"
        return (
            patch(f"{mod}.aget_research_cache", new=AsyncMock(side_effect=self._get)),
            patch(f"{mod}.asave_research_cache", new=AsyncMock(side_effect=self._save)),
        )

    @property
    def checkpoint(self) -> ProposalPipelineCheckpoint:
        cp = self.saved[-1].pipeline_checkpoint
        assert cp is not None
        return cp


class DoneSectionReadTests(unittest.TestCase):
    def test_no_checkpoint_means_nothing_done(self):
        self.assertEqual(targeted_fix_done_sections(None), set())
        self.assertEqual(targeted_fix_done_sections(_research(None)), set())

    def test_reads_ids_from_a_targeted_fix_checkpoint(self):
        cp = _cp(scanProfile="targeted_fix", targetedFixDoneSectionIds=["a", "b"])
        self.assertEqual(targeted_fix_done_sections(_research(cp)), {"a", "b"})

    def test_ids_survive_a_run_under_another_profile(self):
        # A Complete & clean run in between must NOT cost the user their Review
        # & Fix place: the field is targeted_fix's own, so no other profile can
        # misread it. Gating on scan_profile silently reset users to section 1.
        cp = _cp(scanProfile="full", targetedFixDoneSectionIds=["a", "b"])
        self.assertEqual(targeted_fix_done_sections(_research(cp)), {"a", "b"})

    def test_targeted_fix_counter_never_becomes_a_full_scan_step(self):
        # Section 7 of Review & Fix is not step 7 of Complete & clean.
        cp = _cp(scanProfile="targeted_fix", stepIndex=7, resumeFulfillStep=7)
        self.assertEqual(fulfill_resume_step(_research(cp)), 1)


class RecordSectionDoneTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_and_accumulates_without_duplicates(self):
        h = _SaveHarness(_research(_cp(inProgressPhase="fulfill-scan")))
        p1, p2 = h.patches()
        with p1, p2:
            await record_targeted_fix_section_done("r1", "sec-a", step_index=1, step_total=3)
            await record_targeted_fix_section_done("r1", "sec-b", step_index=2, step_total=3)
            await record_targeted_fix_section_done("r1", "sec-b", step_index=2, step_total=3)
        self.assertEqual(h.checkpoint.targeted_fix_done_section_ids, ["sec-a", "sec-b"])
        self.assertEqual(h.checkpoint.scan_profile, "targeted_fix")

    async def test_stop_then_restart_keeps_the_done_sections(self):
        h = _SaveHarness(
            _research(
                _cp(
                    inProgressPhase="fulfill-scan",
                    scanProfile="targeted_fix",
                    stepIndex=2,
                    stepTotal=5,
                    targetedFixDoneSectionIds=["sec-a", "sec-b"],
                    lastCompletedPhase="phase-4-review",
                )
            )
        )
        p1, p2 = h.patches()
        with p1, p2:
            await record_generation_stopped("r1", "fulfill-scan")
            self.assertEqual(
                h.checkpoint.targeted_fix_done_section_ids, ["sec-a", "sec-b"]
            )
            await record_phase_started("r1", "fulfill-scan", scan_profile="targeted_fix")
        self.assertEqual(
            targeted_fix_done_sections(_research(h.checkpoint)), {"sec-a", "sec-b"}
        )

    async def test_starting_a_full_scan_keeps_the_targeted_progress(self):
        # Only a COMPLETED Review & Fix pass (complete_fulfill_scan) clears
        # these — never the start of some other phase.
        h = _SaveHarness(
            _research(
                _cp(
                    scanProfile="targeted_fix",
                    targetedFixDoneSectionIds=["sec-a"],
                    targetedFixStructureDone=True,
                )
            )
        )
        p1, p2 = h.patches()
        with p1, p2:
            await record_phase_started("r1", "fulfill-scan", scan_profile="full")
        self.assertEqual(h.checkpoint.targeted_fix_done_section_ids, ["sec-a"])
        self.assertTrue(h.checkpoint.targeted_fix_structure_done)

    async def test_completing_the_run_clears_the_done_sections(self):
        h = _SaveHarness(
            _research(
                _cp(
                    inProgressPhase="fulfill-scan",
                    scanProfile="targeted_fix",
                    targetedFixDoneSectionIds=["sec-a", "sec-b"],
                )
            )
        )
        p1, p2 = h.patches()
        with p1, p2:
            await complete_fulfill_scan("r1", scan_hash="h1")
        self.assertEqual(h.checkpoint.targeted_fix_done_section_ids, [])
        self.assertEqual(h.checkpoint.last_clean_fulfill_scan_hash, "h1")


if __name__ == "__main__":
    unittest.main()
