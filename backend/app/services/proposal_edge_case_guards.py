"""Recurring proposal edge-case guards — RFP-agnostic, no invented facts.

Observed across live QC (North Miami Beach RFP-2026-086 and prior):
1. Manuscript bio marks (§2.1 Sonja) substituted as if they were RFP cites
2. Blank name slots before ``will ensure/execute…``
3. County engagements described with ``city manager`` (copy-paste bleed)
4. Hollow References tabs with no MANUAL FILL / VERIFY handoff

These are mechanical; they never invent names, certs, or RFP section numbers.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalSection

# §2.1 (Sonja Anderson) / RFP §2.8 (Letitia Hopper)(C) — bio mark + person paren.
_BIO_MARK_AS_RFP_CITE_RE = re.compile(
    r"(?i)(?:RFP\s+)?(?:§\s*|Section\s+)(\d+\.\d+)\s*"
    r"\(([^)]{2,80}?)\)(?:\([A-Z]\))?"
)

# ", will ensure resource allocation" — missing subject name.
_BLANK_NAME_WILL_RE = re.compile(
    r"(?m)(?:^|[.!?]\s+|,\s+)(?:,\s*)?(will\s+(?:ensure|execute|lead|manage|"
    r"oversee|coordinate|deliver|drive)\b)",
)

# "X County … city manager" in one sentence — jurisdiction mismatch.
_COUNTY_CITY_MANAGER_RE = re.compile(
    r"(?i)\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)\s+County\b"
    r"([^.!?\n]{0,160}?)\bcity\s+managers?\b",
)

_HOLLOW_REF_FILL = (
    "[MANUAL FILL: Sonja — supply verified client references from "
    "ClientList / KB only (name, title, org, phone, email). Do not invent.]"
)


def collect_bio_person_names(draft: ProposalDraft) -> dict[str, str]:
    """Map casefolded person name → manuscript mark (``2.1``) from bio tabs."""
    out: dict[str, str] = {}
    for section in draft.sections:
        sid = section.id or ""
        title = section.title or ""
        title_cf = title.casefold()
        if not (
            sid.startswith("section-2-bio")
            or re.match(r"^\s*2\.\d+", title)
            or ("bio" in title_cf and re.search(r"\d+\.\d+", title))
        ):
            # Also accept any 2.N — First Last title in Section 2 band.
            if not re.match(r"^\s*2\.\d+\s*[.:—–\-]", title):
                continue
        match = re.match(
            r"^\s*(\d+\.\d+)\s*[.:—–\-)\]]\s*(.+)$",
            title,
        )
        if not match:
            continue
        mark, rest = match.group(1), match.group(2)
        # "Sonja Anderson — Executive Sponsor" / "Sonja Anderson, CEO"
        name = re.split(r"[—–,|(/]", rest, maxsplit=1)[0].strip()
        name = re.sub(r"\s+", " ", name)
        if len(name.split()) < 2:
            continue
        out[name.casefold()] = mark
    return out


def _paren_looks_like_person(paren: str) -> bool:
    raw = (paren or "").strip()
    if not raw or len(raw) > 60:
        return False
    # Drop pure section titles / roman / policy labels.
    if re.search(
        r"(?i)\b(?:cone of silence|proposal\s+bond|gifts?\s+policy|"
        r"insurance|certification|attachment|form|schedule|section)\b",
        raw,
    ):
        return False
    tokens = re.findall(r"[A-Za-z][a-z]+", raw)
    if len(tokens) < 2 or len(tokens) > 4:
        return False
    # Require Title Case person shape.
    return all(t[0].isupper() for t in tokens[:2])


def scrub_bio_marks_used_as_rfp_cites(
    content: str,
    *,
    bio_names: dict[str, str],
) -> tuple[str, list[str]]:
    """Replace manuscript bio § cites with VERIFY — never invent the real RFP #."""
    body = content or ""
    if not body.strip() or not bio_names:
        return body, []
    logs: list[str] = []

    def _repl(match: re.Match[str]) -> str:
        mark = match.group(1)
        paren = (match.group(2) or "").strip()
        paren_cf = paren.casefold()
        # Exact bio name hit, or paren is a person and mark matches a bio mark.
        is_bio_name = paren_cf in bio_names
        if not is_bio_name:
            # "Sonja Anderson" vs bio key; also first+last subset.
            for name_cf, bio_mark in bio_names.items():
                if name_cf in paren_cf or paren_cf in name_cf:
                    is_bio_name = True
                    mark = bio_mark
                    break
        if not is_bio_name:
            if not (
                _paren_looks_like_person(paren)
                and mark in set(bio_names.values())
            ):
                return match.group(0)
        logs.append(
            f"replaced bio mark §{mark} ({paren}) used as RFP citation"
        )
        return (
            f"[VERIFY: Sonja — confirm actual RFP section citation for this "
            f"requirement — manuscript bio §{mark} ({paren}) was incorrectly "
            f"substituted]"
        )

    updated = _BIO_MARK_AS_RFP_CITE_RE.sub(_repl, body)
    return updated, logs


