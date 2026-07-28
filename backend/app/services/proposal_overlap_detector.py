"""Deterministic section-overlap detector (W6 / T6.3).

Accounts for advisory prior-section digests (OQ-15): overlap is measured on
full section bodies in the manuscript, not on truncated digests.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]{2,}", re.IGNORECASE)


class OverlapFinding(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    section_a_id: str = Field(alias="sectionAId")
    section_b_id: str = Field(alias="sectionBId")
    jaccard: float
    shared_ngrams: int
    severity: str  # warning | critical
    message: str


def _tokens(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text or "")]


def _ngrams(tokens: list[str], n: int = 5) -> set[str]:
    if len(tokens) < n:
        return set()
    return {" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def jaccard_ngram_overlap(text_a: str, text_b: str, *, n: int = 5) -> tuple[float, int]:
    """Return (jaccard, shared_ngram_count) for word n-grams."""
    a = _ngrams(_tokens(text_a), n=n)
    b = _ngrams(_tokens(text_b), n=n)
    if not a or not b:
        return 0.0, 0
    shared = a & b
    union = a | b
    return (len(shared) / len(union), len(shared))


def detect_section_overlaps(
    sections: Iterable[tuple[str, str]],
    *,
    n: int = 5,
    warn_threshold: float = 0.18,
    critical_threshold: float = 0.28,
    min_shared: int = 8,
) -> list[OverlapFinding]:
    """Pairwise n-gram Jaccard on full section bodies.

    Thresholds are intentionally conservative for proposal prose; tune via FP
    review before enabling ``OVERLAP_GATES_BLOCK``.
    """
    items = [(sid, body) for sid, body in sections if (sid or "").strip() and (body or "").strip()]
    findings: list[OverlapFinding] = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            sid_a, body_a = items[i]
            sid_b, body_b = items[j]
            score, shared = jaccard_ngram_overlap(body_a, body_b, n=n)
            if shared < min_shared or score < warn_threshold:
                continue
            severity = "critical" if score >= critical_threshold else "warning"
            findings.append(
                OverlapFinding(
                    sectionAId=sid_a,
                    sectionBId=sid_b,
                    jaccard=round(score, 4),
                    shared_ngrams=shared,
                    severity=severity,
                    message=(
                        f"Sections {sid_a} and {sid_b} share {shared} {n}-grams "
                        f"(Jaccard={score:.3f}); likely duplicated narrative."
                    ),
                )
            )
    findings.sort(key=lambda f: (-f.jaccard, f.section_a_id, f.section_b_id))
    logger.info(
        "overlap_scan_complete pairs=%s findings=%s critical=%s",
        len(items) * (len(items) - 1) // 2 if len(items) > 1 else 0,
        len(findings),
        sum(1 for f in findings if f.severity == "critical"),
    )
    return findings
