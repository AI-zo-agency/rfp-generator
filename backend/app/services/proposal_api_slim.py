"""Trim large research payloads for HTTP responses (full data stays in DB)."""

from __future__ import annotations

from app.models.proposal import EvidenceItem, ProposalResearchCache

_EXCERPT_API_MAX = 500


def slim_research_for_api(research: ProposalResearchCache) -> ProposalResearchCache:
    """Shorten evidence excerpts in API responses to avoid proxy timeouts."""
    corpus: list[EvidenceItem] = []
    for item in research.evidence_corpus:
        excerpt = item.excerpt or ""
        if len(excerpt) > _EXCERPT_API_MAX:
            excerpt = excerpt[:_EXCERPT_API_MAX] + "…"
        corpus.append(item.model_copy(update={"excerpt": excerpt}))
    return research.model_copy(update={"evidence_corpus": corpus})
