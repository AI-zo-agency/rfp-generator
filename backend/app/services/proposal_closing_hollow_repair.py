"""Normalize hollow closing tabs — references intros and addenda handoff tables."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.proposal import ProposalDraft, ProposalSection

_HANDOFF_CELL_RE = re.compile(
    r"^\[(?:MANUAL\s+FILL|VERIFY|FLAG|DESIGNER\s+NOTE)\b[^\]]*\]$",
    re.I,
)
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:\-–—]+\|[\s|:\-–—]+\|?\s*$")
_REFERENCES_BELOW_RE = re.compile(
    r"(?is)\b(?:references?\s+below|provide\s+three\s+[^.]*references|"
    r"three\s+municipal\s+references|client\s+references\s+below)\b"
)
_HAS_REFERENCE_CONTACT_RE = re.compile(
    r"(?i)(\bphone\b|\bemail\b|\bcontact\b|@|\(\d{3}\)|\d{3}[-.\s]\d{3})",
)


def _md_cells(line: str) -> list[str]:
    return [c.strip() for c in (line or "").strip().strip("|").split("|")]


def _is_handoff_cell(cell: str) -> bool:
    return bool(_HANDOFF_CELL_RE.match((cell or "").strip()))


def _table_is_handoff_spam(block_lines: list[str]) -> bool:
    data = [ln for ln in block_lines if not _TABLE_SEP_RE.match(ln)]
    if len(data) < 2:
        return False
    cells: list[str] = []
    for row in data[1:]:
        cells.extend(c for c in _md_cells(row) if c)
    if not cells:
        return False
    handoff = sum(1 for c in cells if _is_handoff_cell(c))
    return handoff / len(cells) >= 0.6


_CLEAN_ADDENDA = """## Acknowledgment of Addenda

As of this proposal submission, zö agency has monitored the solicitation portal for addenda.

| Addendum Number | Issue Date | Description | Acknowledged |
| --- | --- | --- | --- |
| None issued / none received | — | No addenda posted as of the submission date | Yes |

[MANUAL FILL: Sonja — confirm on the buyer portal whether any addenda were issued; update the table if so]
"""


def normalize_addenda_handoff_tables(content: str) -> tuple[str, bool]:
    """Replace addenda tables that are mostly MANUAL FILL chips with a clean template."""
    text = content or ""
    if "|" not in text:
        return text, False
    title_hint = bool(
        re.search(r"(?i)addend", text[:400])
        or re.search(r"(?i)addendum\s+number", text)
    )
    if not title_hint and not re.search(r"(?i)\|[^|\n]*addendum", text):
        return text, False

    lines = text.split("\n")
    out: list[str] = []
    index = 0
    changed = False
    while index < len(lines):
        line = lines[index]
        if "|" not in line:
            out.append(line)
            index += 1
            continue
        block = [line]
        cursor = index + 1
        while cursor < len(lines) and "|" in (lines[cursor] or ""):
            block.append(lines[cursor])
            cursor += 1
        header = " ".join(_md_cells(block[0])).casefold()
        is_addenda_table = (
            "addendum" in header
            or "addenda" in header
            or "issue date" in header
            or "acknowledged" in header
        )
        if is_addenda_table and _table_is_handoff_spam(block):
            # Drop prior duplicate heading lines immediately above the table.
            while out and not out[-1].strip():
                out.pop()
            if out and re.match(r"^\s*#{1,3}\s+.*addend", out[-1], re.I):
                out.pop()
            out.append(_CLEAN_ADDENDA.strip())
            changed = True
            index = cursor
            continue
        out.extend(block)
        index = cursor
    return "\n".join(out), changed


def references_section_is_hollow(content: str) -> bool:
    """True when the tab claims references but has no contact details."""
    body = (content or "").strip()
    if not body:
        return True
    if _HAS_REFERENCE_CONTACT_RE.search(body):
        return False
    if _REFERENCES_BELOW_RE.search(body):
        return True
    return len(body.split()) < 60


_REFERENCES_HANDOFF = (
    "[MANUAL FILL: Sonja — provide three municipal/governmental client references "
    "with organization, contact name, title, phone, and email]"
)


def repair_hollow_references_section(content: str, *, title: str = "") -> tuple[str, bool]:
    """Fix intros that promise references 'below' but list none."""
    body = (content or "").strip()
    title_cf = (title or "").casefold()
    if "reference" not in title_cf and "reference" not in body[:200].casefold():
        return content or "", False
    if _HAS_REFERENCE_CONTACT_RE.search(body):
        return content or "", False
    if not _REFERENCES_BELOW_RE.search(body) and len(body.split()) > 80:
        return content or "", False
    # Thin / hollow references tab
    cleaned = re.sub(
        r"(?is)\b(we\s+provide|below\s+are|listed\s+below)[^.]*references?[^.]*\.\s*",
        "RFP requires three municipal/governmental client references with contact information. ",
        body,
        count=1,
    )
    if _REFERENCES_HANDOFF.casefold() in cleaned.casefold():
        return cleaned, cleaned != body
    cleaned = (cleaned.rstrip() + "\n\n" + _REFERENCES_HANDOFF + "\n").strip() + "\n"
    return cleaned, True


def repair_hollow_closing_sections(
    draft: "ProposalDraft",
) -> tuple["ProposalDraft", list[str]]:
    """Deterministic repair for hollow references + addenda MANUAL FILL spam."""
    from datetime import datetime, timezone

    from app.models.proposal import ProposalSection

    if not draft.sections:
        return draft, []

    logs: list[str] = []
    sections: list[ProposalSection] = []
    changed = False
    for section in draft.sections:
        body = section.content or ""
        title = section.title or ""
        title_cf = title.casefold()
        updated = body
        if "addend" in title_cf or "addend" in body[:300].casefold():
            updated, did = normalize_addenda_handoff_tables(updated)
            if did:
                logs.append(f"{title or section.id}: cleaned addenda MANUAL FILL table")
        if "reference" in title_cf:
            updated, did = repair_hollow_references_section(updated, title=title)
            if did:
                logs.append(f"{title or section.id}: hollow references → Sonja handoff")
        if updated != body:
            changed = True
            sections.append(section.model_copy(update={"content": updated}))
        else:
            sections.append(section)

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
