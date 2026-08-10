"""Phase 3 just-in-time retrieval from the Phase 2 retrievalPlan.

Uses the same ``retrieve_for_question`` path as ``kb_qa_loop`` (hybrid + document
search, agency proposal ranking, full-doc packing) — not bare ``search_documents``.

Applies Evidence Trust Gate (ClientList Public/Confirm, work-type, provenance)
before returning hits to section writers.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.proposal import EvidenceItem
from app.services import supermemory
from app.services.evidence_trust.gate import ClaimIntent, GateDecision, filter_evidence_hits
from app.services.evidence_trust.load_client_list import load_client_list_registry
from app.services.kb_rag_retrieve import (
    build_retrieval_question_from_entry,
    context_blocks_to_hits,
    retrieve_for_question,
)
from app.services.proposal_intelligence.schemas import RetrievalEntry

logger = logging.getLogger(__name__)

# Richer excerpts than legacy JIT (2k) — packed RAG already curated windows.
_JIT_EXCERPT_MAX = 6000


def _hit_label(hit: dict[str, Any]) -> str:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    return str(
        hit.get("customId")
        or metadata.get("fileName")
        or metadata.get("title")
        or hit.get("title")
        or hit.get("id")
        or "document"
    )


def _hit_excerpt(hit: dict[str, Any], *, max_chars: int = _JIT_EXCERPT_MAX) -> str:
    content = (
        hit.get("content")
        or hit.get("memory")
        or hit.get("chunk")
        or hit.get("text")
        or hit.get("summary")
        or hit.get("excerpt")
        or ""
    )
    text = str(content).strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}…"


def _slugify_claim(text: str, *, max_len: int = 60) -> str:
    tokens = re.findall(r"[a-z0-9]+", (text or "").casefold())
    return "_".join(tokens)[:max_len].strip("_")


def _infer_claim_from_entry(entry: RetrievalEntry) -> str:
    blob = " ".join(
        [
            entry.section_id or "",
            " ".join(entry.required_assets or []),
            " ".join(entry.queries or []),
        ]
    ).casefold()
    if any(t in blob for t in ("website", "web site", "web build", "site redesign")):
        return "website_build"
    if any(t in blob for t in ("leisure", "visitor economy", "destination brand")):
        return "tourism_leisure"
    if any(t in blob for t in ("meeting", "conference", "mci")):
        return "tourism_mci"
    if any(t in blob for t in ("brand", "identity", "rebrand")):
        return "brand"
    if any(t in blob for t in ("reference", "past performance", "case study", "experience")):
        return "experience"
    requirement_text = (
        (entry.required_assets[0] if entry.required_assets else "")
        or entry.why_needed
        or (entry.queries[0] if entry.queries else "")
        or ""
    )
    slug = _slugify_claim(requirement_text)
    return slug or "experience"


async def retrieve_for_section(
    entry: RetrievalEntry,
    *,
    rfp_client: str = "",
    start_index: int = 1,
    claim: str | None = None,
    section_title: str = "",
) -> list[EvidenceItem]:
    """Retrieve writing assets for one section using kb_qa_loop-quality RAG."""
    if not supermemory.is_configured():
        return []

    question = build_retrieval_question_from_entry(
        section_id=entry.section_id or "",
        section_title=section_title,
        required_assets=list(entry.required_assets or []),
        planner_queries=list(entry.queries or []),
        why_needed=entry.why_needed or "",
        rfp_client=rfp_client,
    )
    logger.info(
        "JIT RAG retrieve section=%s question=%r",
        entry.section_id,
        question[:120],
    )

    try:
        context, sources, queries_used = await retrieve_for_question(
            question,
            limit=8,
            max_chars=20_000,
            threshold=0.15,
            fallback_threshold=0.12,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "JIT RAG retrieve_for_question failed section=%s: %s",
            entry.section_id,
            exc,
        )
        context, sources, queries_used = "", [], []

    raw_hits = context_blocks_to_hits(context, sources)
    if not raw_hits:
        logger.info(
            "JIT RAG no hits section=%s queries=%s",
            entry.section_id,
            queries_used,
        )

    intent = ClaimIntent(
        slot=entry.section_id or "experience",
        claim=claim or _infer_claim_from_entry(entry),
        require_win_provenance=True,
        allow_unknown_clients=False,
    )

    try:
        registry = await load_client_list_registry()
    except Exception as exc:
        logger.warning("ClientList unavailable for JIT gate: %s", exc)
        registry = None

    gated_hits = raw_hits
    gap_tag: str | None = None
    if registry and registry.entries:
        result = filter_evidence_hits(raw_hits, registry=registry, intent=intent)
        gated_hits = result.allowed_hits
        gap_tag = result.gap_tag
        if result.decision == GateDecision.EMPTY:
            logger.info(
                "JIT gate emptied evidence for %s claim=%s rejected=%d",
                entry.section_id,
                intent.claim,
                len(result.rejected),
            )
        elif result.rejected:
            logger.info(
                "JIT gate filtered %d/%d hits for %s",
                len(result.rejected),
                len(raw_hits),
                entry.section_id,
            )

    items: list[EvidenceItem] = []
    counter = start_index
    for hit in gated_hits[:12]:
        label = _hit_label(hit)
        key = str(hit.get("id") or hit.get("customId") or label)
        excerpt = hit.get("excerpt") or _hit_excerpt(hit)
        if not excerpt:
            continue
        items.append(
            EvidenceItem(
                id=f"E{counter}",
                source=label,
                excerpt=str(excerpt),
                sectionIds=[entry.section_id],
                chunkKey=key,
            )
        )
        counter += 1

    if not items and gap_tag:
        items.append(
            EvidenceItem(
                id=f"E{counter}",
                source="evidence_trust_gate",
                excerpt=(
                    f"{gap_tag}\n\n"
                    "NO VERIFIED KB MATCH after ClientList + provenance filtering. "
                    "Do NOT invent clients, references, emails, or certifications. "
                    "Insert the VERIFY/FLAG tag above and continue other RFP requirements only."
                ),
                sectionIds=[entry.section_id],
                chunkKey="evidence-trust-gap",
            )
        )

    return items
