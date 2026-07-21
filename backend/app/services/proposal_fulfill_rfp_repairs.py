"""Targeted manuscript repairs during Scan RFP — KPI spine, roster names, human flags."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.proposal import ProposalDraft, ProposalSection

from app.services.proposal_fulfill_kpi_fix import (
    apply_contractor_kpi_text_fixes,
    content_uses_agency_kpi_framework,
)
_AGENCY_KPI_PHRASES = (
    "four kpi",
    "four kpis",
    "four headline kpi",
    "four hta kpi",
    "four key performance",
    "resident sentiment",
    "visitor satisfaction",
    "visitor satisfaction survey",
    "average daily visitor spending",
    "total visitor expenditures trend",
    "direct line to one of the four",
    "tied back to the four kpi",
    "against the four hta kpi",
)

_CONTRACTOR_KPI_PHRASES = (
    "total visitor arrivals",
    "visitor arrivals",
    "average islands visited",
    "islands visited per person",
    "total visitor expenditures",  # also agency — pair with growth targets
    "+3.0%",
    "+4.6%",
    "+0.8%",
)

_ROSTER_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bTed Anderson\b"), "Todd Anderson"),
    (re.compile(r"\bTed brings\b"), "Todd brings"),
    (re.compile(r"\bBen Edwards\b"), "[REMOVE: verify roster — not on approved team]"),
    (re.compile(r"\bErica Schultz\b"), "[REMOVE: verify roster — not on approved team]"),
)

_OCEANIA_NO_OFFICE = re.compile(
    r"no physical oceania office|no physical office in oceania|delivered through our bend",
    re.I,
)
_OCEANIA_CLAIMS_PRESENCE = re.compile(
    r"on-the-ground presence in sydney|office in sydney|auckland operating window|"
    r"honolulu office|satellite office",
    re.I,
)

_ACTIVITY_MEASURE_HEADING_RE = re.compile(
    r"(?im)^#{1,4}\s*(?:g\.\s*)?activity\s+measures?(?:\s+methodology)?\s*$"
)
_ACTIVITY_MEASURE_TABLE_HINT_RE = re.compile(
    r"(?i)activity\s+measure|"
    r"\|\s*measure\s*\||"
    r"total\s+visitor\s+arrivals|"
    r"average\s+islands\s+visited"
)
_BMP_SECTION_TITLE_RE = re.compile(
    r"\bbmp\b|brand\s+management\s+plan|exhibit\s*a",
    re.I,
)


def section_blob(section: "ProposalSection") -> str:
    return f"{section.title}\n{section.content or ''}"


def sections_with_wrong_kpi_framework(draft: "ProposalDraft") -> list[str]:
    """All section ids that still use agency/four-KPI language instead of contractor KPIs."""
    ids: list[str] = []
    for section in draft.sections:
        if not (section.content or "").strip():
            continue
        blob = section_blob(section).casefold()
        if any(p in blob for p in _AGENCY_KPI_PHRASES):
            ids.append(section.id)
            continue
        if re.search(r"\bfour\b.{0,56}\bkpi", blob):
            ids.append(section.id)
        elif content_uses_agency_kpi_framework(section.content or ""):
            ids.append(section.id)
    return ids


def run_global_contractor_kpi_fix(
    draft: "ProposalDraft",
    *,
    skip_section_ids: set[str],
) -> tuple["ProposalDraft", list[str]]:
    """Deterministic KPI spine — all sections except preserved bios/case studies."""
    from datetime import datetime, timezone

    from app.models.proposal import ProposalDraft

    logs: list[str] = []
    sections = list(draft.sections)
    changed = False
    for idx, section in enumerate(sections):
        if section.id in skip_section_ids:
            continue
        body = section.content or ""
        if not body.strip():
            continue
        fixed, fix_logs = apply_contractor_kpi_text_fixes(body)
        if fixed != body:
            sections[idx] = section.model_copy(update={"content": fixed})
            logs.extend(fix_logs)
            changed = True

    if not changed:
        return draft, logs
    now = datetime.now(timezone.utc).isoformat()
    return (
        draft.model_copy(update={"sections": sections, "updated_at": now}),
        logs,
    )


def apply_deterministic_roster_fixes(content: str) -> tuple[str, list[str]]:
    logs: list[str] = []
    out = content or ""
    for pattern, repl in _ROSTER_FIXES:
        if pattern.search(out):
            out = pattern.sub(repl, out)
            logs.append(f"Roster fix: {pattern.pattern} → {repl[:40]}")
    return out, logs


def scan_oceania_office_contradiction(draft: "ProposalDraft") -> str | None:
    manuscript = "\n\n".join(
        section_blob(s) for s in draft.sections if (s.content or "").strip()
    )
    if not manuscript.strip():
        return None
    has_denial = bool(_OCEANIA_NO_OFFICE.search(manuscript))
    has_claim = bool(_OCEANIA_CLAIMS_PRESENCE.search(manuscript))
    if has_denial and has_claim:
        return (
            "Oceania office: draft both denies a physical Oceania office and claims "
            "Sydney/Auckland on-the-ground presence. RFP Section 3.1.3.4.a.i requires "
            "an Oceania office — align to one truthful story (lease, partner hub, or "
            "honest gap + plan to establish)."
        )
    return None


def scan_empty_qualifications_stub(draft: "ProposalDraft") -> str | None:
    for section in draft.sections:
        title_cf = (section.title or "").casefold()
        blob = (section.content or "").casefold()
        if not (section.content or "").strip():
            continue
        qual = (
            "qualification" in title_cf
            or "offeror qualification" in title_cf
            or ("experience" in title_cf and "reference" in title_cf)
        )
        from app.services.proposal_fulfill_fabrication_guard import (
            portfolio_client_names,
            section_has_invented_qual_content,
        )

        portfolio = portfolio_client_names(draft)
        invented = section_has_invented_qual_content(section.content or "", portfolio)
        stub = len(blob) < 400 and (
            "[verify:" in blob
            or "insufficient evidence" in blob
            or "manual fill" in blob
        )
        if qual and (stub or invented):
            return (
                f"Section “{section.title}” is still a placeholder — RFP Criteria #1/#3 "
                "need creative examples, Oceania/Hawaii case studies, and references. "
                "Generate or paste from KB before submit."
            )
    return None


def _extract_activity_measures_block(content: str) -> tuple[str, str] | None:
    """Return (block, remainder) when a ## Activity Measures… section exists with body."""
    text = content or ""
    match = _ACTIVITY_MEASURE_HEADING_RE.search(text)
    if not match:
        return None
    start = match.start()
    rest = text[match.end() :]
    next_h = re.search(r"(?m)^#{1,3}\s+\S", rest)
    end = match.end() + (next_h.start() if next_h else len(rest))
    block = text[start:end].strip()
    # Require real rows — header-only / truncated G is not worth moving.
    if block.count("|") < 6 and "total visitor" not in block.casefold():
        return None
    remainder = (text[:start] + text[end:]).strip()
    remainder = re.sub(r"\n{3,}", "\n\n", remainder).strip()
    return block, remainder


