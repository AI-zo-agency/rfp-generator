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
_DESIGNER_NOTE_TAG_RE = re.compile(
    r"\[DESIGNER\s+NOTE\s*:([^\]]*)\]",
    re.I,
)
_TABLEISH_NOTE_RE = re.compile(
    r"\b(table|matrix|grid|columns?|rows?|checklist|swimlane|gantt|timeline)\b",
    re.I,
)
_HAS_MD_TABLE_RE = re.compile(r"(?m)^\s*\|.+\|\s*$")


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


def normalize_hollow_addenda_content(content: str, *, title: str = "") -> tuple[str, bool]:
    """Replace tag-only / broken addenda stubs with a clean acknowledgment table.

    Writes real content first; keeps one MANUAL FILL only for portal confirmation
    when KB/RFP does not state issued addenda.
    """
    body = content or ""
    title_cf = (title or "").casefold()
    if "addend" not in title_cf and "addend" not in body[:500].casefold():
        return body, False
    if "None issued / none received" in body:
        updated, changed = normalize_addenda_handoff_tables(body)
        return updated, changed

    updated, changed = normalize_addenda_handoff_tables(body)
    if changed:
        return updated, True

    words = len(body.split())
    mfill = len(re.findall(r"\[MANUAL\s+FILL\b", body, re.I))
    broken_nested = bool(re.search(r"\[MANUAL\s+FILL:[^\]]*\[", body, re.I))
    has_real_ack = bool(
        re.search(
            r"(?i)(none issued|no addenda|acknowledged\s+all|addendum\s+no\.?\s*\d)",
            body,
        )
    )
    has_ack_table = bool(re.search(r"(?i)addendum\s+number", body)) and "|" in body
    if has_ack_table and has_real_ack and not broken_nested:
        return body, False
    # Hollow: tiny stub, tag-heavy, or nested-bracket corruption — no real ack.
    if (
        broken_nested
        or words < 70
        or (mfill >= 1 and words < 140 and not has_real_ack)
        or (mfill >= 2 and not has_real_ack)
    ):
        return _CLEAN_ADDENDA.strip() + "\n", True
    return body, False


def ensure_table_when_designer_note_promises_one(content: str) -> tuple[str, bool]:
    """If a DESIGNER NOTE promises a table/matrix but body has no ``|`` table, add one.

    Keeps the note as a layout supplement. Does not invent RFP facts — one
    MANUAL FILL row flags fields KB did not supply.
    """
    body = content or ""
    if not body.strip():
        return body, False
    if _HAS_MD_TABLE_RE.search(body):
        return body, False
    notes = list(_DESIGNER_NOTE_TAG_RE.finditer(body))
    promising = [m for m in notes if _TABLEISH_NOTE_RE.search(m.group(1) or "")]
    if not promising:
        return body, False
    first = promising[0]
    stub = (
        "| Item | Detail |\n"
        "| --- | --- |\n"
        "| [MANUAL FILL: Sonja — row from this tab's RFP ask / work plan] | "
        "[MANUAL FILL: Sonja — concrete answer; leave flag only if KB has nothing] |\n\n"
    )
    new_body = body[: first.start()] + stub + body[first.start() :]
    return new_body, True


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
    """Deterministic repair for hollow references + addenda + designer-note gaps."""
    from datetime import datetime, timezone

    from app.models.proposal import ProposalSection
    from app.services.proposal_manual_flags import (
        sanitize_bare_bracket_tag_words,
        sanitize_nested_brackets_in_handoff_tags,
    )
    from app.services.proposal_outline_dedup import humanize_outline_title

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
        new_title = title

        cleaned_title = humanize_outline_title(title) if title else ""
        if cleaned_title and cleaned_title != title:
            new_title = cleaned_title
            logs.append(f"{title or section.id}: scrubbed TOC leader noise from title")

        if updated.strip():
            nested = sanitize_nested_brackets_in_handoff_tags(updated)
            nested = sanitize_bare_bracket_tag_words(nested)
            if nested != updated:
                logs.append(f"{new_title or section.id}: sanitized nested handoff brackets")
                updated = nested

        if "addend" in title_cf or "addend" in (new_title or "").casefold() or "addend" in body[:300].casefold():
            updated, did = normalize_hollow_addenda_content(updated, title=new_title or title)
            if did:
                logs.append(f"{new_title or section.id}: cleaned hollow/addenda MANUAL FILL stub")

        if "reference" in title_cf or "reference" in (new_title or "").casefold():
            updated, did = repair_hollow_references_section(updated, title=new_title or title)
            if did:
                logs.append(f"{new_title or section.id}: hollow references → Sonja handoff")

        updated, did_table = ensure_table_when_designer_note_promises_one(updated)
        if did_table:
            logs.append(
                f"{new_title or section.id}: designer note promised table — inserted markdown table shell"
            )

        if updated != body or new_title != title:
            changed = True
            sections.append(
                section.model_copy(update={"content": updated, "title": new_title})
            )
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
