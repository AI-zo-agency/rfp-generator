"""Deterministic fact repairs for Complete & Clean (Scan RFP).

Fixes bio fabrication, leaked system notes, ownership language, invented banking,
cover-letter contact mismatches, and timeline week totals without LLM invention.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection

logger = logging.getLogger(__name__)

# Client-facing leaks — matched as literal line/bracket text, not regex.
_LEAK_LINE_MARKERS = (
    "delete this section",
    "deletion notice",
    "no verified clientlist",
    "previous draft contained unverified reference",
    "references package removed",
    "do not invent names or emails",
)


def _collapse_blank_lines(text: str) -> str:
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    while ", ," in text:
        text = text.replace(", ,", ",")
    while ",," in text:
        text = text.replace(",,", ",")
    return text


def _bracket_span_is_leak(inner: str) -> bool:
    cf = inner.strip().casefold()
    if cf.startswith("flag for") or cf.startswith("flag:") or cf.startswith("pricing flag"):
        return True
    if cf.startswith("delete this section") or cf.startswith("deletion notice"):
        return True
    if cf.startswith("references package removed"):
        return True
    if "no verified clientlist" in cf:
        return True
    if "do not invent names or emails" in cf:
        return True
    return False


def _strip_leaked_bracket_spans(text: str) -> tuple[str, bool]:
    changed = False
    pieces: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        start = text.find("[", i)
        if start < 0:
            pieces.append(text[i:])
            break
        end = text.find("]", start + 1)
        if end < 0:
            pieces.append(text[i:])
            break
        if _bracket_span_is_leak(text[start + 1 : end]):
            pieces.append(text[i:start])
            i = end + 1
            changed = True
            continue
        pieces.append(text[i : end + 1])
        i = end + 1
    return "".join(pieces), changed


def _drop_leaked_lines(text: str) -> tuple[str, bool]:
    changed = False
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.strip().casefold()
        if not body:
            kept.append(line)
            continue
        if any(marker in body for marker in _LEAK_LINE_MARKERS):
            changed = True
            continue
        if body.startswith("s and s") or body.startswith("s — no verified") or body.startswith(
            "s - no verified"
        ):
            changed = True
            continue
        kept.append(line)
    return "".join(kept), changed


_SOLE_PROPRIETOR_RE = re.compile(
    r"(?i)\bOwnership:\s*Sole proprietor\s+Sonja Anderson\b",
)
_SOLE_PROPRIETOR_GENERIC_RE = re.compile(r"(?i)\bsole proprietor\b")

_COLUMBIA_BANK_RE = re.compile(
    r"(?is)[^\n]*\bColumbia Bank\b[^\n]*\n?",
)

_FIN_STABILITY_RE = re.compile(
    r"(?is)"
    r"(?:financial stability|year-over-year growth|no liens|judgments|current on taxes)"
)

_KEY_PERSONNEL_RE = re.compile(
    r"(?is)(Key Personnel\s*:?\s*)([^\n]+)",
)

_PRIMARY_CONTACT_BLOCK_RE = re.compile(
    r"(?is)(Primary Contact\s*:?\s*)([^\n]+)",
)

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@zo\.agency\b", re.I)

_TABLE_WEEK_CELL_RE = re.compile(
    r"(?is)\|\s*(\d{1,3})\s*(?:weeks?|wks?)\s*\|",
)


def _evidence_blob(research: ProposalResearchCache | None) -> str:
    if research is None:
        return ""
    parts: list[str] = []
    for item in research.evidence_corpus or []:
        excerpt = getattr(item, "excerpt", None) or getattr(item, "text", None) or ""
        source = getattr(item, "source", None) or getattr(item, "file_name", None) or ""
        parts.append(f"{source}\n{excerpt}")
    return "\n".join(parts)


def scrub_leaked_system_fragments(content: str) -> tuple[str, list[str]]:
    text = content or ""
    logs: list[str] = []
    text, dropped = _drop_leaked_lines(text)
    if dropped:
        logs.append("Removed leaked internal/system fragment")
    text, stripped = _strip_leaked_bracket_spans(text)
    if stripped:
        logs.append("Removed leaked internal/system fragment")
    text = _collapse_blank_lines(text)
    if not logs:
        return content or "", []
    suffix = "\n" if (content or "").endswith("\n") else ""
    return text.strip() + suffix, logs


def apply_leaked_fragment_scrub_to_draft(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, list[str]]:
    """Strip leaked editor/system strings from every tab (client-facing body)."""
    logs: list[str] = []
    sections: list[ProposalSection] = []
    changed = False
    for section in draft.sections:
        body = section.content or ""
        cleaned, leak_logs = scrub_leaked_system_fragments(body)
        if leak_logs and cleaned != body:
            changed = True
            sections.append(section.model_copy(update={"content": cleaned}))
            logs.append(f"{section.title or section.id}: {leak_logs[0]}")
        else:
            sections.append(section)
    if not changed:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs


def repair_sole_proprietor_language(content: str) -> tuple[str, list[str]]:
    text = content or ""
    logs: list[str] = []
    if _SOLE_PROPRIETOR_RE.search(text):
        text = _SOLE_PROPRIETOR_RE.sub(
            "Ownership: Sonja Anderson, sole owner (S-Corp/LLC structure per company records)",
            text,
        )
        logs.append("Replaced 'Sole proprietor' with S-Corp/LLC sole-owner language")
    elif _SOLE_PROPRIETOR_GENERIC_RE.search(text):
        text = _SOLE_PROPRIETOR_GENERIC_RE.sub("sole owner (S-Corp/LLC)", text)
        logs.append("Replaced generic 'sole proprietor' with S-Corp/LLC phrasing")
    return text, logs


def scrub_unverified_banking_claims(
    content: str,
    *,
    evidence_text: str,
) -> tuple[str, list[str]]:
    text = content or ""
    if not _COLUMBIA_BANK_RE.search(text):
        return text, []
    blob = (evidence_text or "").casefold()
    if "columbia bank" in blob:
        return text, []
    cleaned = _COLUMBIA_BANK_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned, ["Removed unverified Columbia Bank claim (not in KB evidence)"]


def flag_financial_stability_without_companyfacts(
    content: str,
    *,
    evidence_text: str,
) -> tuple[str, list[str]]:
    text = content or ""
    if not _FIN_STABILITY_RE.search(text):
        return text, []
    blob = (evidence_text or "").casefold()
    if "01_companyfacts" in blob or "companyfacts_verified" in blob:
        if "year-over-year" in blob or "no liens" in blob:
            return text, []
    replacement = (
        "[MANUAL FILL: Sonja — confirm Financial Stability language from "
        "01_companyfacts_verified or leadership-approved boilerplate for this RFP. "
        "Do not reuse another client's proposal text without sign-off.]\n"
    )
    # Only replace the financial stability paragraph block if present
    block_re = re.compile(
        r"(?is)(?:^|\n)(?:#{1,4}\s*)?Financial Stability[^\n]*\n(?:[^\n#]+(?:\n[^\n#]+)*)",
    )
    if block_re.search(text):
        text = block_re.sub(replacement, text, count=1)
        return text, ["Flagged Financial Stability block — not in 01_companyfacts evidence"]
    return text, []


def parse_org_chart_roles(draft: ProposalDraft) -> dict[str, str]:
    """Name (lower) → canonical role title from Section 1.2 org chart."""
    roles: dict[str, str] = {}
    for section in draft.sections:
        sid = section.id or ""
        title_cf = (section.title or "").casefold()
        if sid != "section-1-2" and "org chart" not in title_cf and "team roster" not in title_cf:
            continue
        for line in (section.content or "").splitlines():
            if "|" not in line or "---" in line:
                continue
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if len(cells) < 2:
                continue
            name = re.sub(r"\*+", "", cells[0]).strip()
            role = re.sub(r"\*+", "", cells[-1]).strip()
            if re.search(r"[A-Za-z]+\s+[A-Za-z]+", name) and len(role) > 3:
                roles[name.casefold()] = role
    return roles


def _person_years_from_bio(content: str) -> str | None:
    """Best-effort max years string from a bio expertise table."""
    years_found: list[int] = []
    for match in re.finditer(r"(\d{1,2})\s*(?:\+)?\s*years?", content or "", re.I):
        try:
            years_found.append(int(match.group(1)))
        except ValueError:
            continue
    if not years_found:
        return None
    return f"{max(years_found)}+ years"


def repair_cover_letter_facts(
    content: str,
    *,
    org_roles: dict[str, str],
    bio_by_name: dict[str, str],
) -> tuple[str, list[str]]:
    text = content or ""
    logs: list[str] = []

    def _role_for(person: str) -> str | None:
        key = person.strip().casefold()
        if key in org_roles:
            return org_roles[key]
        for name, role in org_roles.items():
            if key.split()[0] in name and key.split()[-1] in name:
                return role
        return None

    match = _KEY_PERSONNEL_RE.search(text)
    if match and org_roles:
        people_bits: list[str] = []
        for name_key, role in sorted(org_roles.items(), key=lambda x: x[0]):
            display_name = " ".join(w.capitalize() for w in name_key.split())
            years = None
            for bio_name, bio_body in bio_by_name.items():
                if name_key.split()[0] in bio_name and name_key.split()[-1] in bio_name:
                    years = _person_years_from_bio(bio_body)
                    break
            bit = f"{display_name} ({role}"
            if years:
                bit += f", {years}"
            bit += ")"
            people_bits.append(bit)
        if people_bits:
            new_line = match.group(1) + ", ".join(people_bits)
            text = text[: match.start()] + new_line + text[match.end() :]
            logs.append("Rewrote Key Personnel from org chart + KB bios")

    # Primary Contact name vs @zo.agency email alignment
    pc = _PRIMARY_CONTACT_BLOCK_RE.search(text)
    if pc:
        block = pc.group(2)
        emails = _EMAIL_RE.findall(block)
        name_match = re.search(r"(Ron\s+Comer|Sonja\s+Anderson|Ella\s+Lindau)", block, re.I)
        if name_match and emails:
            name = name_match.group(1)
            email = emails[0]
            expected = {
                "ron comer": "ron@zo.agency",
                "sonja anderson": "sonja@zo.agency",
                "ella lindau": "ella@zo.agency",
            }.get(name.casefold())
            if expected and email.casefold() != expected:
                text = text.replace(email, expected, 1)
                logs.append(f"Aligned Primary Contact email to {expected} for {name}")

    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\(\s*,", "(", text)
    return text, logs


def repair_timeline_week_totals(content: str) -> tuple[str, list[str]]:
    text = content or ""
    week_sum = 0
    for match in _TABLE_WEEK_CELL_RE.finditer(text):
        try:
            week_sum += int(match.group(1))
        except ValueError:
            continue
    if week_sum <= 0 or not _TABLE_WEEK_CELL_RE.search(text):
        return text, []

    header_match = re.search(r"(?is)\b(\d{1,3})\s+weeks\b", text)
    if not header_match:
        return text, []

    stated = int(header_match.group(1))
    if stated == week_sum:
        return text, []

    new_text = (
        text[: header_match.start(1)]
        + str(week_sum)
        + text[header_match.end(1) :]
    )
    return new_text, [
        f"Timeline header weeks {stated} → {week_sum} to match phase table sum",
    ]


async def rebuild_team_bios_from_kb(
    draft: ProposalDraft,
    *,
    rfp_text: str = "",
) -> tuple[ProposalDraft, list[str]]:
    """Replace dumped resumes with the designer-note stub (attach 04_Bio PDF)."""
    from app.services.proposal_capability_bio_grounding import ground_bios_to_kb

    return await ground_bios_to_kb(draft, rfp_text=rfp_text, use_llm=False)


def _bio_index_by_name(draft: ProposalDraft) -> dict[str, str]:
    from app.services.proposal_bio_stub import is_plausible_person_name
    from app.services.proposal_kb_fact_checker import _member_name_from_bio_section

    out: dict[str, str] = {}
    for section in draft.sections:
        if not (section.id or "").startswith("section-2-bio-"):
            continue
        member = _member_name_from_bio_section(section.title or "")
        if member and is_plausible_person_name(member):
            out[member.casefold()] = section.content or ""
    return out


_EMPTY_TEAM_FIELD_RE = re.compile(
    r"(?im)^-\s*(?:"
    r"Qualifications|Relevant projects|Relevant work|Relevant experience|"
    r"Municipal branding experience"
    r"):\s*$"
)


def team_role_skeleton_is_hollow(content: str) -> bool:
    """True when a Project Team / Key Personnel tab left role fields blank."""
    return len(_EMPTY_TEAM_FIELD_RE.findall(content or "")) >= 2


def _case_study_labels(draft: ProposalDraft, *, limit: int = 4) -> list[str]:
    labels: list[str] = []
    for section in draft.sections:
        sid = section.id or ""
        title = (section.title or "").strip()
        if not title:
            continue
        if sid.startswith("section-3-") or "sample work" in title.casefold():
            # Prefer the display title after an em dash / number prefix.
            label = re.sub(r"^\s*(?:\d+(?:\.\d+)*\s*[—\-–:]\s*)", "", title).strip()
            if label and label not in labels:
                labels.append(label)
        if len(labels) >= limit:
            break
    return labels


def _named_roster_from_section2(
    draft: ProposalDraft,
    org_roles: dict[str, str],
) -> list[tuple[str, str]]:
    from app.services.proposal_bio_stub import (
        extract_engagement_role,
        is_plausible_person_name,
    )
    from app.services.proposal_kb_fact_checker import _member_name_from_bio_section

    rows: list[tuple[str, str]] = []
    for section in draft.sections:
        if not (section.id or "").startswith("section-2-bio-"):
            continue
        if (section.id or "").endswith("placeholder"):
            continue
        name = _member_name_from_bio_section(section.title or "")
        if not name or not is_plausible_person_name(name):
            continue
        role = extract_engagement_role(section.content or "")
        if not role or role.casefold().startswith("bio "):
            role = org_roles.get(name.casefold(), "") or "Assigned to this engagement"
        rows.append((name, role))
    return rows


def fill_hollow_project_team_from_bios(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, list[str]]:
    """Replace empty role-skeleton team tabs with the named Section 2 roster.

    Writers sometimes emit untitled role shells (Qualifications: with no body).
    Past-proposal / bio substance already lives in Section 2 + Our Work — reuse it
    instead of leaving blank bullets.
    """
    org_roles = parse_org_chart_roles(draft)
    roster = _named_roster_from_section2(draft, org_roles)
    if len(roster) < 2:
        return draft, []
    projects = _case_study_labels(draft)
    logs: list[str] = []
    sections: list[ProposalSection] = []
    changed = False
    for section in draft.sections:
        body = section.content or ""
        if not team_role_skeleton_is_hollow(body):
            sections.append(section)
            continue
        title = (section.title or "Project Team").strip()
        lines = [
            f"## {title}",
            "",
            "### Named project team",
            "",
            "The engagement team below matches the Section 2 bios selected for this "
            "proposal. Full résumés / 04_Bio PDFs are attached per the RFP; manuscript "
            "entries stay concise.",
            "",
        ]
        for name, role in roster:
            lines.append(f"**{name}**")
            lines.append(f"- Role: {role}")
            lines.append(
                f"- Qualifications: See Section 2 — {name} (approved bio PDF). "
                "Public-sector brand and communications experience is documented in "
                "Our Work / Sample Work."
            )
            if projects:
                lines.append(f"- Relevant projects: {'; '.join(projects)}")
            lines.append("")
        new_body = "\n".join(lines).strip()
        sections.append(section.model_copy(update={"content": new_body, "status": "generated"}))
        logs.append(
            f"«{title}»: filled hollow role skeleton from {len(roster)} Section 2 bio(s)"
            + (f" + {len(projects)} work sample(s)" if projects else "")
        )
        changed = True
    if not changed:
        return draft, []
    return draft.model_copy(update={"sections": sections}), logs


async def run_scan_fact_repairs(
    draft: ProposalDraft,
    *,
    research: ProposalResearchCache | None,
    rfp_text: str = "",
    rfp_title: str = "",
    rfp_client: str = "",
    rfp_sector: str = "",
    rfp_id: str = "",
) -> tuple[ProposalDraft, list[str]]:
    """Deterministic scan repairs + hollow-answer fill from past won proposals."""
    logs: list[str] = []
    evidence = _evidence_blob(research)

    from app.services.agency_facts import apply_canonical_agency_tenure_to_draft

    draft, tenure_logs = apply_canonical_agency_tenure_to_draft(draft)
    logs.extend(tenure_logs)

    draft, bio_logs = await rebuild_team_bios_from_kb(draft, rfp_text=rfp_text)
    logs.extend(bio_logs)

    from app.services.proposal_hollow_kb_fill import fill_hollow_sections_for_pipeline

    draft, hollow_logs = await fill_hollow_sections_for_pipeline(
        draft,
        rfp_title=rfp_title,
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
        rfp_text=rfp_text,
        rfp_id=rfp_id or (draft.rfp_id if draft else ""),
    )
    logs.extend(hollow_logs)

    from app.services.proposal_scan_compliance_fabrication import (
        run_compliance_fabrication_repairs,
    )

    draft, compliance_logs = await run_compliance_fabrication_repairs(
        draft,
        rfp_text=rfp_text,
        evidence_text=evidence,
    )
    logs.extend(compliance_logs)

    org_roles = parse_org_chart_roles(draft)
    bio_by_name = _bio_index_by_name(draft)

    sections: list[ProposalSection] = []
    changed = False
    for section in draft.sections:
        body = section.content or ""
        title_cf = (section.title or "").casefold()
        sid = section.id or ""
        new_body = body
        section_logs: list[str] = []

        scrubbed, leak_logs = scrub_leaked_system_fragments(new_body)
        if scrubbed != new_body:
            new_body = scrubbed
            section_logs.extend(leak_logs)

        fixed, sp_logs = repair_sole_proprietor_language(new_body)
        if fixed != new_body:
            new_body = fixed
            section_logs.extend(sp_logs)

        if "cover letter" in title_cf or "transmittal" in title_cf:
            fixed_cl, cl_logs = repair_cover_letter_facts(
                new_body,
                org_roles=org_roles,
                bio_by_name=bio_by_name,
            )
            if fixed_cl != new_body:
                new_body = fixed_cl
                section_logs.extend(cl_logs)

        if "timeline" in title_cf or "schedule" in title_cf or "milestone" in title_cf:
            fixed_tl, tl_logs = repair_timeline_week_totals(new_body)
            if fixed_tl != new_body:
                new_body = fixed_tl
                section_logs.extend(tl_logs)

        if sid.startswith("section-1-") or "company" in title_cf:
            fixed_bank, bank_logs = scrub_unverified_banking_claims(
                new_body, evidence_text=evidence
            )
            if fixed_bank != new_body:
                new_body = fixed_bank
                section_logs.extend(bank_logs)
            fixed_fin, fin_logs = flag_financial_stability_without_companyfacts(
                new_body, evidence_text=evidence
            )
            if fixed_fin != new_body:
                new_body = fixed_fin
                section_logs.extend(fin_logs)

        if new_body != body:
            changed = True
            sections.append(section.model_copy(update={"content": new_body}))
            for line in section_logs:
                logs.append(f"{section.title or sid}: {line}")
        else:
            sections.append(section)

    if not changed and not logs:
        return draft, logs

    from app.services.proposal_manuscript import strip_internal_flag_tags
    from app.services.agency_facts import apply_canonical_agency_tenure_to_draft

    final_sections: list[ProposalSection] = []
    for section in sections:
        flag_scrubbed = strip_internal_flag_tags(section.content or "")
        if flag_scrubbed != (section.content or ""):
            logs.append(f"{section.title or section.id}: Removed [FLAG FOR …] tags")
            section = section.model_copy(update={"content": flag_scrubbed})
        final_sections.append(section)

    updated = draft.model_copy(update={"sections": final_sections})
    return updated, logs
