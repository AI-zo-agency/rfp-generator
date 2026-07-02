"""Scan manuscript + compliance gaps into structured manual-fill flags for the UI."""

from __future__ import annotations

import re
from typing import Literal

from app.models.proposal import ManualFillFlag, ProposalDraft, ProposalResearchCache
from app.models.rfp import RfpRecord
from app.services.proposal_rfp_compliance import ComplianceGap, scan_rfp_compliance_gaps

MANUAL_FILL_TAG_RE = re.compile(r"\[MANUAL\s+FILL:[^\]]+\]", re.I)
VERIFY_TAG_RE = re.compile(r"\[VERIFY:\s*([^\]]+)\]", re.I)
PLACEHOLDER_TAG_RE = re.compile(r"\[(?:PLACEHOLDER|INSERT|TBD)[^\]]+\]", re.I)
GENERIC_VERIFY_RE = re.compile(r"\[VERIFY\]", re.I)
_FEIN_RE = re.compile(r"\b\d{2}-\d{7}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?:\(\d{3}\)\s*\d{3}[-.\s]?\d{4}|\d{3}[-.\s]\d{3}[-.\s]\d{4})",
    re.I,
)

_GAP_OWNER: dict[str, str] = {
    "insurance": "Sonja",
    "questionnaire": "Sonja",
    "budget": "Sonja",
    "workforce_data": "Ella",
    "references": "Ella",
    "requirement_coverage": "Sonja",
    "psa_acknowledgment": "Sonja",
}


def _owner_for_gap(gap: ComplianceGap) -> str:
    if gap.category == "references" and re.search(
        r"\bnew\s+jersey\b|\bNJ\b", gap.message, re.I
    ):
        return "Ella"
    return _GAP_OWNER.get(gap.category, "Sonja")


def _classify_tag(tag: str) -> Literal[
    "verify", "placeholder", "manual_fill", "compliance", "budget", "consistency", "other"
]:
    upper = tag.upper()
    if upper.startswith("[MANUAL FILL"):
        return "manual_fill"
    if upper.startswith("[VERIFY"):
        return "verify"
    if upper.startswith("[PLACEHOLDER") or upper.startswith("[INSERT") or upper.startswith("[TBD"):
        return "placeholder"
    return "other"


def _parse_owner_from_tag(tag: str) -> str | None:
    match = re.match(r"\[MANUAL\s+FILL:\s*([^—\-]+)", tag, re.I)
    if not match:
        return None
    name = match.group(1).strip()
    if name.lower().startswith("sonja"):
        return "Sonja"
    if name.lower().startswith("ella"):
        return "Ella"
    return name.split()[0] if name else None


def scan_tags_in_section(
    section_id: str,
    section_title: str,
    content: str,
    *,
    finalized: bool = False,
    kb_searched: bool = False,
) -> list[ManualFillFlag]:
    if not content.strip():
        return []

    flags: list[ManualFillFlag] = []
    patterns = (
        (MANUAL_FILL_TAG_RE, True),
        (VERIFY_TAG_RE, False),
        (PLACEHOLDER_TAG_RE, False),
        (GENERIC_VERIFY_RE, False),
    )
    for pattern, is_manual in patterns:
        for match in pattern.finditer(content):
            tag = match.group(0)
            flags.append(
                ManualFillFlag(
                    sectionId=section_id,
                    sectionTitle=section_title,
                    kind="manual_fill" if is_manual else _classify_tag(tag),
                    tag=tag,
                    highlightText=tag,
                    owner=_parse_owner_from_tag(tag),
                    finalized=finalized or is_manual,
                    kbSearched=kb_searched,
                )
            )
    return flags


def _owner_for_field(field: str) -> str:
    lower = field.casefold()
    if "reference" in lower and ("nj" in lower or "new jersey" in lower):
        return "Ella"
    if "reference" in lower:
        return "Ella"
    if any(k in lower for k in ("workforce", "diversity", "eeo", "female", "minority")):
        return "Ella"
    return "Sonja"


def convert_verify_tags_to_manual_fill(content: str) -> str:
    """Replace open VERIFY tags with owner-assigned MANUAL FILL handoff tags."""

    def repl(match: re.Match[str]) -> str:
        field = (match.group(1) or "confirm before submission").strip()
        owner = _owner_for_field(field)
        return f"[MANUAL FILL: {owner} — {field}]"

    content = VERIFY_TAG_RE.sub(repl, content)
    content = GENERIC_VERIFY_RE.sub("[MANUAL FILL: Sonja — confirm before submission]", content)
    return content