def relocate_misplaced_activity_measures(
    draft: "ProposalDraft",
    *,
    skip_section_ids: set[str],
) -> tuple["ProposalDraft", list[str]]:
    """Move a populated Activity Measures block into the BMP section (Exhibit A §G)."""
    from datetime import datetime, timezone

    logs: list[str] = []
    sections = list(draft.sections)

    bmp_idx: int | None = None
    for i, section in enumerate(sections):
        if section.id in skip_section_ids:
            continue
        if _BMP_SECTION_TITLE_RE.search(section.title or ""):
            bmp_idx = i
            break
    if bmp_idx is None:
        return draft, logs

    bmp = sections[bmp_idx]
    bmp_body = bmp.content or ""
    existing = _extract_activity_measures_block(bmp_body)
    if existing and existing[0].count("|") >= 6:
        return draft, logs

    donor_idx: int | None = None
    donor_block = ""
    donor_remainder = ""
    for i, section in enumerate(sections):
        if i == bmp_idx or section.id in skip_section_ids:
            continue
        title_cf = (section.title or "").casefold()
        if _BMP_SECTION_TITLE_RE.search(title_cf):
            continue
        extracted = _extract_activity_measures_block(section.content or "")
        if not extracted:
            body = section.content or ""
            if (
                _ACTIVITY_MEASURE_TABLE_HINT_RE.search(body)
                and "oceania" in title_cf
                and body.count("|") >= 8
            ):
                m = re.search(r"(?im)^#{1,4}\s*.*activity\s+measure", body)
                if not m:
                    continue
                start = m.start()
                rest = body[m.end() :]
                next_h = re.search(r"(?m)^#{1,2}\s+\S", rest)
                end = m.end() + (next_h.start() if next_h else len(rest))
                block = body[start:end].strip()
                if block.count("|") < 6:
                    continue
                extracted = (block, (body[:start] + body[end:]).strip())
            else:
                continue
        block, remainder = extracted
        if block.count("|") < 6:
            continue
        donor_idx = i
        donor_block = block
        donor_remainder = remainder
        break

    if donor_idx is None or not donor_block:
        return draft, logs

    if not re.match(r"(?im)^#{1,4}\s*g\.\s*activity", donor_block):
        donor_block = re.sub(
            r"(?im)^#{1,4}\s*(?:g\.\s*)?activity\s+measures?(?:\s+methodology)?\s*$",
            "## G. Activity Measures Methodology",
            donor_block,
            count=1,
        )
        if not donor_block.lstrip().startswith("#"):
            donor_block = "## G. Activity Measures Methodology\n\n" + donor_block

    bmp_cleaned = bmp_body
    stub = _extract_activity_measures_block(bmp_cleaned)
    if stub and stub[0].count("|") < 6:
        bmp_cleaned = stub[1]

    new_bmp = (bmp_cleaned.rstrip() + "\n\n" + donor_block.strip()).strip()
    donor_title = sections[donor_idx].title
    sections[bmp_idx] = bmp.model_copy(update={"content": new_bmp, "status": "generated"})
    sections[donor_idx] = sections[donor_idx].model_copy(
        update={"content": donor_remainder, "status": "generated"}
    )
    logs.append(
        f"Activity Measures: moved populated table from “{donor_title}” "
        f"into BMP Section G (“{bmp.title}”)."
    )
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs


