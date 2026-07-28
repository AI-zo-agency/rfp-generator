"""W6 — shared evidence corpus, allocation ledger, overlap detector."""

from __future__ import annotations

import unittest
from unittest import mock

from app.models.evidence_allocation import AllocationAssetClass
from app.models.proposal import (
    EvidenceItem,
    ProofPoint,
    ProposalDraft,
    ProposalSection,
    RfpSectionMap,
)
from app.models.rfp import RfpRecord
from app.services.evidence_allocator import (
    build_evidence_allocation_ledger,
    drafting_exclusion_contract,
)
from app.services.evidence_corpus_builder import (
    build_shared_evidence_corpus,
    corpus_has_section_hits,
    merge_corpus,
)
from app.services.proposal_consistency import scan_manuscript_consistency
from app.services.proposal_overlap_detector import (
    detect_section_overlaps,
    jaccard_ngram_overlap,
)
from app.services.proposal_pipeline_status import collect_manuscript_blockers


def _rfp() -> RfpRecord:
    return RfpRecord(
        id="r1",
        title="Test RFP",
        client="Acme County",
        dueDate="2026-12-01",
        receivedDate="2026-01-01",
        lastActivity="2026-01-01T00:00:00Z",
        lastActivityNote="test",
    )


_DUP_BLOCK = (
    "Investment Framing protects scope by clarifying reimbursable expenses and "
    "agency fees before media placement begins. Scope Protection requires written "
    "change orders for out-of-scope creative. Reimbursable Expenses cover travel, "
    "stock, and third-party production invoices with receipts attached for audit. "
    "Investment Framing also ensures the client sees media pass-through separately "
    "from agency revenue so evaluation panels can compare fee structures fairly."
)


class OverlapDetectorTests(unittest.TestCase):
    def test_identical_prose_high_jaccard(self) -> None:
        score, shared = jaccard_ngram_overlap(_DUP_BLOCK, _DUP_BLOCK)
        self.assertGreater(score, 0.9)
        self.assertGreaterEqual(shared, 8)

    def test_detect_flags_near_duplicate_sections(self) -> None:
        findings = detect_section_overlaps(
            [
                ("rfp-sec-13", _DUP_BLOCK),
                ("rfp-sec-14", _DUP_BLOCK + " Additional closing sentence for pad."),
                ("rfp-sec-18", "Unrelated methodology prose about discovery workshops."),
            ],
            warn_threshold=0.15,
            critical_threshold=0.25,
            min_shared=5,
        )
        pair = {(f.section_a_id, f.section_b_id) for f in findings}
        self.assertIn(("rfp-sec-13", "rfp-sec-14"), pair)
        self.assertTrue(any(f.severity == "critical" for f in findings))

    def test_distinct_sections_no_finding(self) -> None:
        findings = detect_section_overlaps(
            [
                (
                    "a",
                    "Discovery workshops establish stakeholder alignment and research plans.",
                ),
                (
                    "b",
                    "Media buying follows transparent pass-through invoices with receipts.",
                ),
            ]
        )
        self.assertEqual(findings, [])

    def test_consistency_scan_emits_duplication_category(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(id="s13", title="Budget Narrative", content=_DUP_BLOCK),
                ProposalSection(
                    id="s14",
                    title="Fees",
                    content=_DUP_BLOCK + " Extra pad words for length only.",
                ),
            ],
        )
        issues = scan_manuscript_consistency(draft=draft, research=None, rfp=_rfp())
        dup = [i for i in issues if i.category == "duplication"]
        self.assertTrue(dup)
        self.assertTrue(any("[T6:overlap]" in i.message for i in dup))

    def test_overlap_gates_block_flag_off_by_default(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(id="s1", title="A", content=_DUP_BLOCK),
                ProposalSection(id="s2", title="B", content=_DUP_BLOCK),
            ],
        )
        with mock.patch("app.services.proposal_pipeline_status.settings") as settings:
            settings.t1_gates_block = False
            settings.consistency_criticals_block = False
            settings.overlap_gates_block = False
            blockers = collect_manuscript_blockers(
                draft=draft,
                research=None,
                rfp=_rfp(),
                require_budget=False,
            )
        self.assertFalse(any("Overlap critical" in b for b in blockers))

    def test_overlap_gates_block_when_enabled(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(id="s1", title="A", content=_DUP_BLOCK),
                ProposalSection(id="s2", title="B", content=_DUP_BLOCK),
            ],
        )
        with mock.patch("app.services.proposal_pipeline_status.settings") as settings:
            settings.t1_gates_block = False
            settings.consistency_criticals_block = False
            settings.overlap_gates_block = True
            blockers = collect_manuscript_blockers(
                draft=draft,
                research=None,
                rfp=_rfp(),
                require_budget=False,
            )
        self.assertTrue(any("Overlap critical" in b for b in blockers))


