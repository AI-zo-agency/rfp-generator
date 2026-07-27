"""Generic markdown-table column ops for proposal chat (any column — not per-field helpers).

Handles: remove / add / rename / fill a named column when the user says so in chat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.models.proposal import ProposalDraft, ProposalSection

TableColumnAction = Literal["remove", "add", "rename", "fill"]


@dataclass(frozen=True)
class TableColumnAsk:
    action: TableColumnAction
    column: str
    new_column: str | None = None
    fill_value: str | None = None


_ASK_RE = re.compile(
    r"(?is)"
    r"\b(?P<action>remove|drop|delete|omit|add|insert|create|rename|relabel|fill|set)\b"
    r".{0,60}?"
    r"(?:the\s+)?"
    r"(?P<col>[A-Za-z][A-Za-z0-9 /\-]{1,48}?)"
    r"\s+column\b"
    r"(?:.{0,40}?\b(?:to|as|with|→|->)\s+"
    r"(?P<to>[A-Za-z0-9\[\]: /\-]{1,60}))?",
)

_ASK_ALT_RE = re.compile(
    r"(?is)"
    r"\b(?P<col>[A-Za-z][A-Za-z0-9 /\-]{1,48}?)"
    r"\s+column\b.{0,24}?"
    r"\b(?P<action>remove|drop|delete|omit|add|rename|fill)\b"
    r"(?:.{0,40}?\b(?:to|as|with)\s+(?P<to>[A-Za-z0-9\[\]: /\-]{1,60}))?",
)


def _clean_col_name(raw: str) -> str:
    text = (raw or "").strip(" \t\"'`.,;:!")
    text = re.sub(r"(?i)^(the|a|an)\s+", "", text).strip()
    text = re.sub(r"(?i)\s+only$", "", text).strip()
    return text


def parse_table_column_ask(text: str) -> TableColumnAsk | None:
    """Parse 'remove/add/rename/fill <Name> column …' — column name is whatever the user said."""
    raw = (text or "").strip()
    if not raw or "column" not in raw.casefold():
        return None

    match = _ASK_RE.search(raw) or _ASK_ALT_RE.search(raw)
    if not match:
        return None

    action_raw = (match.group("action") or "").casefold()
    column = _clean_col_name(match.group("col") or "")
    if not column or column.casefold() in {"the", "this", "that", "a", "an"}:
        return None

    to_raw = _clean_col_name(match.groupdict().get("to") or "")

    if action_raw in {"remove", "drop", "delete", "omit"}:
        return TableColumnAsk(action="remove", column=column)
    if action_raw in {"add", "insert", "create"}:
        return TableColumnAsk(
            action="add",
            column=column,
            fill_value=to_raw or "[VERIFY: value]",
        )
    if action_raw in {"rename", "relabel"}:
        if not to_raw:
            return None
        return TableColumnAsk(action="rename", column=column, new_column=to_raw)
    if action_raw in {"fill", "set"}:
        if not to_raw:
            return None
        return TableColumnAsk(action="fill", column=column, fill_value=to_raw)
    return None


def _norm_header(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").casefold())


def _header_match_score(header: str, column: str) -> int:
    h = _norm_header(header)
    c = _norm_header(column)
    if not h or not c:
        return 0
    if h == c:
        return 100
    if c in h or h in c:
        return 80
    h_tokens = set(re.findall(r"[a-z0-9]+", (header or "").casefold()))
    c_tokens = set(re.findall(r"[a-z0-9]+", (column or "").casefold()))
    if not c_tokens:
        return 0
    overlap = len(h_tokens & c_tokens) / len(c_tokens)
    if overlap >= 1.0:
        return 70
    if overlap >= 0.5:
        return 40
    return 0


def _split_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    return [c.strip() for c in stripped.strip("|").split("|")]


def _join_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _is_separator_row(cells: list[str]) -> bool:
    if not cells:
        return False
    return all(
        bool(re.fullmatch(r":?-{3,}:?", (c or "").replace(" ", ""))) for c in cells
    )


def _iter_tables(
    lines: list[str],
) -> list[tuple[int, int, list[list[str]]]]:
    """Return list of (start_line, end_line_exclusive, rows_as_cells)."""
    tables: list[tuple[int, int, list[list[str]]]] = []
    i = 0
    while i < len(lines):
        cells = _split_row(lines[i])
        if cells is None:
            i += 1
            continue
        start = i
        rows = [cells]
        j = i + 1
        while j < len(lines):
            nxt = _split_row(lines[j])
            if nxt is None:
                break
            rows.append(nxt)
            j += 1
        if len(rows) >= 2:
            tables.append((start, j, rows))
        i = j if j > i else i + 1
    return tables


def find_section_with_table_column(
    draft: ProposalDraft,
    column: str,
    *,
    focus: ProposalSection | None = None,
    prefer_any_table: bool = False,
) -> ProposalSection | None:
    """Pick the section whose markdown table best matches the named column."""
    scored: list[tuple[int, ProposalSection]] = []
    for section in draft.sections:
        body = section.content or ""
        if "|" not in body:
            continue
        lines = body.splitlines()
        best = 0
        has_table = False
        for start, end, rows in _iter_tables(lines):
            del start, end
            has_table = True
            if not rows:
                continue
            for cell in rows[0]:
                best = max(best, _header_match_score(cell, column))
        if best <= 0 and not (prefer_any_table and has_table):
            continue
        score = best if best > 0 else (10 if prefer_any_table and has_table else 0)
        if score <= 0:
            continue
        if focus is not None and section.id == focus.id:
            score += 15
        title = (section.title or "").casefold()
        if section.id.startswith("section-2-bio"):
            score -= 25
        if any(k in title for k in ("team", "staff", "personnel", "qualification", "budget")):
            score += 5
        scored.append((score, section))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def apply_table_column_ask(content: str, ask: TableColumnAsk) -> tuple[str, int, str]:
    """Apply remove/add/rename/fill to the first matching markdown table.

    Returns (updated_content, rows_touched, human_label_of_column).
    """
    lines = list((content or "").splitlines(keepends=False))
    tables = _iter_tables(lines)
    if not tables:
        return content or "", 0, ask.column

    target_idx = -1
    col_idx = -1
    matched_header = ask.column

    for ti, (start, end, rows) in enumerate(tables):
        del start, end
        header = rows[0]
        if ask.action == "add":
            # Prefer a table that does NOT already have this column; else first table.
            scores = [_header_match_score(c, ask.column) for c in header]
            if scores and max(scores) >= 80:
                continue  # already present — try next table
            target_idx = ti
            col_idx = len(header)  # append
            matched_header = ask.column
            break

        scores = [_header_match_score(c, ask.column) for c in header]
        best = max(range(len(scores)), key=lambda i: scores[i]) if scores else -1
        if best >= 0 and scores[best] >= 40:
            target_idx = ti
            col_idx = best
            matched_header = header[best]
            break

    if ask.action == "add" and target_idx < 0 and tables:
        # Every table already had the column, or none matched — use first table append
        # only if column missing on first.
        start, end, rows = tables[0]
        del start, end
        scores = [_header_match_score(c, ask.column) for c in rows[0]]
        if not scores or max(scores) < 80:
            target_idx = 0
            col_idx = len(rows[0])
            matched_header = ask.column

    if target_idx < 0 or col_idx < 0:
        return content or "", 0, ask.column

    start, end, rows = tables[target_idx]
    new_rows: list[list[str]] = []
    touched = 0

    for r_i, row in enumerate(rows):
        row = list(row)
        if ask.action == "remove":
            if len(row) > col_idx:
                row = row[:col_idx] + row[col_idx + 1 :]
                touched += 1
        elif ask.action == "rename":
            if r_i == 0 and ask.new_column and len(row) > col_idx:
                row[col_idx] = ask.new_column
                touched += 1
                matched_header = ask.new_column
        elif ask.action == "fill":
            if r_i > 0 and not _is_separator_row(row) and ask.fill_value is not None:
                if len(row) > col_idx:
                    row[col_idx] = ask.fill_value
                    touched += 1
        elif ask.action == "add":
            if r_i == 0:
                row.insert(col_idx, ask.column)
                touched += 1
            elif _is_separator_row(row):
                row.insert(col_idx, "---")
                touched += 1
            else:
                row.insert(col_idx, ask.fill_value or "[VERIFY: value]")
                touched += 1
        new_rows.append(row)

    new_lines = lines[:start] + [_join_row(r) for r in new_rows] + lines[end:]
    text = "\n".join(new_lines)

    if ask.action == "remove":
        pat = re.escape(ask.column).replace(r"\ ", r"[\s\-]*")
        text, _ = re.subn(
            rf"(?im)^[^\n]*{pat}[^\n]*commitments?[^\n]*\n?",
            "",
            text,
        )

    return text, touched, matched_header
