"""Build a shared Phase 2 evidence corpus for drafting (W6 / T6.1)."""

from __future__ import annotations

import logging

from app.models.proposal import EvidenceItem, ProofPoint
from app.services.proposal_intelligence.jit_retrieval import retrieve_for_section
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan

logger = logging.getLogger(__name__)


async def build_shared_evidence_corpus(
    *,
    plan: ProposalExecutionPlan,
    rfp_client: str = "",
    proof_points: list[ProofPoint] | None = None,
    max_sections: int = 40,
) -> list[EvidenceItem]:
    """Retrieve writing assets once per retrieval-plan entry and merge + dedupe.

    Replaces the historical ``evidenceCorpus=[]`` hard rule so Phase 3 drafts from
    a shared base; JIT remains a miss-path behind ``jit_retrieval_on_miss``.
    """
    entries = list(plan.writing.retrieval_plan.entries)[:max_sections]
    title_by_id = {
        p.section_id: p.title
        for p in (plan.writing.section_plans.plans or [])
        if p.section_id
    }
    by_key: dict[str, EvidenceItem] = {}
    start_index = 1

    for entry in entries:
        try:
            items = await retrieve_for_section(
                entry,
                rfp_client=rfp_client,
                start_index=start_index,
                section_title=title_by_id.get(entry.section_id, ""),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "phase2_corpus_retrieve_failed section=%s err=%s",
                entry.section_id,
                str(exc)[:200],
            )
            continue
        for item in items:
            key = (item.chunk_key or item.id or "").strip() or f"{item.source}:{item.excerpt[:80]}"
            existing = by_key.get(key)
            if existing is not None:
                merged_ids = list(
                    dict.fromkeys([*existing.section_ids, *item.section_ids])
                )
                by_key[key] = existing.model_copy(update={"section_ids": merged_ids})
                continue
            by_key[key] = item
            start_index += 1

    corpus = list(by_key.values())
    seen = set(by_key.keys())

    # Proof-point stubs as lightweight evidence when retrieval returned nothing useful.
    for index, pp in enumerate(proof_points or [], start=1):
        stub_id = f"pp-{index}"
        if stub_id in seen:
            continue
        excerpt = " | ".join(
            part for part in (pp.case_study, pp.narrative_hook, pp.requirement) if part
        ).strip()
        if not excerpt:
            continue
        seen.add(stub_id)
        corpus.append(
            EvidenceItem(
                id=stub_id,
                source=pp.kb_source or "proof_point",
                excerpt=excerpt[:2000],
                sectionIds=list(pp.section_ids or []),
                chunkKey=stub_id,
            )
        )

    logger.info(
        "phase2_shared_corpus_built entries=%s items=%s proof_stubs=%s rfp_client=%s",
        len(entries),
        len(corpus),
        len(proof_points or []),
        rfp_client[:80],
    )
    return corpus


def corpus_has_section_hits(corpus: list[EvidenceItem], section_id: str) -> bool:
    sid = (section_id or "").strip()
    if not sid:
        return False
    for item in corpus:
        if sid in (item.section_ids or []) and (item.excerpt or "").strip():
            return True
    return False


def merge_corpus(
    existing: list[EvidenceItem],
    new_items: list[EvidenceItem],
) -> list[EvidenceItem]:
    """Append new items without dropping the shared Phase 2 base."""
    out = list(existing)
    seen = {
        (i.chunk_key or i.id or "").strip() or f"{i.source}:{i.excerpt[:80]}" for i in out
    }
    for item in new_items:
        key = (item.chunk_key or item.id or "").strip() or f"{item.source}:{item.excerpt[:80]}"
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
