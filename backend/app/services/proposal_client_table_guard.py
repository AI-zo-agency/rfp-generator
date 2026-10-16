"""Collapse a client-reference table that is mostly unverified cells.

HomeFront Tab 7 shipped an eight-row, six-column matrix. Every row left phone, dates, or the contact itself as
``[MANUAL FILL]``. The RFP asked for a client list and a short narrative.
A table where more than half the rows are unfinished is not that list.
"""

from __future__ import annotations

import re

_MANUAL = re.compile(r"\[MANUAL FILL:[^\]]*\]", re.I)
_ROW_SPLIT = re.compile(r"\n")

_GAP_SENTENCE = (
    "The knowledge base does not have a complete contact row "
    "(name, phone, email, scope, and dates) for every client named here. "
    "[MANUAL FILL: Sonja — add verified client name, phone, email, scope, and dates before submit.]"
)


def _is_client_header(cells: list[str]) -> bool:
    joined = " ".join(cells).casefold()
    if "client" not in joined:
        return False
    return any(token in joined for token in ("contact", "phone", "email", "scope"))


def _split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(line: str) -> bool:
    compact = line.replace("|", "").replace("-", "").replace(":", "").replace(" ", "")
    return not compact


def _cell_incomplete(cell: str) -> bool:
    text = (cell or "").strip()
    if not text or text in {"—", "-", "n/a", "N/A"}:
        return True
    return bool(_MANUAL.search(text))


def _collapse_table(block: str) -> tuple[str, bool]:
    lines = [ln for ln in block.splitlines() if ln.strip()]
    if len(lines) < 3:
        return block, False
    header = _split_row(lines[0])
    if not _is_client_header(header):
        return block, False
    if not _is_separator(lines[1]):
        return block, False
    data = lines[2:]
    cells = [_split_row(row) for row in data]
    if not cells:
        return block, False
    dirty_rows = sum(1 for row in cells if any(_cell_incomplete(cell) for cell in row))
    # HomeFront's client matrix was exactly half manual-fill cells and every
    # row was unfinished. Collapse when more than half the rows have a gap.
    if dirty_rows * 2 <= len(cells):
        return block, False
    kept = [row for row, parsed in zip(data, cells) if parsed and not any(_cell_incomplete(c) for c in parsed)]
    header_line = lines[0]
    sep_line = lines[1]
    if kept:
        table = "\n".join([header_line, sep_line, *kept])
        return f"{table}\n\n{_GAP_SENTENCE}", True
    return _GAP_SENTENCE, True


def collapse_incomplete_client_tables(content: str) -> tuple[str, bool]:
    """Return ``(text, changed)``. Non-client tables are left as written."""
    if not content or "|" not in content:
        return content, False
    lines = content.splitlines()
    out: list[str] = []
    changed = False
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|"):
            start = i
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                i += 1
            block = "\n".join(lines[start:i])
            replacement, did = _collapse_table(block)
            changed = changed or did
            out.append(replacement)
            continue
        out.append(lines[i])
        i += 1
    if not changed:
        return content, False
    return "\n".join(out), True