async def run_manuscript_consistency_repairs(
    draft: "ProposalDraft",
    *,
    skip_section_ids: set[str],
) -> tuple["ProposalDraft", list[str], list[str]]:
    """Deterministic fixes + human flags (no full-section LLM rewrite)."""
    from datetime import datetime, timezone

    from app.models.proposal import ProposalDraft

    logs: list[str] = []
    human: list[str] = []
    sections = list(draft.sections)
    changed = False

    for idx, section in enumerate(sections):
        if section.id in skip_section_ids:
            continue
        body = section.content or ""
        kpi_fixed, kpi_logs = apply_contractor_kpi_text_fixes(body)
        if kpi_fixed != body:
            body = kpi_fixed
            sections[idx] = section.model_copy(update={"content": body})
            logs.extend(kpi_logs)
            changed = True
        fixed, fix_logs = apply_deterministic_roster_fixes(body)
        if fixed != body:
            sections[idx] = section.model_copy(update={"content": fixed})
            logs.extend(fix_logs)
            changed = True

    working = (
        draft.model_copy(update={"sections": sections}) if changed else draft
    )
    working, am_logs = relocate_misplaced_activity_measures(
        working, skip_section_ids=skip_section_ids
    )
    if am_logs:
        logs.extend(am_logs)
        sections = list(working.sections)
        changed = True

    office = scan_oceania_office_contradiction(
        draft.model_copy(update={"sections": sections}) if changed else draft
    )
    if office:
        human.append(office)

    qual = scan_empty_qualifications_stub(
        draft.model_copy(update={"sections": sections}) if changed else draft
    )
    if qual:
        human.append(qual)

    if not changed:
        return draft, logs, human

    now = datetime.now(timezone.utc).isoformat()
    return (
        draft.model_copy(update={"sections": sections, "updated_at": now}),
        logs,
        human,
    )
