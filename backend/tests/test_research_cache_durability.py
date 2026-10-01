"""Task 1b: five research-cache fields must survive a Sections 1-3 regeneration.

_generate_sections_1_3_inner (proposal_generator.py:1741, and identically the
_persist_sections_1_3_partial rebuild at :968) constructs a fresh
ProposalResearchCache from a hand-written whitelist of prior fields. Task 1
fixed requirement_ledger by adding it to merge_research_preserve_audit_fields.
The same hole still drops five more fields that are never in the rebuild
whitelist and never protected by the merge helper:

    manuscript_locks, proof_points, section_queries,
    loss_lessons, evidence_allocation

Every test here is a REAL sqlite round trip via proposal_repository
save/get_research_cache, not a mock — the defect lives in the save path,
so a mocked store would not see it. Pattern follows
tests/test_requirement_ledger.py::LedgerSurvivesRoutineResavesTests exactly.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import config
from app.models.proposal import (
    LossLesson,
    ManuscriptLocks,
    ProofPoint,
    ProposalResearchCache,
)
from app.services import proposal_repository as repo


# Verified live KB tier data.
def _sections_1_3_rebuild_payload(rfp_id: str, prior: ProposalResearchCache, when: str) -> ProposalResearchCache:
    """Mirrors the EXACT whitelist _generate_sections_1_3_inner (proposal_generator.py:1741)
    and _persist_sections_1_3_partial (:968) construct — forwards rfpSections/questions/
    evidenceCorpus/retrievalRounds/coverageThreshold/pipelineCheckpoint/brandVoice, and
    nothing else. None of the six fields under test appear here, exactly like production.
    """
    return ProposalResearchCache(
        rfpId=rfp_id,
        rfpSections=prior.rfp_sections,
        questions=prior.questions,
        evidenceCorpus=prior.evidence_corpus,
        retrievalRounds=prior.retrieval_rounds,
        coverageThreshold=prior.coverage_threshold,
        pipelineCheckpoint=prior.pipeline_checkpoint,
        updatedAt=when,
    )


class ResearchCacheDurabilityTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db = Path(self._tmpdir.name) / "durability.db"
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


class ManuscriptLocksSurviveRegenerationTests(ResearchCacheDurabilityTestBase):
    async def test_manuscript_locks_survive_sections_1_3_regeneration(self) -> None:
        rfp_id = "rfp-locks"
        locks = ManuscriptLocks(
            primaryContactName="Jane Doe",
            primaryContactTitle="VP Marketing",
            requiredKpis=["visitor arrivals"],
        )
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                manuscriptLocks=locks,
                updatedAt="2026-08-05T00:00:00Z",
            )
        )
        prior = await repo.aget_research_cache(rfp_id)
        self.assertIsNotNone(prior.manuscript_locks)

        await repo.asave_research_cache(
            _sections_1_3_rebuild_payload(rfp_id, prior, "2026-08-05T01:00:00Z")
        )

        after = await repo.aget_research_cache(rfp_id)
        self.assertIsNotNone(
            after.manuscript_locks,
            "manuscript_locks was wiped by a routine sections-1-3 regeneration",
        )
        self.assertEqual(after.manuscript_locks.primary_contact_name, "Jane Doe")

    async def test_a_freshly_built_manuscript_locks_still_overwrites_the_stored_one(self) -> None:
        rfp_id = "rfp-locks-refresh"
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                manuscriptLocks=ManuscriptLocks(primaryContactName="Stale"),
                updatedAt="2026-08-05T00:00:00Z",
            )
        )
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                manuscriptLocks=ManuscriptLocks(primaryContactName="Fresh"),
                updatedAt="2026-08-05T02:00:00Z",
            )
        )
        reloaded = await repo.aget_research_cache(rfp_id)
        self.assertEqual(reloaded.manuscript_locks.primary_contact_name, "Fresh")


class ProofPointsSurviveRegenerationTests(ResearchCacheDurabilityTestBase):
    async def test_proof_points_survive_sections_1_3_regeneration(self) -> None:
        rfp_id = "rfp-proof-points"
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                proofPoints=[
                    ProofPoint(requirement="Cover letter", caseStudy="Case A")
                ],
                updatedAt="2026-08-05T00:00:00Z",
            )
        )
        prior = await repo.aget_research_cache(rfp_id)
        self.assertTrue(prior.proof_points)

        await repo.asave_research_cache(
            _sections_1_3_rebuild_payload(rfp_id, prior, "2026-08-05T01:00:00Z")
        )

        after = await repo.aget_research_cache(rfp_id)
        self.assertTrue(
            after.proof_points,
            "proof_points was wiped by a routine sections-1-3 regeneration",
        )
        self.assertEqual(after.proof_points[0].case_study, "Case A")

    async def test_freshly_built_proof_points_still_overwrite_the_stored_ones(self) -> None:
        rfp_id = "rfp-proof-points-refresh"
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                proofPoints=[ProofPoint(requirement="Stale", caseStudy="Stale case")],
                updatedAt="2026-08-05T00:00:00Z",
            )
        )
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                proofPoints=[ProofPoint(requirement="Fresh", caseStudy="Fresh case")],
                updatedAt="2026-08-05T02:00:00Z",
            )
        )
        reloaded = await repo.aget_research_cache(rfp_id)
        self.assertEqual([p.case_study for p in reloaded.proof_points], ["Fresh case"])


class SectionQueriesSurviveRegenerationTests(ResearchCacheDurabilityTestBase):
    async def test_section_queries_survive_sections_1_3_regeneration(self) -> None:
        rfp_id = "rfp-section-queries"
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                sectionQueries={"section-1-cover": ["cover letter requirements"]},
                updatedAt="2026-08-05T00:00:00Z",
            )
        )
        prior = await repo.aget_research_cache(rfp_id)
        self.assertTrue(prior.section_queries)

        await repo.asave_research_cache(
            _sections_1_3_rebuild_payload(rfp_id, prior, "2026-08-05T01:00:00Z")
        )

        after = await repo.aget_research_cache(rfp_id)
        self.assertTrue(
            after.section_queries,
            "section_queries was wiped by a routine sections-1-3 regeneration",
        )
        self.assertEqual(
            after.section_queries["section-1-cover"], ["cover letter requirements"]
        )

    async def test_freshly_built_section_queries_still_overwrite_the_stored_ones(self) -> None:
        rfp_id = "rfp-section-queries-refresh"
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                sectionQueries={"section-1": ["stale query"]},
                updatedAt="2026-08-05T00:00:00Z",
            )
        )
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                sectionQueries={"section-1": ["fresh query"]},
                updatedAt="2026-08-05T02:00:00Z",
            )
        )
        reloaded = await repo.aget_research_cache(rfp_id)
        self.assertEqual(reloaded.section_queries["section-1"], ["fresh query"])


class LossLessonsSurviveRegenerationTests(ResearchCacheDurabilityTestBase):
    async def test_loss_lessons_survive_sections_1_3_regeneration(self) -> None:
        rfp_id = "rfp-loss-lessons"
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                lossLessons=[
                    LossLesson(pattern="Generic case studies", avoid="Name-dropping only")
                ],
                updatedAt="2026-08-05T00:00:00Z",
            )
        )
        prior = await repo.aget_research_cache(rfp_id)
        self.assertTrue(prior.loss_lessons)

        await repo.asave_research_cache(
            _sections_1_3_rebuild_payload(rfp_id, prior, "2026-08-05T01:00:00Z")
        )

        after = await repo.aget_research_cache(rfp_id)
        self.assertTrue(
            after.loss_lessons,
            "loss_lessons was wiped by a routine sections-1-3 regeneration",
        )
        self.assertEqual(after.loss_lessons[0].pattern, "Generic case studies")

    async def test_freshly_built_loss_lessons_still_overwrite_the_stored_ones(self) -> None:
        rfp_id = "rfp-loss-lessons-refresh"
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                lossLessons=[LossLesson(pattern="Stale", avoid="Stale avoid")],
                updatedAt="2026-08-05T00:00:00Z",
            )
        )
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                lossLessons=[LossLesson(pattern="Fresh", avoid="Fresh avoid")],
                updatedAt="2026-08-05T02:00:00Z",
            )
        )
        reloaded = await repo.aget_research_cache(rfp_id)
        self.assertEqual([l.pattern for l in reloaded.loss_lessons], ["Fresh"])


class EvidenceAllocationSurvivesRegenerationTests(ResearchCacheDurabilityTestBase):
    async def test_evidence_allocation_survives_sections_1_3_regeneration(self) -> None:
        rfp_id = "rfp-evidence-allocation"
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                evidenceAllocation={"case-study-1": ["section-4"]},
                updatedAt="2026-08-05T00:00:00Z",
            )
        )
        prior = await repo.aget_research_cache(rfp_id)
        self.assertIsNotNone(prior.evidence_allocation)

        await repo.asave_research_cache(
            _sections_1_3_rebuild_payload(rfp_id, prior, "2026-08-05T01:00:00Z")
        )

        after = await repo.aget_research_cache(rfp_id)
        self.assertIsNotNone(
            after.evidence_allocation,
            "evidence_allocation was wiped by a routine sections-1-3 regeneration",
        )
        self.assertEqual(after.evidence_allocation["case-study-1"], ["section-4"])

    async def test_a_freshly_built_evidence_allocation_still_overwrites_the_stored_one(
        self,
    ) -> None:
        rfp_id = "rfp-evidence-allocation-refresh"
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                evidenceAllocation={"case-study-1": ["stale"]},
                updatedAt="2026-08-05T00:00:00Z",
            )
        )
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                evidenceAllocation={"case-study-1": ["fresh"]},
                updatedAt="2026-08-05T02:00:00Z",
            )
        )
        reloaded = await repo.aget_research_cache(rfp_id)
        self.assertEqual(reloaded.evidence_allocation["case-study-1"], ["fresh"])


if __name__ == "__main__":
    unittest.main()
