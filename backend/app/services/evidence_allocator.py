"""Assign exclusive ownership of reusable evidence assets (W6 / T6.2)."""

from __future__ import annotations

import hashlib
import logging
import re

from app.models.evidence_allocation import (
    AllocationAssetClass,
    AllocationEntry,
    EvidenceAllocationLedger,
)
from app.models.proposal import EvidenceItem, ProofPoint, RfpSectionMap

logger = logging.getLogger(__name__)

_LEDGER_VERSION = "evidence-allocation-v1"

_BOILERPLATE_MARKERS = (
    "about zo",
    "our mission",
    "founded in",
    "we are a",
    "company overview",
    "who we are",
)

_HIGH_RISK_NUMERIC = re.compile(
    r"(?:\$[\d,]+(?:\.\d+)?|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|\b\d+(?:\.\d+)?%|"
    r"\b\d+\+?\s*(?:years?|fte|staff|employees?)\b)",
    re.IGNORECASE,
)


def _fingerprint(text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _looks_boilerplate(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in _BOILERPLATE_MARKERS)


def build_evidence_allocation_ledger(
    *,
    proof_points: list[ProofPoint],
    evidence_corpus: list[EvidenceItem],
    rfp_sections: list[RfpSectionMap],
) -> EvidenceAllocationLedger:
    """First-touch ownership: earliest outline section that lists the asset wins.

    Other sections that share the same fingerprint may reference but must not
    re-narrate (enforced via drafting contracts + overlap scan).
    """
    section_order = {s.id: i for i, s in enumerate(rfp_sections)}
    entries: list[AllocationEntry] = []
    seen_fp: set[str] = set()

    def _owner_among(section_ids: list[str], fallback: str) -> str:
        ranked = sorted(
            (sid for sid in section_ids if sid in section_order),
            key=lambda sid: section_order[sid],
        )
        return ranked[0] if ranked else (fallback or (rfp_sections[0].id if rfp_sections else "unknown"))

    for index, pp in enumerate(proof_points, start=1):
        text = " | ".join(
            part for part in (pp.case_study, pp.narrative_hook, pp.requirement) if part
        )
        fp = _fingerprint(text)
        if not fp or fp in seen_fp:
            continue
        seen_fp.add(fp)
        owner = _owner_among(list(pp.section_ids or []), "")
        others = [sid for sid in (pp.section_ids or []) if sid != owner]
        if _HIGH_RISK_NUMERIC.search(text):
            asset_class = AllocationAssetClass.HIGH_RISK_NUMERIC_CLAIM
        elif (pp.case_study or "").strip():
            asset_class = AllocationAssetClass.CASE_STUDY
        else:
            asset_class = AllocationAssetClass.PROOF_POINT
        entries.append(
            AllocationEntry(
                assetId=f"pp-{index}",
                assetClass=asset_class,
                ownerSectionId=owner,
                referenceOnlySectionIds=others,
                excludedSectionIds=[],
                rationale="first-touch proof point ownership",
                fingerprint=fp,
            )
        )

    for item in evidence_corpus:
        text = item.excerpt or ""
        fp = _fingerprint(text)
        if not fp or fp in seen_fp:
            continue
        seen_fp.add(fp)
        owner = _owner_among(list(item.section_ids or []), "")
        others = [sid for sid in (item.section_ids or []) if sid != owner]
        if _looks_boilerplate(text):
            asset_class = AllocationAssetClass.BOILERPLATE
            # Boilerplate: owner may use full narrative; others get reference-only.
            rationale = "boilerplate — exclusive narrative ownership"
        elif _HIGH_RISK_NUMERIC.search(text):
            asset_class = AllocationAssetClass.HIGH_RISK_NUMERIC_CLAIM
            rationale = "high-risk numeric claim — exclusive ownership"
        else:
            asset_class = AllocationAssetClass.PROOF_POINT
            rationale = "shared corpus proof — first-touch ownership"
        entries.append(
            AllocationEntry(
                assetId=item.id or item.chunk_key or fp,
                assetClass=asset_class,
                ownerSectionId=owner,
                referenceOnlySectionIds=others,
                excludedSectionIds=[],
                rationale=rationale,
                fingerprint=fp,
            )
        )

    ledger = EvidenceAllocationLedger(version=_LEDGER_VERSION, entries=entries)
    logger.info(
        "evidence_allocation_built entries=%s classes=%s",
        len(entries),
        {c.value: sum(1 for e in entries if e.asset_class == c) for c in AllocationAssetClass},
    )
    return ledger


def drafting_exclusion_contract(
    ledger: EvidenceAllocationLedger | None,
    *,
    section_id: str,
) -> str:
    """Prompt fragment: assets this section must not re-narrate."""
    if not ledger or not section_id:
        return ""
    lines: list[str] = []
    for entry in ledger.entries:
        if entry.owner_section_id == section_id:
            continue
        if section_id in entry.excluded_section_ids:
            lines.append(
                f"- FORBIDDEN: do not use asset {entry.asset_id} ({entry.asset_class.value})"
            )
        elif section_id in entry.reference_only_section_ids:
            lines.append(
                f"- REFERENCE ONLY: asset {entry.asset_id} ({entry.asset_class.value}) — "
                f"cite briefly; do not re-tell the full narrative (owned by {entry.owner_section_id})"
            )
        elif entry.asset_class in (
            AllocationAssetClass.CASE_STUDY,
            AllocationAssetClass.BOILERPLATE,
            AllocationAssetClass.HIGH_RISK_NUMERIC_CLAIM,
        ):
            # Default: non-owners should not expand exclusive classes.
            lines.append(
                f"- DO NOT RE-NARRATE: {entry.asset_id} ({entry.asset_class.value}) "
                f"owned by {entry.owner_section_id}"
            )
    if not lines:
        return ""
    return (
        "EVIDENCE ALLOCATION CONTRACT (mandatory):\n"
        + "\n".join(lines[:40])
        + "\nIf you need a claimed fact owned elsewhere, point to that section instead of repeating."
    )
