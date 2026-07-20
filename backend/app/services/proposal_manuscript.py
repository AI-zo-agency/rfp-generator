"""Ordered full-proposal manuscript for export (matches workspace section order)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.proposal import ProposalSection

SECTION_1_ID_ORDER: tuple[str, ...] = (
    "section-1-who-we-are",
    "section-1-org-structure",
    "section-1-business-info",
    "section-1-certifications",
    "section-1-insurance",
    "section-1-company-overview",
)

_PLACEHOLDER_IDS = frozenset(
    {
        "section-2-bio-placeholder",
        "section-3-work-placeholder",
    }
)

_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:\-–—]+\|[\s|:\-–—]+\|?\s*$")


def _parse_title_major_minor(title: str) -> tuple[int, int]:
    m = re.match(r"^\s*(\d+)\.(\d+)", title or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    return 999, 999


def manuscript_rank(section: "ProposalSection") -> tuple:
    sid = section.id
    major, minor = _parse_title_major_minor(section.title)

    if sid.startswith("section-1-"):
        try:
            idx = SECTION_1_ID_ORDER.index(sid)  # type: ignore[arg-type]
        except ValueError:
            idx = 40 + minor
        return (1, idx, minor, sid)
    if sid.startswith("section-2-bio-") or sid == "section-2-team-overview":
        return (2, minor, 0, sid)
    if sid.startswith("section-3-work-") or sid == "section-3-our-work":
        return (3, minor, 0, sid)
    if sid.startswith("section-4-"):
        return (4, major, minor, sid)
    if sid.startswith("section-5-"):
        return (5, major, minor, sid)
    if section.source == "rfp" or sid.startswith("rfp-"):
        return (6, major, minor, sid)
    return (7, major, minor, sid)


def manuscript_sections_for_export(sections: list["ProposalSection"]) -> list["ProposalSection"]:
    """All non-placeholder sections with body text, in proposal reading order."""
    out: list[ProposalSection] = []
    for section in sections:
        if section.id in _PLACEHOLDER_IDS:
            continue
        if not (section.content or "").strip():
            continue
        out.append(section)
    return sorted(out, key=manuscript_rank)


def plain_text_for_export(markdown: str) -> str:
    """Strip markdown markers for plain Google Docs text (keep list markers)."""
    text = markdown or ""
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Internal evidence markers ([E1], [E2], …) — not for client-facing copy
    text = re.sub(r"\s*\[E\d+\]", "", text)
    return text.strip()


def _strip_inline_md(text: str) -> str:
    """Remove bold/italic/code markers but keep the words."""
    t = text or ""
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"\s*\[E\d+\]", "", t)
    return t.strip()


def _is_table_row(line: str) -> bool:
    trimmed = line.strip()
    if "|" not in trimmed:
        return False
    cells = [c for c in trimmed.strip("|").split("|")]
    return len(cells) >= 2


def _is_table_separator(line: str) -> bool:
    return bool(_TABLE_SEP_RE.match(line.strip()))


def _parse_table_row(line: str) -> list[str]:
    return [_strip_inline_md(cell) for cell in line.strip().strip("|").split("|")]


def parse_markdown_parts(markdown: str) -> list[dict[str, Any]]:
    """Split markdown into heading / paragraph / list / table parts for Docs export."""
    lines = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    parts: list[dict[str, Any]] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        trimmed = line.strip()

        if not trimmed:
            i += 1
            continue

        heading = re.match(r"^(#{1,4})\s+(.+)$", trimmed)
        if heading:
            level = min(len(heading.group(1)), 3)
            parts.append(
                {
                    "type": "heading",
                    "level": level,
                    "text": _strip_inline_md(heading.group(2)),
                }
            )
            i += 1
            continue

        if _is_table_row(trimmed):
            table_lines: list[str] = []
            while i < len(lines) and _is_table_row(lines[i].strip()):
                table_lines.append(lines[i].strip())
                i += 1
            data_lines = [row for row in table_lines if not _is_table_separator(row)]
            if data_lines:
                headers = _parse_table_row(data_lines[0])
                rows = [_parse_table_row(row) for row in data_lines[1:]]
                width = max(len(headers), 1)
                headers = (headers + [""] * width)[:width]
                norm_rows = [(row + [""] * width)[:width] for row in rows]
                parts.append({"type": "table", "headers": headers, "rows": norm_rows})
            continue

        if re.match(r"^[-*]\s+", trimmed) or re.match(r"^\d+\.\s+", trimmed):
            ordered = bool(re.match(r"^\d+\.\s+", trimmed))
            items: list[str] = []
            while i < len(lines):
                cur = lines[i].strip()
                if not cur:
                    break
                if ordered:
                    m = re.match(r"^\d+\.\s+(.+)$", cur)
                    if not m:
                        break
                    items.append(_strip_inline_md(m.group(1)))
                elif re.match(r"^[-*]\s+", cur):
                    items.append(_strip_inline_md(re.sub(r"^[-*]\s+", "", cur)))
                else:
                    break
                i += 1
            if items:
                parts.append({"type": "list", "ordered": ordered, "items": items})
            continue

        para_lines: list[str] = []
        while i < len(lines):
            cur = lines[i]
            cur_trim = cur.strip()
            if not cur_trim:
                break
            if (
                re.match(r"^#{1,4}\s+", cur_trim)
                or _is_table_row(cur_trim)
                or re.match(r"^[-*]\s+", cur_trim)
                or re.match(r"^\d+\.\s+", cur_trim)
            ):
                break
            para_lines.append(cur_trim)
            i += 1
        text = _strip_inline_md(" ".join(para_lines))
        if text:
            designer = re.match(
                r"^\[(?:DESIGNER NOTE|Designer Note)\s*:?\s*(.*)\]\s*$",
                text,
                re.I | re.S,
            )
            if designer:
                parts.append(
                    {
                        "type": "designer_note",
                        "text": designer.group(1).strip(),
                    }
                )
            else:
                parts.append({"type": "paragraph", "text": text})

    return parts


def build_manuscript_blocks(
    sections: list["ProposalSection"],
) -> list[tuple[str, str]]:
    """Legacy plain blocks (title, body text). Prefer build_manuscript_structured."""
    blocks: list[tuple[str, str]] = []
    for section in manuscript_sections_for_export(sections):
        title = (section.title or "Untitled section").strip()
        body = plain_text_for_export(section.content or "")
        blocks.append((title, body))
    return blocks


def build_manuscript_structured(
    sections: list["ProposalSection"],
) -> list[dict[str, Any]]:
    """Section title + ordered text/table parts for Google Doc export."""
    out: list[dict[str, Any]] = []
    for section in manuscript_sections_for_export(sections):
        title = (section.title or "Untitled section").strip()
        parts = parse_markdown_parts(section.content or "")
        out.append({"title": title, "parts": parts})
    return out


def build_manuscript_plain_text(sections: list["ProposalSection"]) -> str:
    parts: list[str] = []
    for title, body in build_manuscript_blocks(sections):
        parts.append(f"{title}\n\n{body}")
    return "\n\n—\n\n".join(parts)


def _format_table_plain(headers: list[str], rows: list[list[str]]) -> str:
    cols = max(len(headers), 1)
    hdr = (headers + [""] * cols)[:cols]
    lines = [" | ".join(hdr)]
    for row in rows:
        padded = (list(row) + [""] * cols)[:cols]
        lines.append(" | ".join(padded))
    return "\n".join(lines)


def build_google_doc_bulk_export(
    doc_title: str,
    sections: list[dict[str, Any]],
) -> tuple[str, list[tuple[int, int, int, bool]]]:
    """
    Single string for insertText plus style spans: (start, end, heading_level, bold).
    Indices are 0-based in the returned text; map to Docs with insertIndex + start.
    Tables are plain pipe-separated lines to avoid expensive table API calls.
    """
    pieces: list[str] = []
    spans: list[tuple[int, int, int, bool, bool]] = []
    pos = 0

    def add_line(line: str, *, heading: int | None = None, bold: bool = False) -> None:
        nonlocal pos
        block = line + "\n"
        start = pos
        pieces.append(block)
        pos += len(block)
        if heading and line.strip():
            spans.append((start, pos, heading, bold, False))
        elif bold and line.strip():
            spans.append((start, pos, 0, True, False))

    add_line(doc_title.strip(), heading=1, bold=True)
    add_line("")

    for section in sections:
        title = (section.get("title") or "Untitled").strip()
        add_line(title, heading=1)

        for part in section.get("parts") or []:
            ptype = part.get("type")
            if ptype == "heading":
                text = (part.get("text") or "").strip()
                if text:
                    # Plain line — styling every subheading exceeds Docs write quota.
                    add_line(text)
                continue
            if ptype == "table":
                headers = part.get("headers") or []
                rows = part.get("rows") or []
                if headers:
                    tbl = _format_table_plain(headers, rows)
                    for tbl_line in tbl.split("\n"):
                        add_line(tbl_line)
                    add_line("")
                continue
            if ptype == "list":
                items = part.get("items") or []
                ordered = bool(part.get("ordered"))
                for i, item in enumerate(items):
                    prefix = f"{i + 1}. " if ordered else "• "
                    add_line(f"{prefix}{(item or '').strip()}")
                continue
            text = (part.get("text") or "").strip()
            if text:
                for chunk in re.split(r"\n{2,}", text):
                    chunk = chunk.strip()
                    if chunk:
                        add_line(chunk)

        add_line("")

    return "".join(pieces), spans


def build_google_doc_export_blocks(
    doc_title: str,
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Ordered blocks for Docs export: {"kind": "text", "text", "spans"} or
    {"kind": "table", "headers", "rows"}. Spans are 0-based within each text block.
    """
    blocks: list[dict[str, Any]] = []
    pieces: list[str] = []
    spans: list[tuple[int, int, int, bool, bool]] = []
    pos = 0

    def flush_text() -> None:
        nonlocal pieces, spans, pos
        if not pieces:
            return
        blocks.append(
            {
                "kind": "text",
                "text": "".join(pieces),
                "spans": list(spans),
            }
        )
        pieces = []
        spans = []
        pos = 0

    def add_line(
        line: str,
        *,
        heading: int | None = None,
        bold: bool = False,
        italic: bool = False,
    ) -> None:
        nonlocal pos
        block = line + "\n"
        start = pos
        pieces.append(block)
        pos += len(block)
        if heading and line.strip():
            spans.append((start, pos, heading, bold, italic))
        elif bold or italic:
            if line.strip():
                spans.append((start, pos, 0, bold, italic))

    add_line(doc_title.strip(), heading=1, bold=True)
    add_line("")

    for section in sections:
        title = (section.get("title") or "Untitled").strip()
        add_line(title, heading=2)

        for part in section.get("parts") or []:
            ptype = part.get("type")
            if ptype == "heading":
                text = (part.get("text") or "").strip()
                if text:
                    level = int(part.get("level") or 2)
                    mapped = min(max(level + 1, 2), 3)
                    add_line(text, heading=mapped)
                continue
            if ptype == "designer_note":
                note = (part.get("text") or "").strip()
                if note:
                    add_line(f"Designer note: {note}", italic=True)
                    add_line("")
                continue
            if ptype == "table":
                headers = part.get("headers") or []
                rows = part.get("rows") or []
                if headers:
                    tbl = _format_table_plain(headers, rows)
                    for tbl_line in tbl.split("\n"):
                        add_line(tbl_line)
                    add_line("")
                continue
            if ptype == "list":
                items = part.get("items") or []
                ordered = bool(part.get("ordered"))
                for i, item in enumerate(items):
                    prefix = f"{i + 1}. " if ordered else "• "
                    add_line(f"{prefix}{(item or '').strip()}")
                continue
            text = (part.get("text") or "").strip()
            if text:
                for chunk in re.split(r"\n{2,}", text):
                    chunk = chunk.strip()
                    if chunk:
                        add_line(chunk)

        add_line("")

    flush_text()
    return blocks