def apply_corpus_snippet_fills(
    draft: ProposalDraft,
    corpus: list,
) -> ProposalDraft:
    """Insert KB facts (FEIN, email, phone) into questionnaire sections when found in corpus."""
    from app.models.proposal import EvidenceItem

    items = [e for e in corpus if isinstance(e, EvidenceItem)]
    blob = "\n".join((e.excerpt or "")[:3000] for e in items[:100])
    if not blob.strip():
        return draft

    fein_match = _FEIN_RE.search(blob)
    fein = fein_match.group(0) if fein_match else None
    emails = _EMAIL_RE.findall(blob)
    email = next(
        (e for e in emails if "zo" in e.casefold() or "sonja" in e.casefold()),
        emails[0] if emails else None,
    )
    phones = _PHONE_RE.findall(blob)
    phone = phones[0] if phones else None

    updated_sections = []
    for section in draft.sections:
        content = section.content or ""
        title = (section.title or "").casefold()
        is_questionnaire = any(
            p in title
            for p in (
                "questionnaire",
                "vendor",
                "contractor",
                "offeror",
                "business entity",
                "administrative",
                "compliance and administrative",
            )
        )
        if is_questionnaire:
            if fein and fein not in content:
                content = VERIFY_TAG_RE.sub(
                    lambda m: fein
                    if any(k in m.group(0).casefold() for k in ("fein", "ein", "tax"))
                    else m.group(0),
                    content,
                )
                if fein not in content:
                    content = f"{content.rstrip()}\n\n**Federal EIN (FEIN):** {fein}"
            if email and email not in content:
                content = f"{content.rstrip()}\n\n**Primary business email:** {email}"
            if phone and phone not in content:
                content = f"{content.rstrip()}\n\n**Business phone:** {phone}"

        updated_sections.append(section.model_copy(update={"content": content}))

    now = draft.updated_at
    return draft.model_copy(update={"sections": updated_sections, "updated_at": now})


def apply_finalize_handoff_to_draft(
    draft: ProposalDraft,
    gaps: list[ComplianceGap],
) -> ProposalDraft:
    """Write MANUAL FILL handoff tags into the manuscript for gaps KB could not close."""
    from datetime import datetime, timezone

    gap_flags = gaps_to_manual_fill_flags(gaps, kb_searched=True, finalized=True)
    tags_by_section: dict[str, list[str]] = {}
    for gf in gap_flags:
        tags_by_section.setdefault(gf.section_id, []).append(gf.tag)

    updated_sections = []
    for section in draft.sections:
        content = convert_verify_tags_to_manual_fill(section.content or "")
        for tag in tags_by_section.get(section.id, []):
            if tag.casefold() not in content.casefold():
                content = f"{content.rstrip()}\n\n{tag}"
        updated_sections.append(section.model_copy(update={"content": content}))

    return draft.model_copy(
        update={
            "sections": updated_sections,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def gaps_to_manual_fill_flags(
    gaps: list[ComplianceGap],
    *,
    kb_searched: bool = True,
    finalized: bool = True,
) -> list[ManualFillFlag]:
    flags: list[ManualFillFlag] = []
    for gap in gaps:
        owner = _owner_for_gap(gap)
        field = (gap.rfp_requirement or gap.message)[:100].strip()
        tag = f"[MANUAL FILL: {owner} — {field}]"
        kind: Literal[
            "verify", "placeholder", "manual_fill", "compliance", "budget", "consistency", "other"
        ] = "compliance"
        if gap.category == "budget":
            kind = "budget"
        elif gap.category in ("references", "insurance", "questionnaire", "workforce_data"):
            kind = "compliance"

        flags.append(
            ManualFillFlag(
                sectionId=gap.section_id,
                sectionTitle=gap.section_title,
                kind=kind,
                tag=tag,
                highlightText=gap.excerpt[:120] if gap.excerpt else None,
                owner=owner,
                finalized=finalized,
                kbSearched=kb_searched,
            )
        )
    return flags


def _dedupe_flags(flags: list[ManualFillFlag]) -> list[ManualFillFlag]:
    seen: set[str] = set()
    out: list[ManualFillFlag] = []
    for flag in flags:
        key = f"{flag.section_id}::{flag.kind}::{flag.tag.casefold()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(flag)
    return out


def build_presubmit_manual_fill_flags(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
    kb_searched: bool = False,
    finalized: bool = False,
) -> list[ManualFillFlag]:
    """Combine in-manuscript tags with unresolved compliance gaps."""
    flags: list[ManualFillFlag] = []

    for section in draft.sections:
        flags.extend(
            scan_tags_in_section(
                section.id,
                section.title,
                section.content or "",
                finalized=finalized,
                kb_searched=kb_searched,
            )
        )

    remaining_gaps = scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)
    gap_flags = gaps_to_manual_fill_flags(
        remaining_gaps, kb_searched=kb_searched, finalized=finalized
    )

    section_blob: dict[str, str] = {
        s.id: (s.content or "").casefold() for s in draft.sections
    }
    for gf in gap_flags:
        blob = section_blob.get(gf.section_id, "")
        if gf.tag.casefold() in blob:
            continue
        if MANUAL_FILL_TAG_RE.search(blob):
            continue
        flags.append(gf)

    return _dedupe_flags(flags)


def summarize_manual_fill_flags(flags: list[ManualFillFlag]) -> str:
    if not flags:
        return (
            "No manual fill-ins — KB + final editor resolved all submission gaps, "
            "or run Finalize gaps to produce owner-assigned flags."
        )
    manual = sum(1 for f in flags if f.kind == "manual_fill")
    verify = sum(1 for f in flags if f.kind == "verify")
    placeholder = sum(1 for f in flags if f.kind == "placeholder")
    compliance = sum(1 for f in flags if f.kind == "compliance")
    budget = sum(1 for f in flags if f.kind == "budget")
    finalized = sum(1 for f in flags if f.finalized)
    parts: list[str] = []
    if finalized:
        parts.append(f"{finalized} finalized for Sonja/Ella")
    if manual:
        parts.append(f"{manual} MANUAL FILL")
    if verify:
        parts.append(f"{verify} VERIFY")
    if placeholder:
        parts.append(f"{placeholder} PLACEHOLDER")
    if compliance:
        parts.append(f"{compliance} compliance")
    if budget:
        parts.append(f"{budget} budget")
    return "; ".join(parts) + " — complete before submission."
