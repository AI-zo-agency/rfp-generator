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
        stub = len(blob) < 400 and (
            "[verify:" in blob
            or "insufficient evidence" in blob
            or "manual fill" in blob
        )
        if qual and stub:
            return (
                f"Section “{section.title}” is still a placeholder — RFP Criteria #1/#3 "
                "need creative examples, Oceania/Hawaii case studies, and references. "
                "Generate or paste from KB before submit."
            )
    return None


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
