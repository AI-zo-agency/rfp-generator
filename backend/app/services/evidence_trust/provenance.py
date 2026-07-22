"""Filename / doc-type provenance for experience citations."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any


class ProvenanceKind(str, Enum):
    CASE_STUDY = "case_study"  # 03_CS
    WON = "won"  # 06_WON
    FINALIST = "finalist"  # 07_FIN
    LOST_FOIA = "lost_foia"  # 08
    COMPETITOR = "competitor"  # Resonance / competitor CI
    OTHER = "other"


def _label_from_hit(hit: dict[str, Any] | str) -> str:
    if isinstance(hit, str):
        return hit
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    return str(
        hit.get("customId")
        or metadata.get("fileName")
        or metadata.get("title")
        or hit.get("title")
        or hit.get("source")
        or hit.get("id")
        or ""
    )


def _body_from_hit(hit: dict[str, Any] | str) -> str:
    if isinstance(hit, str):
        return ""
    return str(
        hit.get("content")
        or hit.get("memory")
        or hit.get("chunk")
        or hit.get("text")
        or hit.get("excerpt")
        or hit.get("summary")
        or ""
    )


def classify_provenance(hit: dict[str, Any] | str) -> ProvenanceKind:
    label = _label_from_hit(hit).casefold()
    body = _body_from_hit(hit).casefold()
    blob = f"{label}\n{body[:2000]}"

    if "resonance" in blob:
        return ProvenanceKind.COMPETITOR
    if re.search(r"\b08[_-]?(?:lost|foia)?\b", label) or "lost_proposal" in label:
        return ProvenanceKind.LOST_FOIA
    if re.search(r"(?:^|[_/\s-])07[_-]?fin(?:[_/\s-]|$)", label) or "finalist_proposal" in label:
        return ProvenanceKind.FINALIST
    if re.search(r"(?:^|[_/\s-])06[_-]?won(?:[_/\s-]|$)", label) or "won_proposal" in label:
        return ProvenanceKind.WON
    if re.search(r"(?:^|[_/\s-])03[_-]?cs(?:[_/\s-]|$)", label) or "case_study" in label:
        return ProvenanceKind.CASE_STUDY
    return ProvenanceKind.OTHER

def is_win_eligible(hit: dict[str, Any] | str) -> bool:
    """True when hit may be cited as zö delivered / won experience."""
    kind = classify_provenance(hit)
    return kind in {ProvenanceKind.CASE_STUDY, ProvenanceKind.WON, ProvenanceKind.OTHER}


def provenance_block_reason(hit: dict[str, Any] | str) -> str | None:
    kind = classify_provenance(hit)
    if kind == ProvenanceKind.FINALIST:
        return "07_FIN finalist/loss — not usable as won experience"
    if kind == ProvenanceKind.LOST_FOIA:
        return "08 lost/FOIA — not usable as zö win proof"
    if kind == ProvenanceKind.COMPETITOR:
        return "competitor/Resonance content — not zö experience"
    return None
