"""Scan RFP — never invent qualifications, case studies, or reference relationships."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection

# Known hallucinations from structure pass (extend as needed).
_FABRICATION_MARKERS: tuple[str, ...] = (
    "queensland tourism",
    "tourism fiji",
    "south australian tourism commission",
)

_CASE_STUDY_HEADER_RE = re.compile(r"Case Study\s+\d+\s*:", re.I)

_QUAL_REFERENCE_TITLE_HINTS: tuple[str, ...] = (
    "qualification",
    "offeror qualification",
    "contractor reference",
    "experience & contractor",
    "contractor references",
)


def _title_is_qual_or_reference(title: str) -> bool:
    t = (title or "").casefold()
    return any(h in t for h in _QUAL_REFERENCE_TITLE_HINTS)


def portfolio_client_names(draft: ProposalDraft) -> list[str]:
    names: list[str] = []
    for section in draft.sections:
        if not section.id.startswith("section-3-work-"):
            continue
        if section.id == "section-3-work-placeholder":
            continue
        raw = (section.title or "").strip()
        if "—" in raw:
            raw = raw.split("—", 1)[1].strip()
        elif " - " in raw:
            raw = raw.split(" - ", 1)[1].strip()
        raw = re.sub(r"^[\d.]+\s*", "", raw).strip()
        if raw and len(raw) >= 3:
            names.append(raw)
    return names


def _case_study_line_invented(line: str, portfolio_names: list[str]) -> bool:
    cf = line.casefold()
    if any(marker in cf for marker in _FABRICATION_MARKERS):
        return True
    if not portfolio_names:
        return True
    for name in portfolio_names:
        ncf = name.casefold()
        if ncf in cf or cf in ncf:
            return False
        # Token overlap for "Deschutes Brewery" vs "Deschutes"
        tokens = [t for t in re.split(r"\W+", ncf) if len(t) >= 5]
        if tokens and all(t in cf for t in tokens[:1]):
            return False
    return True


def section_has_invented_qual_content(content: str, portfolio_names: list[str]) -> bool:
    text = content or ""
    cf = text.casefold()
    if any(marker in cf for marker in _FABRICATION_MARKERS):
        return True
    for match in _CASE_STUDY_HEADER_RE.finditer(text):
        line_start = match.start()
        line_end = text.find("\n", match.end())
        if line_end == -1:
            line_end = min(len(text), match.end() + 200)
        line = text[line_start:line_end]
        if _case_study_line_invented(line, portfolio_names):
            return True
    return False


def honest_qual_verify_body(section: ProposalSection, requirements: list[str]) -> str:
    title = section.title or "this section"
    bullets = requirements[:12] if requirements else [
        "Creative work examples required by the RFP scoring criteria",
        "Geography-specific case studies if the RFP requires them",
        "Contractor references with real contact relationships only",
    ]
    req_lines = "\n".join(f"- [VERIFY: {b} — paste from verified Section 3 / KB only]" for b in bullets)
    return (
        f"[VERIFY: Draft content for {title} — insufficient evidence in corpus. "
        "Scan RFP will not invent clients, case studies, or reference relationships.]\n\n"
        f"### Requirements still needed from Sonja / verified portfolio\n\n{req_lines}\n"
    )


def _requirements_for_section(
    research: ProposalResearchCache | None,
    section_id: str,
) -> list[str]:
    if not research:
        return []
    for mapped in research.rfp_sections or []:
        if mapped.id == section_id:
            return list(mapped.requirements or [])
    return []


def repair_fabricated_qualifications(
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> tuple[ProposalDraft, list[str], list[str]]:
    """Revert qual/reference sections that invent Oceania case studies or fake refs."""
    logs: list[str] = []
    human: list[str] = []
    portfolio = portfolio_client_names(draft)
    sections = list(draft.sections)
    changed = False

    for idx, section in enumerate(sections):
        if not _title_is_qual_or_reference(section.title or ""):
            continue
        body = section.content or ""
        if not body.strip():
            continue
        if not section_has_invented_qual_content(body, portfolio):
            continue
        reqs = _requirements_for_section(research, section.id)
        new_body = honest_qual_verify_body(section, reqs)
        sections[idx] = section.model_copy(update={"content": new_body, "status": "generated"})
        logs.append(
            f"Fabrication guard: reverted invented qual/reference content in “{section.title}” "
            "to honest [VERIFY] placeholders (use Section 3 / KB only)."
        )
        human.append(
            f"“{section.title}” had fabricated case studies or references — reverted to [VERIFY]. "
            "Paste real work from Section 3 or KB; do not invent tourism-board engagements."
        )
        changed = True

    if not changed:
        return draft, logs, human
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs, human
