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

# Evaluator-facing apology / process narration — never proposal-ready.
# Live Alameda "References and past performance" opened with "We can't complete
# a reference table… from our knowledge base" plus "we'll supply… as part of
# finalizing" — designers paste tabs into InDesign; flags only, no meta prose.
_GAP_NARRATION_PARA_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"\b(?:we\s+can'?t|we\s+cannot|we\s+are\s+not\s+able\s+to|unable\s+to)\b"
    r".{0,220}?\b(?:knowledge\s+base|complete\s+a\s+reference|publish\s+complete|"
    r"verified\s+contact|reference\s+table|contact\s+records?)\b"
    r"|\bwhat\s+we\s+can\s+stand\s+behind\b"
    r"|\bwe'?ll\s+supply\b.{0,160}?\b(?:finaliz\w*|submission|before\s+submit)\b"
    r"|\ba\s+reference\s+call\s+would\s+tell\s+you\b"
    r"|\bfrom\s+our\s+knowledge\s+base\s+for\s+this\s+response\b"
    r"|\bwe\s+are\s+not\s+able\s+to\s+publish\b"
    r"|\bas\s+part\s+of\s+finalizing\s+this\s+submission\b"
    r")"
)

# Empty list chrome the LLM sometimes emits ("2." with no text) — destroy
# trust and break declaration forms. Strip and renumber; never invent content.
_EMPTY_ORDERED_ITEM_RE = re.compile(r"(?m)^(?P<indent>\s*)(?P<num>\d+)(?P<mark>[.)])\s*$")
_EMPTY_BULLET_ITEM_RE = re.compile(r"(?m)^(?P<indent>\s*)(?P<mark>[-*•])\s*$")
_ORDERED_ITEM_WITH_TEXT_RE = re.compile(
    r"^(?P<indent>\s*)(?P<num>\d+)(?P<mark>[.)])(?P<body>\s+\S.*)$"
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


def _renumber_ordered_list_runs(lines: list[str]) -> list[str]:
    """Renumber contiguous ordered-list runs after empty items were removed."""
    out: list[str] = []
    run_index = 0
    in_run = False
    for line in lines:
        match = _ORDERED_ITEM_WITH_TEXT_RE.match(line)
        if match:
            if not in_run:
                run_index = 0
                in_run = True
            run_index += 1
            out.append(
                f"{match.group('indent')}{run_index}{match.group('mark')}"
                f"{match.group('body')}"
            )
            continue
        in_run = False
        out.append(line)
    return out


def scrub_empty_list_items(content: str) -> tuple[str, list[str]]:
    """Remove empty numbered/bulleted lines and renumber remaining ordered items.

    Live Alameda Non-Collusion declaration emitted ``2.`` with no text between
    items 1 and 3 — form prose must never ship with blank list slots.
    """
    if not (content or "").strip():
        return content or "", []
    ended_with_newline = content.endswith("\n")
    lines = content.splitlines()
    kept: list[str] = []
    removed = 0
    for line in lines:
        if _EMPTY_ORDERED_ITEM_RE.match(line) or _EMPTY_BULLET_ITEM_RE.match(line):
            removed += 1
            continue
        kept.append(line)
    if not removed:
        return content, []
    renumbered = _renumber_ordered_list_runs(kept)
    body = "\n".join(renumbered)
    if ended_with_newline:
        body += "\n"
    return body, [f"removed {removed} empty list item(s) and renumbered"]


def scrub_gap_narration_prose(content: str, *, title: str = "") -> tuple[str, list[str]]:
    """Strip evaluator-facing apologies about missing KB / deferred contacts.

    Proposal tabs are designer-ready paste. Missing facts → ``[MANUAL FILL]`` /
    ``[VERIFY]`` only — never paragraphs explaining what the model could not
    complete, what a reference call would say, or what will be supplied later.
    """
    if not (content or "").strip():
        return content or "", []
    ended_with_newline = content.endswith("\n")
    kept: list[str] = []
    dropped = 0
    for block in re.split(r"\n\s*\n", content):
        chunk = block.strip()
        if not chunk:
            continue
        if re.match(r"(?i)^\[(?:MANUAL\s+FILL|VERIFY|DESIGNER\s+NOTE)\b", chunk):
            kept.append(chunk)
            continue
        if chunk.startswith("|"):
            kept.append(chunk)
            continue
        if chunk.startswith("#") and _GAP_NARRATION_PARA_RE.search(chunk):
            dropped += 1
            continue
        if chunk.startswith("#"):
            kept.append(chunk)
            continue
        if _GAP_NARRATION_PARA_RE.search(chunk):
            dropped += 1
            # Keep any handoff tags that were glued onto the apology paragraph.
            for tag in re.findall(
                r"\[(?:MANUAL\s+FILL|VERIFY|DESIGNER\s+NOTE):[^\]]*\]",
                chunk,
                flags=re.IGNORECASE,
            ):
                kept.append(tag)
            continue
        kept.append(chunk)
    if not dropped:
        return content, []

    title_cf = (title or "").casefold()
    body = "\n\n".join(kept).strip()
    if "reference" in title_cf or "past performance" in title_cf:
        has_fill = bool(re.search(r"(?i)\[(?:MANUAL\s+FILL|VERIFY)\b", body))
        has_table = "|" in body
        if not has_table and (not body or not has_fill):
            body = f"{body}\n\n{_HOLLOW_REF_FILL}".strip() if body else _HOLLOW_REF_FILL
        elif not has_fill:
            body = f"{body.rstrip()}\n\n{_HOLLOW_REF_FILL}"
    if ended_with_newline and body:
        body += "\n"
    return body, [f"removed {dropped} gap-narration paragraph(s)"]


def flag_hollow_references_section(
    content: str,
    *,
    title: str,
) -> tuple[str, list[str]]:
    """Empty / stub References tabs must carry MANUAL FILL — never silent blank."""
    body = (content or "").strip()
    title_cf = (title or "").casefold()
    if "reference" not in title_cf and "past performance" not in title_cf:
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


def _section_is_resume_pointer_tab(section: ProposalSection) -> bool:
    """Resume tabs legitimately cite manuscript §2 bios — do not VERIFY them away."""
    title = (section.title or "").casefold()
    if re.search(
        r"\b(?:resumes?|curriculum\s+vitae|\bcv\b|key\s+personnel|"
        r"personnel\s+resumes?|staff\s+resumes?)\b",
        title,
    ):
        return True
    body = (section.content or "")[:400].casefold()
    return "resumes of key" in body or "key personnel resumes" in body


def apply_edge_case_guards_to_section(
    section: ProposalSection,
    *,
    bio_names: dict[str, str],
) -> tuple[ProposalSection, list[str]]:
    body = section.content or ""
    logs: list[str] = []
    # Resume / key-personnel tabs MUST point at manuscript §2 bios — never
    # replace those pointers with VERIFY. That destructive scrub left ten
    # "incorrectly substituted" placeholders as visible content.
    if not _section_is_resume_pointer_tab(section):
        body, cite_logs = scrub_bio_marks_used_as_rfp_cites(body, bio_names=bio_names)
        logs.extend(cite_logs)
    body, blank_logs = scrub_blank_name_before_will(body)
    logs.extend(blank_logs)
    body, list_logs = scrub_empty_list_items(body)
    logs.extend(list_logs)
    body, gap_logs = scrub_gap_narration_prose(body, title=section.title or "")
    logs.extend(gap_logs)
    body, county_logs = scrub_county_city_manager_mismatch(body)
    logs.extend(county_logs)
    body, ref_logs = flag_hollow_references_section(
        body, title=section.title or ""
    )
    logs.extend(ref_logs)
    if not logs:
        return section, []
    return section.model_copy(update={"content": body}), logs


_REF_CONTACT_FILL = (
    "[MANUAL FILL: Sonja — verified contact from ClientList/KB "
    "(name, title, phone, email)]"
)

_DEFAULT_REF_TABLE_HEADER = (
    "| Client / Engagement | Scope / Relevance | Reference Contact | Contact Info |\n"
    "|---|---|---|---|\n"
)

_LAYOUT_INSTRUCTION_RE = re.compile(
    r"(?im)^(?:\s*(?:single\s+)?\d[\s-]*column\s+table[^\n]*|"
    r"[^\n]*one\s+column\s+per\s+reference[^\n]*|"
    r"[^\n]*no\s+additional\s+layout\s+needed[^\n]*|"
    r"[^\n]*no\s+additional\s+layout[^\n]*)\s*$"
)

_REF_LIST_ITEM_RE = re.compile(
    r"(?m)^\s*(?:\d{1,2}[\.)]\s+|[-*•]\s+)(.+?)\s*$"
)

_FIELD_LABEL_ONLY_RE = re.compile(
    r"(?im)^\s*(?:\*\*|__)?\s*"
    r"(?:contact|phone|email|title|organization|name|"
    r"reference\s+contact|contact\s+info)"
    r"\s*(?:\*\*|__)?\s*:?\s*(?:\*\*|__)?\s*$"
)


def _strip_reference_layout_instructions(body: str) -> str:
    lines = [
        ln
        for ln in (body or "").splitlines()
        if not _LAYOUT_INSTRUCTION_RE.match(ln.strip())
    ]
    return "\n".join(lines).strip()


def _wants_column_per_reference(text: str) -> bool:
    cf = (text or "").casefold()
    return bool(
        "column per reference" in cf
        or "one column per reference" in cf
        or re.search(r"\b\d[\s-]*column\b.{0,48}reference", cf)
    )


def _engagements_from_list_body(body: str) -> list[str]:
    """Pull client/engagement labels from numbered or bulleted reference lists."""
    items: list[str] = []
    seen: set[str] = set()
    for match in _REF_LIST_ITEM_RE.finditer(body or ""):
        raw = match.group(1).strip()
        if _FIELD_LABEL_ONLY_RE.match(raw):
            continue
        if re.match(r"(?i)^\[(?:MANUAL\s+FILL|VERIFY)\b", raw):
            continue
        if re.match(r"(?i)^(contact|phone|email)\s*:", raw):
            continue
        raw = re.split(r"(?i)\bContact\s*:", raw)[0].strip()
        raw = re.sub(r"\*+", "", raw).strip(" -—–")
        if len(raw) < 4:
            continue
        key = raw.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(raw[:140])
        if len(items) >= 6:
            break
    return items


def _build_row_per_reference_table(engagements: list[str]) -> str:
    rows = [
        f"| {eng} | Relevant past performance for this RFP | "
        f"{_REF_CONTACT_FILL} | {_REF_CONTACT_FILL} |"
        for eng in engagements
    ]
    if not rows:
        rows = [
            f"| [MANUAL FILL: Sonja — client / engagement from ClientList] | "
            f"Relevant past performance for this RFP | {_REF_CONTACT_FILL} | "
            f"{_REF_CONTACT_FILL} |"
            for _ in range(3)
        ]
    return _DEFAULT_REF_TABLE_HEADER + "\n".join(rows)


def _build_column_per_reference_table(engagements: list[str], *, columns: int = 4) -> str:
    """RFP layout: one column per reference; rows are field labels."""
    n = max(2, min(columns, 6))
    refs = list(engagements[:n])
    while len(refs) < n:
        refs.append("[MANUAL FILL: Sonja — client / engagement from ClientList/KB]")
    header = "| Field | " + " | ".join(f"Reference {i + 1}" for i in range(n)) + " |"
    sep = "|---|" + "|".join(["---"] * n) + "|"
    client = "| Client / Engagement | " + " | ".join(refs) + " |"
    contact = "| Reference Contact | " + " | ".join([_REF_CONTACT_FILL] * n) + " |"
    info = "| Phone / Email | " + " | ".join([_REF_CONTACT_FILL] * n) + " |"
    return "\n".join([header, sep, client, contact, info])


def _extract_markdown_table_block(content: str) -> str | None:
    """Return the first markdown table in ``content``, or None."""
    lines = (content or "").splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("|") and lines[i].count("|") >= 3:
            block = [lines[i]]
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith("|"):
                block.append(lines[j])
                j += 1
            header = " ".join(block[0].casefold().split())
            if any(
                key in header
                for key in (
                    "engagement",
                    "client",
                    "organization",
                    "reference",
                    "contact",
                    "project",
                    "field",
                )
            ):
                # Header-only / separator-only is not a usable table.
                data_rows = [
                    ln
                    for ln in block[1:]
                    if ln.strip().startswith("|")
                    and not re.match(r"^\s*\|?\s*:?-{2,}", ln.strip())
                ]
                if data_rows:
                    return "\n".join(block).strip()
            i = j
            continue
        i += 1
    return None


def _engagement_rows_from_case_studies(draft: ProposalDraft) -> list[str]:
    """Build table data rows from Section 3 case-study titles — never invent contacts."""
    rows: list[str] = []
    for section in draft.sections:
        sid = section.id or ""
        title = (section.title or "").strip()
        if not (
            sid.startswith("section-3-")
            or re.match(r"^\s*3\.\d+", title)
        ):
            continue
        # "3.1 — City of Umatilla Digital Campaign 2006"
        name = re.sub(r"^\s*\d+(?:\.\d+)*\s*[.:—–\-]\s*", "", title).strip()
        if not name or len(name) < 4:
            continue
        if "overview" in name.casefold() or "our work" in name.casefold():
            continue
        rows.append(
            f"| {name} | Past municipal / public-sector delivery "
            f"(see Section 3) | {_REF_CONTACT_FILL} | {_REF_CONTACT_FILL} |"
        )
        if len(rows) >= 5:
            break
    return rows


def _engagement_names_from_case_studies(draft: ProposalDraft) -> list[str]:
    names: list[str] = []
    for section in draft.sections:
        sid = section.id or ""
        title = (section.title or "").strip()
        if not (
            sid.startswith("section-3-")
            or re.match(r"^\s*3\.\d+", title)
        ):
            continue
        name = re.sub(r"^\s*\d+(?:\.\d+)*\s*[.:—–\-]\s*", "", title).strip()
        if not name or len(name) < 4:
            continue
        if "overview" in name.casefold() or "our work" in name.casefold():
            continue
        names.append(name)
        if len(names) >= 5:
            break
    return names


def _references_tab_needs_table(content: str) -> bool:
    """True when References lacks a usable contact/engagement markdown table."""
    body = _strip_reference_layout_instructions(content or "")
    if not body.strip():
        return True
    if _extract_markdown_table_block(body):
        return False
    # List-shaped or prose-only references still need a real table — word count
    # must not suppress the rebuild (that left numbered lists + empty Contact:).
    return True


def ensure_references_tabs_have_tables(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, list[str]]:
    """References / past-performance tabs must ship a contact table, not a lone flag.

    Prefer the drafted engagement table from a sibling References tab (e.g. Section D).
    Otherwise convert list-form engagements, then Section 3 case studies, with
    MANUAL FILL contact cells. Never invent names, phones, or emails.
    """
    logs: list[str] = []
    # Best sibling table (prefer longer).
    donor_table: str | None = None
    donor_score = 0
    for section in draft.sections:
        title_cf = (section.title or "").casefold()
        if "reference" not in title_cf and "past performance" not in title_cf:
            continue
        table = _extract_markdown_table_block(section.content or "")
        if not table:
            continue
        score = table.count("\n") + len(table)
        if score > donor_score:
            donor_table = table
            donor_score = score

    case_rows = _engagement_rows_from_case_studies(draft)
    case_names = _engagement_names_from_case_studies(draft)
    sections: list[ProposalSection] = []
    changed = False
    for section in draft.sections:
        title = section.title or ""
        title_cf = title.casefold()
        if "reference" not in title_cf and "past performance" not in title_cf:
            sections.append(section)
            continue
        raw_body = section.content or ""
        body = _strip_reference_layout_instructions(raw_body)
        if not _references_tab_needs_table(body):
            if body != raw_body:
                sections.append(section.model_copy(update={"content": body + "\n"}))
                logs.append(f"{title}: stripped leaked layout-instruction lines")
                changed = True
            else:
                sections.append(section)
            continue

        list_engagements = _engagements_from_list_body(body)
        column_layout = _wants_column_per_reference(raw_body) or _wants_column_per_reference(
            title
        )
        table: str | None = None
        source = ""
        if list_engagements:
            if column_layout:
                table = _build_column_per_reference_table(list_engagements)
                source = "list engagements (column-per-reference)"
            else:
                table = _build_row_per_reference_table(list_engagements)
                source = "list engagements"
        if not table and donor_table:
            table = donor_table
            source = "sibling References tab"
        if not table and case_rows:
            if column_layout:
                table = _build_column_per_reference_table(case_names)
                source = "Section 3 engagements (column-per-reference)"
            else:
                table = _DEFAULT_REF_TABLE_HEADER + "\n".join(case_rows)
                source = "Section 3 engagements"
        if not table:
            if column_layout:
                table = _build_column_per_reference_table([])
                source = "blank MANUAL FILL columns"
            else:
                table = (
                    _DEFAULT_REF_TABLE_HEADER
                    + f"| [MANUAL FILL: Sonja — client / engagement from ClientList] | "
                    f"Relevant past performance for this RFP | {_REF_CONTACT_FILL} | "
                    f"{_REF_CONTACT_FILL} |\n"
                    + f"| [MANUAL FILL: Sonja — client / engagement from ClientList] | "
                    f"Relevant past performance for this RFP | {_REF_CONTACT_FILL} | "
                    f"{_REF_CONTACT_FILL} |\n"
                    + f"| [MANUAL FILL: Sonja — client / engagement from ClientList] | "
                    f"Relevant past performance for this RFP | {_REF_CONTACT_FILL} | "
                    f"{_REF_CONTACT_FILL} |"
                )
                source = "blank MANUAL FILL rows"
        # Keep short operational leads — drop list items (they become the table).
        lead_parts: list[str] = []
        for block in re.split(r"\n\s*\n", body):
            chunk = block.strip()
            if not chunk or chunk.startswith("|"):
                continue
            if re.match(r"(?i)^\[(?:MANUAL\s+FILL|VERIFY)\b", chunk):
                continue
            if _GAP_NARRATION_PARA_RE.search(chunk):
                continue
            if chunk.startswith("#"):
                lead_parts.append(chunk)
                continue
            # Skip numbered/bulleted engagement lists — folded into the table.
            if _REF_LIST_ITEM_RE.search(chunk) and not chunk.startswith("#"):
                list_lines = [
                    ln
                    for ln in chunk.splitlines()
                    if ln.strip() and not _REF_LIST_ITEM_RE.match(ln)
                    and not _FIELD_LABEL_ONLY_RE.match(ln.strip())
                ]
                if not list_lines:
                    continue
                chunk = "\n".join(list_lines).strip()
                if not chunk:
                    continue
            if _FIELD_LABEL_ONLY_RE.match(chunk):
                continue
            if len(re.findall(r"\b\w+\b", chunk)) <= 60:
                lead_parts.append(chunk)
        parts = [f"## {title}"] if not any(p.startswith("#") for p in lead_parts) else []
        parts.extend(lead_parts[:2])
        parts.append(table)
        parts.append(_HOLLOW_REF_FILL)
        next_body = "\n\n".join(p for p in parts if p).strip() + "\n"
        sections.append(
            section.model_copy(
                update={
                    "content": next_body,
                    "status": "generated",
                    "word_target": max(section.word_target or 0, 250),
                }
            )
        )
        logs.append(
            f"{title}: rebuilt References contact table from {source}"
        )
        changed = True
    if not changed:
        return draft, []
    return (
        draft.model_copy(
            update={
                "sections": sections,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        logs,
    )


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
    working = (
        draft.model_copy(
            update={
                "sections": sections,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if changed
        else draft
    )
    working, table_logs = ensure_references_tabs_have_tables(working)
    all_logs.extend(table_logs)
    if not all_logs:
        return draft, []
    return working, all_logs
