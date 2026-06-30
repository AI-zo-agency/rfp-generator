"""Evidence corpus merge — excerpt text is immutable once stored."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.proposal import EvidenceItem

logger = logging.getLogger(__name__)


def next_evidence_id(corpus: list[EvidenceItem]) -> int:
    max_id = 0
    for item in corpus:
        match = re.match(r"E(\d+)$", item.id)
        if match:
            max_id = max(max_id, int(match.group(1)))
    return max_id + 1


def merge_hits_into_corpus(
    corpus: list[EvidenceItem],
    hits: list[dict[str, Any]],
    section_id: str,
    *,
    hit_key: Any,
    hit_label: Any,
    hit_excerpt: Any,
    excerpt_max_chars: int,
) -> list[EvidenceItem]:
    """
    Append new evidence or tag existing chunk_keys with section_id.
    Excerpt text for an existing chunk_key is never modified.
    """
    by_key = {item.chunk_key: item for item in corpus if item.chunk_key}
    counter = next_evidence_id(corpus)
    updated = list(corpus)

    for hit in hits:
        key = hit_key(hit)
        if key in by_key:
            existing = by_key[key]
            new_excerpt = hit_excerpt(hit, max_chars=excerpt_max_chars)
            if new_excerpt and new_excerpt != existing.excerpt:
                logger.warning(
                    "Evidence corpus: refusing to mutate excerpt for chunk_key=%s (immutable)",
                    key[:80],
                )
            if section_id not in existing.section_ids:
                merged = existing.model_copy(update={"section_ids": [*existing.section_ids, section_id]})
                by_key[key] = merged
                updated = [merged if item.id == existing.id else item for item in updated]
            continue

        eid = f"E{counter}"
        counter += 1
        item = EvidenceItem(
            id=eid,
            source=hit_label(hit),
            excerpt=hit_excerpt(hit, max_chars=excerpt_max_chars),
            sectionIds=[section_id],
            chunkKey=key,
        )
        by_key[key] = item
        updated.append(item)

    return updated
