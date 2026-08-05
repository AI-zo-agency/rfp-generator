"""Task 1b: six research-cache fields must survive a Sections 1-3 regeneration.

_generate_sections_1_3_inner (proposal_generator.py:1741, and identically the
_persist_sections_1_3_partial rebuild at :968) constructs a fresh
ProposalResearchCache from a hand-written whitelist of prior fields. Task 1
fixed requirement_ledger by adding it to merge_research_preserve_audit_fields.
The same hole still drops six more fields that are never in the rebuild
whitelist and never protected by the merge helper:

    pricing_rate_card, manuscript_locks, proof_points, section_queries,
    loss_lessons, evidence_allocation

This matters most for pricing_rate_card: run_fulfill_budget_scan
(proposal_fulfill_rfp_budget_kpi.py) reads research.pricing_rate_card to
build the rate_card used by the underbid-floor check shipped in
0264e60/0076a22. A missing/invalid card takes the `rate_card = None` branch,
which by design never halts — so wiping this field silently turns off the
10x-underbid protection on the next "Scan RFP" pass.

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
from app.models.pricing_rate_card import PricingRate, PricingRateCard
from app.models.proposal import (
    BudgetLineItem,
    LossLesson,
    ManuscriptLocks,
    ProofPoint,
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    RfpSectionMap,
)
from app.models.rfp import RfpRecord
from app.services import proposal_fulfill_rfp_budget_kpi as scan_mod
from app.services import proposal_repository as repo
from app.services.proposal_common import ProposalError


def _rate(rate_id: str, service: str, low: float, high: float) -> PricingRate:
    return PricingRate(
        rate_id=rate_id,
        service=service,
        tier="Average",
        unit="fixed",
        amount=round((low + high) / 2.0, 2),
        amount_low=low,
        amount_high=high,
        menu_id="",
        source_doc="00_Guide_Pricing",
        confidence=0.95,
        notes="",
    )


# Same verified live KB tier data used by tests/test_budget_underbid_floor.py.
RATE_CARD = PricingRateCard(
    rates=[
        _rate(
            "guide-1.1-average",
            "Stakeholder Interviews (Discovery & Research)",
            6000,
            8000,
        ),
        _rate("guide-2.1-average", "Strategic Plan Document Production", 6000, 9000),
        _rate("guide-3.1-average", "Implementation Roadmap", 12000, 18000),
        _rate(
            "guide-9.1-average",
            "Project Management & Administration (Short Projects)",
            7500,
            12000,
        ),
    ]
)


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


class PricingRateCardSurvivesRegenerationTests(ResearchCacheDurabilityTestBase):
    async def test_pricing_rate_card_survives_sections_1_3_regeneration(self) -> None:
        rfp_id = "rfp-rate-card"
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                rfpSections=[RfpSectionMap(id="sec-1", title="Attachments")],
                pricingRateCard=RATE_CARD.model_dump(by_alias=True),
                updatedAt="2026-08-05T00:00:00Z",
            )
        )
        prior = await repo.aget_research_cache(rfp_id)
        self.assertIsNotNone(prior.pricing_rate_card)

        await repo.asave_research_cache(
            _sections_1_3_rebuild_payload(rfp_id, prior, "2026-08-05T01:00:00Z")
        )

        after = await repo.aget_research_cache(rfp_id)
        self.assertIsNotNone(
            after.pricing_rate_card,
            "pricing_rate_card was wiped by a routine sections-1-3 regeneration",
        )
        self.assertEqual(
            [r["service"] for r in after.pricing_rate_card["rates"]],
            [r.service for r in RATE_CARD.rates],
        )

    async def test_a_freshly_built_rate_card_still_overwrites_the_stored_one(self) -> None:
        rfp_id = "rfp-rate-card-refresh"
        stale = PricingRateCard(rates=[_rate("stale-1", "Stale Service", 1, 2)])
        fresh = PricingRateCard(rates=[_rate("fresh-1", "Fresh Service", 3, 4)])
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                pricingRateCard=stale.model_dump(by_alias=True),
                updatedAt="2026-08-05T00:00:00Z",
            )
        )
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                pricingRateCard=fresh.model_dump(by_alias=True),
                updatedAt="2026-08-05T02:00:00Z",
            )
        )
        reloaded = await repo.aget_research_cache(rfp_id)
        self.assertEqual(
            [r["service"] for r in reloaded.pricing_rate_card["rates"]],
            ["Fresh Service"],
        )


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


class UnderbidFloorSurvivesSectionsRegenerationTests(ResearchCacheDurabilityTestBase):
    """The regression this task exists to close: a sections-1-3 regeneration must
    not silently disable the underbid-floor check on the next Scan RFP pass.

    Drives the REAL production path end to end:
      1. Phase 3.5 persists a pricing_rate_card on the research cache (real sqlite save).
      2. A sections-1-3 regeneration re-saves the cache from the hand-written whitelist
         (mirrors _generate_sections_1_3_inner exactly — no mocking of the rebuild).
      3. run_fulfill_budget_scan (the real "Scan RFP" entry point, not a stand-in) is
         driven with an underbid budget.
    Before the fix: pricing_rate_card is wiped in step 2, rate_card resolves to None
    in run_fulfill_budget_scan, and the check silently never fires (no exception).
    After the fix: the card survives, and the 10x-underbid halt still raises 422.
    """

    async def test_underbid_floor_still_fires_after_sections_1_3_regeneration(self) -> None:
        rfp_id = "rfp-underbid-after-regen"

        # Step 1: Phase 3.5 / KB extraction persists the pricing rate card.
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                pricingRateCard=RATE_CARD.model_dump(by_alias=True),
                updatedAt="2026-08-05T00:00:00Z",
            )
        )
        prior = await repo.aget_research_cache(rfp_id)
        self.assertIsNotNone(prior.pricing_rate_card)

        # Step 2: a routine "regenerate the company section" (Sections 1-3) call —
        # exact field whitelist from _generate_sections_1_3_inner.
        await repo.asave_research_cache(
            _sections_1_3_rebuild_payload(rfp_id, prior, "2026-08-05T01:00:00Z")
        )

        # Now attach the underbid budget the way a real Stage 3.5 run would, without
        # touching pricing_rate_card again — this is the "force_regenerate sections-1-3
        # after pricing already ran" ordering the defect report calls out.
        underbid_budget = ProposalBudget(
            rfpId=rfp_id,
            updatedAt="2026-08-05T01:30:00Z",
            lineItems=[
                BudgetLineItem(
                    id="L01",
                    category="Digital Marketing",
                    description="Discovery & stakeholder interviews",
                    extended=1000,
                ),
                BudgetLineItem(
                    id="L02",
                    category="Digital Marketing",
                    description="Strategic plan document production",
                    extended=1000,
                ),
                BudgetLineItem(
                    id="L03",
                    category="Digital Marketing",
                    description="Implementation roadmap",
                    extended=1000,
                ),
            ],
        )
        after_regen = await repo.aget_research_cache(rfp_id)
        research_for_scan = after_regen.model_copy(update={"budget": underbid_budget})

        rfp = RfpRecord(
            id=rfp_id,
            title="T",
            client="C",
            dueDate="2026-09-01",
            receivedDate="2026-08-01",
            lastActivity="2026-08-05",
            lastActivityNote="n",
        )
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[],
            updatedAt="2026-08-05T01:30:00Z",
        )

        with self.assertRaises(ProposalError) as ctx:
            await scan_mod.run_fulfill_budget_scan(
                rfp_id=rfp_id,
                rfp=rfp,
                draft=draft,
                research=research_for_scan,
                rfp_text="Some RFP text.",
                use_llm=False,
                skip_section_ids=set(),
            )
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("00_Guide_Pricing", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
