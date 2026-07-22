"""Phase 3 just-in-time retrieval from the Phase 2 retrievalPlan.

Applies Evidence Trust Gate (ClientList Public/Confirm, work-type, provenance)
before returning hits to section writers.
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.proposal import EvidenceItem
from app.services import supermemory
from app.services.evidence_trust.gate import ClaimIntent, GateDecision, filter_evidence_hits
from app.services.evidence_trust.load_client_list import load_client_list_registry
from app.services.proposal_intelligence.schemas import RetrievalEntry

logger = logging.getLogger(__name__)


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


def _hit_excerpt(hit: dict[str, Any], *, max_chars: int = 2000) -> str:
    content = (
        hit.get("content")
        or hit.get("memory")
        or hit.get("chunk")
        or hit.get("text")
        or hit.get("summary")
        or ""
    )
    text = str(content).strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}…"


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
    return "experience"


async def retrieve_for_section(
    entry: RetrievalEntry,
    *,
    rfp_client: str = "",
    start_index: int = 1,
    claim: str | None = None,
) -> list[EvidenceItem]:
    """Retrieve writing assets for one section using the planned queries."""
    if not supermemory.is_configured():
        return []

    raw_hits: list[dict[str, Any]] = []
    seen: set[str] = set()

    queries = list(entry.queries) or [
        f"zö agency {rfp_client} {' '.join(entry.required_assets)}".strip()
    ]
    for query in queries[:5]:
        try:
            hits = await supermemory.search_documents(
                query=query[:220],
                limit=6,
                include_full_docs=True,
                filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
            )
        except supermemory.SupermemoryError as exc:
            logger.warning("JIT retrieval failed for %s: %s", entry.section_id, exc)
            continue

        for hit in hits:
            if not supermemory.is_knowledge_base_hit(hit):
                continue
            label = _hit_label(hit)
            key = str(hit.get("id") or hit.get("customId") or label)
            if key in seen:
                continue
            seen.add(key)
            excerpt = _hit_excerpt(hit)
            if not excerpt:
                continue
            enriched = dict(hit)
            enriched["source"] = label
            enriched["excerpt"] = excerpt
            enriched["content"] = excerpt
            raw_hits.append(enriched)
            if len(raw_hits) >= 18:
                break
        if len(raw_hits) >= 18:
            break

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

    # Empty after best-effort: surface explicit VERIFY/FLAG as evidence so writers cannot invent.
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
