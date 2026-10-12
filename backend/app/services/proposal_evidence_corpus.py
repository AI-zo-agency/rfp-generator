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
        match = re.match(r"E(\d+)$", item.id or "", flags=re.I)
        if match:
            max_id = max(max_id, int(match.group(1)))
    return max_id + 1


def ensure_unique_evidence_ids(corpus: list[EvidenceItem]) -> list[EvidenceItem]:
    """One E# means one excerpt.

    The first row keeps its number. A later row that reused it is renumbered.
    The same chunk_key collapses into the first row; section ids are merged.
    Callers may stamp provisional numbers. This function is the only assignment
    that is allowed to persist.
    """
    used: set[str] = set()
    next_n = 1
    by_chunk: dict[str, EvidenceItem] = {}
    order: list[str] = []

    def _mint(preferred: str) -> str:
        nonlocal next_n
        pref = (preferred or "").strip().upper()
        if re.fullmatch(r"E\d+", pref) and pref not in used:
            used.add(pref)
            next_n = max(next_n, int(pref[1:]) + 1)
            return pref
        while f"E{next_n}" in used:
            next_n += 1
        eid = f"E{next_n}"
        used.add(eid)
        next_n += 1
        if pref and pref != eid:
            logger.warning(
                "evidence id renumbered from=%s to=%s",
                pref,
                eid,
            )
        return eid

    for index, item in enumerate(corpus):
        key = (item.chunk_key or "").strip()
        if key and key in by_chunk:
            existing = by_chunk[key]
            merged_ids = list(dict.fromkeys([*existing.section_ids, *item.section_ids]))
            by_chunk[key] = existing.model_copy(update={"section_ids": merged_ids})
            logger.info(
                "evidence chunk collapsed key=%s kept=%s dropped=%s",
                key[:80],
                existing.id,
                item.id,
            )
            continue
        eid = _mint(item.id)
        updated = item.model_copy(update={"id": eid})
        ident = key or f"row:{index}:{eid}"
        by_chunk[ident] = updated
        order.append(ident)
    return [by_chunk[key] for key in order]


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

    return ensure_unique_evidence_ids(updated)