def scrub_blank_name_before_will(content: str) -> tuple[str, list[str]]:
    """``, will ensure…`` → ``[MANUAL FILL: name] will ensure…``."""
    body = content or ""
    if not body.strip():
        return body, []
    logs: list[str] = []

    def _repl(match: re.Match[str]) -> str:
        lead = match.group(0)[: -len(match.group(1))]
        logs.append("flagged blank team-name before will-clause")
        return f"{lead}[MANUAL FILL: name] {match.group(1)}"

    updated, n = _BLANK_NAME_WILL_RE.subn(_repl, body)
    if not n:
        return body, []
    return updated, logs


def scrub_county_city_manager_mismatch(content: str) -> tuple[str, list[str]]:
    """County engagements must not cite a city manager (copy-paste bleed)."""
    body = content or ""
    if not body.strip():
        return body, []
    logs: list[str] = []

    def _repl(match: re.Match[str]) -> str:
        county = match.group(1)
        mid = match.group(2)
        logs.append(
            f"rewrote city manager → county leadership for {county} County"
        )
        return f"{county} County{mid}county leadership"

    updated, n = _COUNTY_CITY_MANAGER_RE.subn(_repl, body)
    if not n:
        return body, []
    return updated, logs


def flag_hollow_references_section(
    content: str,
    *,
    title: str,
) -> tuple[str, list[str]]:
    """Empty / stub References tabs must carry MANUAL FILL — never silent blank."""
    body = (content or "").strip()
    title_cf = (title or "").casefold()
    if "reference" not in title_cf:
        return content or "", []
    if re.search(r"(?i)\[(?:MANUAL\s+FILL|VERIFY)\b", body):
        return content or "", []
    # Already has contact-shaped substance.
    if re.search(
        r"(?i)\b(?:@|\(\d{3}\)|\d{3}[-.\s]\d{3}[-.\s]\d{4}|Reference\s+\d+)\b",
        body,
    ):
        return content or "", []
    words = len(re.findall(r"\b\w+\b", body))
    if words >= 50:
        return content or "", []
    logs = ["flagged hollow References section with MANUAL FILL"]
    if body:
        return f"{body.rstrip()}\n\n{_HOLLOW_REF_FILL}\n", logs
    return f"{_HOLLOW_REF_FILL}\n", logs


def apply_edge_case_guards_to_section(
    section: ProposalSection,
    *,
    bio_names: dict[str, str],
) -> tuple[ProposalSection, list[str]]:
    body = section.content or ""
    logs: list[str] = []
    body, cite_logs = scrub_bio_marks_used_as_rfp_cites(body, bio_names=bio_names)
    logs.extend(cite_logs)
    body, blank_logs = scrub_blank_name_before_will(body)
    logs.extend(blank_logs)
    body, county_logs = scrub_county_city_manager_mismatch(body)
    logs.extend(county_logs)
    body, ref_logs = flag_hollow_references_section(
        body, title=section.title or ""
    )
    logs.extend(ref_logs)
    if not logs:
        return section, []
    return section.model_copy(update={"content": body}), logs


def apply_edge_case_guards_to_draft(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, list[str]]:
    """Draft-wide edge-case scrubs for Generate / Complete Scan / chat persist."""
    bio_names = collect_bio_person_names(draft)
    sections: list[ProposalSection] = []
    all_logs: list[str] = []
    changed = False
    for section in draft.sections:
        updated, logs = apply_edge_case_guards_to_section(
            section, bio_names=bio_names
        )
        if logs:
            changed = True
            label = section.title or section.id
            all_logs.append(f"{label}: " + "; ".join(logs))
        sections.append(updated)
    if not changed:
        return draft, []
    return (
        draft.model_copy(
            update={
                "sections": sections,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        all_logs,
    )
