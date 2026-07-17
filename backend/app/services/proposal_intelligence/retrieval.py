"""Intelligence-only Supermemory retrieval for Phase 2 planners.

Never retrieves writing evidence (case studies, bios, testimonials, etc.).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from app.services import supermemory

logger = logging.getLogger(__name__)

IntelligenceBucket = Literal[
    "won_patterns",
    "methodology",
    "pricing",
    "playbooks",
    "standards",
]

INTELLIGENCE_BUCKETS: tuple[str, ...] = (
    "won_patterns",
    "methodology",
    "pricing",
    "playbooks",
    "standards",
)

_WRITING_EVIDENCE_PATTERNS = (
    r"03_cs",
    r"04_bio",
    r"case[\s_-]?study",
    r"testimonial",
    r"reference[\s_-]?contact",
    r"portfolio",
    r"writing[\s_-]?sample",
    r"exec(utive)?[\s_-]?summary",
    r"marketing[\s_-]?copy",
)

_BUCKET_HINTS: dict[str, tuple[str, ...]] = {
    "won_patterns": ("06_won", "won proposal", "winning proposal"),
    "methodology": ("methodology", "delivery process", "project approach", "phases"),
    "pricing": ("00_guide_pricing", "07_fin", "rate card", "pricing guide", "hourly rate"),
    "playbooks": ("playbook", "risk playbook", "qa playbook", "communication", "training playbook"),
    "standards": ("qa standard", "accessibility", "wcag", "security process", "iso"),
}


def is_writing_evidence_source(name: str) -> bool:
    text = (name or "").strip().lower()
    if not text:
        return False
    return any(re.search(pattern, text) for pattern in _WRITING_EVIDENCE_PATTERNS)


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


def _hit_matches_bucket(label: str, bucket: IntelligenceBucket) -> bool:
    lower = label.lower()
    if is_writing_evidence_source(lower):
        return False
    hints = _BUCKET_HINTS.get(bucket, ())
    if not hints:
        return True
    return any(hint in lower for hint in hints) or bucket == "playbooks"


async def retrieve_intelligence(
    bucket: IntelligenceBucket,
    *,
    query: str,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Search KB for planning intelligence only; filter out writing evidence."""
    if not supermemory.is_configured():
        logger.info("Supermemory not configured — skipping intelligence retrieval (%s)", bucket)
        return []

    enriched = f"{query} {bucket}".strip()[:220]
    try:
        hits = await supermemory.search_documents(
            query=enriched,
            limit=limit * 2,
            include_full_docs=False,
            filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
        )
    except supermemory.SupermemoryError as exc:
        logger.warning("Intelligence retrieval failed (%s): %s", bucket, exc)
        return []

    results: list[dict[str, Any]] = []
    for hit in hits:
        if not supermemory.is_knowledge_base_hit(hit):
            continue
        label = _hit_label(hit)
        if is_writing_evidence_source(label):
            continue
        if bucket in ("won_patterns", "methodology", "pricing", "standards") and not _hit_matches_bucket(
            label, bucket
        ):
            # Soft filter: still allow if query was specific and label is unknown
            if not any(h in label.lower() for h in _BUCKET_HINTS.get(bucket, ())):
                content = str(hit.get("content") or hit.get("memory") or "")[:200].lower()
                if not any(h in content for h in _BUCKET_HINTS.get(bucket, ())):
                    continue
        excerpt = str(
            hit.get("content") or hit.get("memory") or hit.get("chunk") or hit.get("text") or ""
        ).strip()
        if not excerpt:
            continue
        results.append(
            {
                "source": label,
                "excerpt": excerpt[:2500],
                "bucket": bucket,
            }
        )
        if len(results) >= limit:
            break
    return results