class AllocationLedgerTests(unittest.TestCase):
    def test_first_touch_owner_and_reference_only(self) -> None:
        sections = [
            RfpSectionMap(id="sec-a", title="A", requirements=["x"], retrievalFocus=[]),
            RfpSectionMap(id="sec-b", title="B", requirements=["y"], retrievalFocus=[]),
        ]
        pp = ProofPoint(
            requirement="Show tourism ROI",
            caseStudy="Visit Oregon campaign lifted overnight stays 18%",
            narrativeHook="place branding proof",
            sectionIds=["sec-b", "sec-a"],
        )
        ledger = build_evidence_allocation_ledger(
            proof_points=[pp],
            evidence_corpus=[],
            rfp_sections=sections,
        )
        self.assertEqual(len(ledger.entries), 1)
        entry = ledger.entries[0]
        self.assertEqual(entry.owner_section_id, "sec-a")
        self.assertIn("sec-b", entry.reference_only_section_ids)
        self.assertEqual(entry.asset_class, AllocationAssetClass.HIGH_RISK_NUMERIC_CLAIM)

        owner_contract = drafting_exclusion_contract(ledger, section_id="sec-a")
        self.assertEqual(owner_contract, "")
        other = drafting_exclusion_contract(ledger, section_id="sec-b")
        self.assertIn("REFERENCE ONLY", other)
        self.assertIn(entry.asset_id, other)

    def test_boilerplate_class_from_corpus(self) -> None:
        sections = [
            RfpSectionMap(id="s1", title="Who", requirements=[], retrievalFocus=[]),
            RfpSectionMap(id="s2", title="Approach", requirements=[], retrievalFocus=[]),
        ]
        item = EvidenceItem(
            id="e1",
            source="kb",
            excerpt="About Zo: we are a place-branding agency founded in Portland.",
            sectionIds=["s1", "s2"],
            chunkKey="e1",
        )
        ledger = build_evidence_allocation_ledger(
            proof_points=[],
            evidence_corpus=[item],
            rfp_sections=sections,
        )
        self.assertEqual(ledger.entries[0].asset_class, AllocationAssetClass.BOILERPLATE)
        self.assertEqual(ledger.entries[0].owner_section_id, "s1")


class SharedCorpusBuilderTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_merges_and_dedupes_by_chunk_key(self) -> None:
        from app.services.proposal_intelligence.schemas import (
            ProposalExecutionPlan,
            RetrievalEntry,
            RetrievalPlan,
            WritingIntelligence,
        )

        plan = ProposalExecutionPlan(
            writing=WritingIntelligence(
                retrievalPlan=RetrievalPlan(
                    entries=[
                        RetrievalEntry(
                            sectionId="sec-a",
                            queries=["q1"],
                            whyNeeded="x",
                        ),
                        RetrievalEntry(
                            sectionId="sec-b",
                            queries=["q2"],
                            whyNeeded="y",
                        ),
                    ]
                )
            )
        )

        async def fake_retrieve(entry, *, rfp_client="", start_index=1):
            return [
                EvidenceItem(
                    id=f"E{start_index}",
                    source="kb",
                    excerpt=f"shared excerpt for {entry.section_id}",
                    sectionIds=[entry.section_id],
                    chunkKey="same-chunk",
                )
            ]

        with mock.patch(
            "app.services.evidence_corpus_builder.retrieve_for_section",
            side_effect=fake_retrieve,
        ):
            corpus = await build_shared_evidence_corpus(plan=plan, rfp_client="Acme")

        self.assertEqual(len(corpus), 1)
        self.assertEqual(set(corpus[0].section_ids), {"sec-a", "sec-b"})
        self.assertTrue(corpus_has_section_hits(corpus, "sec-a"))
        self.assertTrue(corpus_has_section_hits(corpus, "sec-b"))

    def test_merge_corpus_preserves_phase2_base(self) -> None:
        base = [
            EvidenceItem(
                id="E1",
                source="kb",
                excerpt="base",
                sectionIds=["a"],
                chunkKey="k1",
            )
        ]
        jut = [
            EvidenceItem(
                id="E2",
                source="kb",
                excerpt="jit",
                sectionIds=["b"],
                chunkKey="k2",
            ),
            EvidenceItem(
                id="E1b",
                source="kb",
                excerpt="dup",
                sectionIds=["c"],
                chunkKey="k1",
            ),
        ]
        merged = merge_corpus(base, jut)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0].id, "E1")


class JitFallbackFlagTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_hits_skip_jit(self) -> None:
        from app.services.proposal_drafting_graph import _ensure_jit_evidence

        state = {
            "evidence_corpus": [
                {
                    "id": "E1",
                    "source": "kb",
                    "excerpt": "tagged evidence body",
                    "sectionIds": ["sec-1"],
                    "chunkKey": "c1",
                }
            ],
            "rfp_sections": [{"id": "sec-1", "title": "Approach"}],
            "execution_plan": None,
            "rfp_client": "Acme",
        }
        with mock.patch("app.core.config.settings") as cfg, mock.patch(
            "app.services.proposal_intelligence.jit_retrieval.retrieve_for_section",
            new_callable=mock.AsyncMock,
        ) as retrieve:
            cfg.jit_retrieval_on_miss = True
            hits = await _ensure_jit_evidence(state, "sec-1")  # type: ignore[arg-type]

        retrieve.assert_not_called()
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["id"], "E1")

    async def test_miss_without_jit_flag_does_not_retrieve(self) -> None:
        from app.services.proposal_drafting_graph import _ensure_jit_evidence

        state = {
            "evidence_corpus": [
                {
                    "id": "E1",
                    "source": "kb",
                    "excerpt": "other section only",
                    "sectionIds": ["sec-other"],
                    "chunkKey": "c1",
                }
            ],
            "rfp_sections": [{"id": "sec-1", "title": "Approach"}],
            "execution_plan": {
                "writing": {
                    "retrievalPlan": {
                        "entries": [
                            {
                                "sectionId": "sec-1",
                                "queries": ["q"],
                                "priority": "required",
                                "requiredAssets": [],
                                "expectedSources": [],
                                "whyNeeded": "x",
                            }
                        ]
                    }
                }
            },
            "rfp_client": "Acme",
        }
        with mock.patch("app.core.config.settings") as cfg, mock.patch(
            "app.services.proposal_intelligence.jit_retrieval.retrieve_for_section",
            new_callable=mock.AsyncMock,
        ) as retrieve:
            cfg.jit_retrieval_on_miss = False
            hits = await _ensure_jit_evidence(state, "sec-1")  # type: ignore[arg-type]
        retrieve.assert_not_called()
        self.assertEqual(len(hits), 1)  # fallback to corpus[:12]


if __name__ == "__main__":
    unittest.main()
