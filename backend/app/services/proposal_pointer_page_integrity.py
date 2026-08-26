"""Pointer / cover-page integrity for Technical Proposal-style tabs.

After Improve-full-section (or Scan compact) leaves a cross-ref table:

1. Remap "Addressed In" cells to the real manuscript marks (§21, §22, …)
   from the live TOC — never invent "Section 3" when §3 is Our Work cards.
2. Execute ``EDITOR NOTES / INSERT INTO §N`` blocks into those tabs, then
   delete the notes from the pointer page so internal work tickets never ship.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.proposal import ProposalDraft, ProposalSection

_ADDRESSED_IN_HEADER_RE = re.compile(
    r"(?im)^\s*\|[^\n]*\b(?:RFP\s+)?Requirement\b[^\n]*\|[^\n]*\bAddressed\s+In\b[^\n]*\|\s*$"
)

_EDITOR_NOTES_START_RE = re.compile(
    r"(?:#{1,4}\s*)?(?:\*\*)?EDITOR\s+NOTES?\b|"
    r">\s*\*\*\[?DESIGNER\s+NOTE:|"
    r"(?:#{1,4}\s*)?INSERTS?\s+REQUIRED\b",
    re.I | re.M,
)

_INSERT_INTO_RE = re.compile(
    r"(?im)^\*{0,2}INSERT\s+INTO\s+(?:§\s*|sec(?:tion)?\.?\s*)?(\d+(?:\.\d+)?)[^\n]*$"
)

_TABLE_ROW_RE = re.compile(r"^\s*\|")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|\-]+\|\s*$")


@dataclass(frozen=True)
class _TocEntry:
    section: ProposalSection
    mark: str
    title: str
    tokens: frozenset[str]


_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "section",
        "part",
        "our",
        "of",
        "to",
        "a",
        "an",
        "or",
        "in",
        "on",
        "at",
        "by",
        "as",
        "is",
        "are",
        "this",
        "that",
        "proposed",
        "project",
        "work",
        "plan",
        "samples",
        "form",
        "attached",
        "cover",
        "under",
        "separate",
    }
)

def manuscript_section_mark(title: str) -> str | None:
    """``21. Experience — …`` → ``21``; ``3.1 — Oregon`` → ``3.1``."""
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*[.:—–\-\)]", title or "")
    return match.group(1) if match else None


def _tokens(text: str) -> set[str]:
    """Significant tokens from a title/requirement — no topic keyword tables.

    Length floor is 3 so short but real words (fee, bio) participate; stopwords
    alone are dropped. No domain allowlists.
    """
    return {
        t
        for t in re.findall(r"[a-z0-9]{3,}", (text or "").casefold())
        if t not in _STOP
    }

def _short_title(title: str) -> str:
    raw = (title or "").strip()
    raw = re.sub(r"^\s*\d+(?:\.\d+)?\s*[.:—–\-\)]\s*", "", raw).strip()
    if "—" in raw:
        raw = raw.split("—", 1)[0].strip()
    if "–" in raw:
        raw = raw.split("–", 1)[0].strip()
    return raw[:80] or (title or "").strip()[:80]


def _toc_entries(draft: ProposalDraft) -> list[_TocEntry]:
    out: list[_TocEntry] = []
    for section in draft.sections:
        title = section.title or ""
        mark = manuscript_section_mark(title)
        if not mark:
            # Static 1.1 / 2.1 style already captured; bare titles skip.
            continue
        toks = _tokens(title)
        if not toks:
            continue
        out.append(
            _TocEntry(
                section=section,
                mark=mark,
                title=title,
                tokens=frozenset(toks),
            )
        )
    return out


def _score_requirement_to_entry(requirement: str, entry: _TocEntry) -> float:
    """Overlap score only — no topic keyword / synonym tables."""
    req = _tokens(requirement)
    if not req:
        return 0.0
    shared = req & set(entry.tokens)
    if not shared:
        return 0.0
    # Jaccard-ish: shared over union, plus recall against the requirement.
    union = req | set(entry.tokens)
    score = (len(shared) / max(len(union), 1)) + (
        0.5 * len(shared) / max(len(req), 1)
    )
    # Structural preference (ids), not topic words: dedicated RFP tabs beat
    # per-person bios / Section 3 case cards for whole-topic requirements.
    sid = entry.section.id or ""
    if sid.startswith("section-2-bio-") or sid.startswith("section-3-work-"):
        score *= 0.35
    # Prefer integer manuscript marks (21) over dotted Our Work (3.1).
    if "." not in entry.mark and score >= 0.2:
        score += 0.12
    return score


def resolve_addressed_in_target(
    draft: ProposalDraft,
    requirement: str,
    *,
    self_section_id: str | None = None,
) -> _TocEntry | None:
    """Best live TOC tab for a cross-ref requirement cell."""
    best: _TocEntry | None = None
    best_score = 0.0
    for entry in _toc_entries(draft):
        if self_section_id and entry.section.id == self_section_id:
            continue
        score = _score_requirement_to_entry(requirement, entry)
        if score > best_score:
            best = entry
            best_score = score
    if best is None or best_score < 0.28:
        return None
    return best


def format_addressed_in_cell(entry: _TocEntry) -> str:
    return f"§{entry.mark} ({_short_title(entry.title)})"


def format_see_pointer(entry: _TocEntry) -> str:
    """Canonical cross-section pointer with live § mark."""
    return f"See **§{entry.mark} ({_short_title(entry.title)})**"


def format_see_pointer_for_title(title: str) -> str:
    """Build ``See **§N (short)**`` when the title carries a manuscript mark."""
    home = (title or "").strip()
    mark = manuscript_section_mark(home)
    if not mark:
        return f"See **{home or 'the overlapping section'}**"
    return f"See **§{mark} ({_short_title(home)})**"


# Free-prose citations: "Section 3 (Experience…)", "See Section 6", "§4 (Approach)".
_PROSE_CITATION_RE = re.compile(
    r"(?i)\b((?:See\s+|As\s+(?:detailed|described|shown|noted)\s+in\s+|under\s+)?)"
    r"(?:§\s*|Section\s+)(\d+(?:\.\d+)?)"
    r"(?:\s*\(([^)]{3,140})\))?"
)

# Sibling-compress style: See **21. Experience — …** / See **Experience…**
_SEE_BOLD_TITLE_RE = re.compile(r"(?i)\bSee\s+\*\*([^*]{3,140})\*\*")


def _toc_entry_for_section(section: ProposalSection) -> _TocEntry | None:
    mark = manuscript_section_mark(section.title or "")
    if not mark:
        return None
    toks = _tokens(section.title or "")
    if not toks:
        return None
    return _TocEntry(
        section=section,
        mark=mark,
        title=section.title or "",
        tokens=frozenset(toks),
    )


def rewrite_prose_section_citations(
    content: str,
    draft: ProposalDraft,
    *,
    self_section_id: str | None = None,
) -> tuple[str, int, list[str]]:
    """Remap free-prose Section-N / See **title** pointers to live TOC § marks."""
    body = content or ""
    if not body.strip():
        return body, 0, []

    logs: list[str] = []
    changed = 0

    def _replace_citation(match: re.Match[str]) -> str:
        nonlocal changed
        prefix = match.group(1) or ""
        cited_mark = match.group(2)
        paren = (match.group(3) or "").strip()
        # Prefer parenthetical topic text — that is how wrong "Section 3
        # (Experience…)" gets remapped to §21.
        query = paren or f"Section {cited_mark}"
        entry = None
        if paren:
            entry = resolve_addressed_in_target(
                draft, paren, self_section_id=self_section_id
            )
        if entry is None:
            hit = _find_section_by_mark(draft, cited_mark)
            if hit is not None and (
                self_section_id is None or hit.id != self_section_id
            ):
                entry = _toc_entry_for_section(hit)
        if entry is None:
            logs.append(
                f"unresolved prose citation: {match.group(0).strip()[:100]}"
            )
            return match.group(0)
        # Already correct live mark with matching paren short-title — keep.
        if entry.mark == cited_mark and (
            not paren or _short_title(entry.title).casefold() in paren.casefold()
        ):
            # Normalize "Section N" → "§N (short)" for consistency.
            if match.group(0).startswith("§") and paren:
                return match.group(0)
        cell = format_addressed_in_cell(entry)
        changed += 1
        # Preserve "See " / "As detailed in " prefixes; drop bare "Section ".
        if prefix.strip():
            return f"{prefix}{cell}"
        return cell

    body = _PROSE_CITATION_RE.sub(_replace_citation, body)

    def _replace_see_bold(match: re.Match[str]) -> str:
        nonlocal changed
        title_blob = (match.group(1) or "").strip()
        # Already §-prefixed inside bold.
        if re.match(r"^§\s*\d", title_blob):
            return match.group(0)
        mark = manuscript_section_mark(title_blob)
        entry = None
        if mark:
            hit = _find_section_by_mark(draft, mark)
            if hit is not None:
                entry = _toc_entry_for_section(hit)
        if entry is None:
            entry = resolve_addressed_in_target(
                draft, title_blob, self_section_id=self_section_id
            )
        if entry is None:
            # Exact title match against TOC (full or short).
            needle = title_blob.casefold()
            for toc in _toc_entries(draft):
                if self_section_id and toc.section.id == self_section_id:
                    continue
                if (
                    toc.title.casefold() == needle
                    or _short_title(toc.title).casefold() == needle
                ):
                    entry = toc
                    break
        if entry is None:
            return match.group(0)
        changed += 1
        return format_see_pointer(entry)

    body = _SEE_BOLD_TITLE_RE.sub(_replace_see_bold, body)
    return body, changed, logs


def rewrite_prose_section_citations_in_draft(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, list[str]]:
    """Sweep every section for prose / bold See-pointers to live § marks."""
    logs: list[str] = []
    sections = list(draft.sections)
    changed_any = False
    for i, section in enumerate(sections):
        body = section.content or ""
        if not body.strip():
            continue
        # Skip if neither prose citation nor See **…** appears.
        if not (
            re.search(r"(?i)(?:§\s*|Section\s+)\d", body)
            or re.search(r"(?i)See\s+\*\*", body)
        ):
            continue
        rewritten, n, unresolved = rewrite_prose_section_citations(
            body, draft, self_section_id=section.id
        )
        label = _short_title(section.title or section.id)
        for line in unresolved:
            logs.append(f"{label}: {line}")
        if n and rewritten != body:
            sections[i] = section.model_copy(update={"content": rewritten})
            changed_any = True
            logs.append(f"{label}: remapped {n} prose cross-reference(s)")
    if not changed_any and not logs:
        return draft, []
    if not changed_any:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs


def rewrite_cross_ref_addressed_in_table(
    content: str,
    draft: ProposalDraft,
    *,
    self_section_id: str | None = None,
) -> tuple[str, int, list[str]]:
    """Rewrite Addressed-In cells to real § marks from the live TOC.

    Returns ``(body, remapped_count, unresolved_logs)``. Unresolved rows are
    left as-is and logged so Completeness / Scan can surface orphans.
    """
    body = content or ""
    lines = body.splitlines(keepends=True)
    header_idx = None
    for i, line in enumerate(lines):
        if _ADDRESSED_IN_HEADER_RE.match(line.rstrip("\n")):
            header_idx = i
            break
    if header_idx is None:
        return body, 0, []

    # Skip separator row
    row_start = header_idx + 1
    if row_start < len(lines) and _TABLE_SEP_RE.match(lines[row_start].rstrip("\n")):
        row_start += 1

    changed = 0
    unresolved: list[str] = []
    for i in range(row_start, len(lines)):
        raw = lines[i]
        stripped = raw.rstrip("\n")
        if not _TABLE_ROW_RE.match(stripped):
            break
        cells = [c.strip() for c in stripped.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        requirement, addressed = cells[0], cells[1]
        if not requirement or requirement.casefold() in {"rfp requirement", "requirement"}:
            continue
        # Keep multi-target static refs that already cite 1.1 / 1.3 accurately.
        if re.search(r"\b1\.\d+\b", addressed) and "who we are" in addressed.casefold():
            continue
        if re.search(r"(?i)section\s+iii\b|references?\s+form", addressed):
            continue
        # Already a live § mark that exists in the TOC — leave alone.
        live_mark = re.search(r"§\s*(\d+(?:\.\d+)?)", addressed)
        if live_mark and _find_section_by_mark(draft, live_mark.group(1)):
            continue
        entry = resolve_addressed_in_target(
            draft, requirement, self_section_id=self_section_id
        )
        if entry is None:
            unresolved.append(
                f"unresolved Addressed-In: {requirement[:100].strip()}"
            )
            continue
        new_cell = format_addressed_in_cell(entry)
        if new_cell.casefold() in addressed.casefold() and f"§{entry.mark}" in addressed:
            continue
        cells[1] = new_cell
        newline = "\n" if raw.endswith("\n") else ""
        lines[i] = "| " + " | ".join(cells) + " |" + newline
        changed += 1

    if not changed and not unresolved:
        return body, 0, []
    return "".join(lines) if changed else body, changed, unresolved


def _parse_markdown_table_block(block: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in (block or "").splitlines():
        if not _TABLE_ROW_RE.match(line):
            if rows:
                break
            continue
        if _TABLE_SEP_RE.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells:
            rows.append(cells)
    return rows


def _find_section_by_mark(draft: ProposalDraft, mark: str) -> ProposalSection | None:
    hits = [
        s
        for s in draft.sections
        if manuscript_section_mark(s.title or "") == mark
    ]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        return None
    # Prefer non-bio / non-case-study when several share a mark prefix.
    ranked = sorted(
        hits,
        key=lambda s: (
            0
            if not (s.id or "").startswith(("section-2-bio-", "section-3-work-"))
            else 1,
            -(len(s.title or "")),
        ),
    )
    return ranked[0]


def _merge_table_rows_into_section(
    section: ProposalSection,
    new_rows: list[list[str]],
    *,
    replace_blank_name: str | None = None,
) -> tuple[ProposalSection, bool]:
    """Append rows to the first markdown table, or fill a blank name cell."""
    body = section.content or ""
    if not new_rows:
        return section, False
    lines = body.splitlines(keepends=True)
    table_start = None
    for i, line in enumerate(lines):
        if _TABLE_ROW_RE.match(line):
            table_start = i
            break
    if table_start is None:
        # No table — append one.
        header = new_rows[0]
        data = new_rows[1:] if len(new_rows) > 1 else new_rows
        # If first row looks like a header (Client / Name), keep; else invent.
        if not any(h.casefold() in {"client", "name", "role"} for h in header):
            data = new_rows
            header = ["Client", "Engagement Type", "Scope Summary"][: len(new_rows[0])]
        sep = "| " + " | ".join("---" for _ in header) + " |"
        block = (
            "\n\n| "
            + " | ".join(header)
            + " |\n"
            + sep
            + "\n"
            + "\n".join("| " + " | ".join(r) + " |" for r in data)
            + "\n"
        )
        return section.model_copy(update={"content": body.rstrip() + block}), True

    # Find table end
    table_end = table_start
    for j in range(table_start, len(lines)):
        if _TABLE_ROW_RE.match(lines[j]):
            table_end = j + 1
        elif table_end > table_start:
            break

    table_lines = lines[table_start:table_end]
    # Drop header + sep from incoming if present
    incoming = list(new_rows)
    if incoming and any(
        h.casefold() in {"client", "name", "role", "engagement"} for h in incoming[0]
    ):
        incoming = incoming[1:]

    changed = False
    if replace_blank_name:
        needle = replace_blank_name.casefold()
        for idx, tline in enumerate(table_lines):
            if not _TABLE_ROW_RE.match(tline) or _TABLE_SEP_RE.match(tline):
                continue
            cells = [c.strip() for c in tline.strip().strip("|").split("|")]
            if len(cells) < 2:
                continue
            name, role = cells[0], cells[1]
            role_cf = role.casefold()
            if needle in role_cf and (not name or name in {"—", "-", "–", "n/a", ""}):
                # Use first incoming data row
                if incoming:
                    table_lines[idx] = (
                        "| " + " | ".join(incoming[0]) + " |\n"
                        if not tline.endswith("\n")
                        else "| " + " | ".join(incoming[0]) + " |\n"
                    )
                    incoming = incoming[1:]
                    changed = True
                    break

    for row in incoming:
        table_lines.append("| " + " | ".join(row) + " |\n")
        changed = True

    if not changed:
        return section, False
    new_body = "".join(lines[:table_start] + table_lines + lines[table_end:])
    return section.model_copy(update={"content": new_body}), True


def strip_editor_notes_blocks(content: str) -> tuple[str, bool]:
    """Remove EDITOR NOTES / INSERT INTO work-ticket blocks from a pointer page."""
    body = content or ""
    if not body.strip():
        return body, False
    # Cut from first EDITOR NOTES / INSERTS REQUIRED heading to EOF
    # (these are always trailing work tickets on pointer pages).
    match = re.search(
        r"(?is)\n{0,3}(?:#{1,4}\s*)?(?:\*\*)?EDITOR\s+NOTES?\b.*\Z",
        body,
    )
    if match:
        cleaned = body[: match.start()].rstrip() + "\n"
        return cleaned, True
    # Also strip standalone INSERT INTO blocks if labeled without EDITOR NOTES
    match2 = re.search(
        r"(?is)\n{0,3}\*{0,2}INSERT\s+INTO\s+(?:§|sec).*?\Z",
        body,
    )
    if match2:
        cleaned = body[: match2.start()].rstrip() + "\n"
        return cleaned, True
    return body, False


def _manual_fill_for_unapplied_insert(mark: str, rows: list[list[str]]) -> str:
    """Honest handoff when an EDITOR NOTES insert cannot be merged mechanically."""
    flat = "; ".join(
        " / ".join(c for c in row if c).strip() for row in rows[:4] if any(row)
    )
    flat = re.sub(r"\s+", " ", flat).strip()[:180]
    detail = flat or "table rows from EDITOR NOTES"
    return (
        f"[MANUAL FILL: Sonja — apply EDITOR NOTES insert for §{mark}: {detail}]"
    )


def apply_editor_notes_inserts(
    draft: ProposalDraft,
    *,
    source_section_id: str,
) -> tuple[ProposalDraft, list[str]]:
    """Apply INSERT INTO §N tables from the source tab, then strip the notes.

    Failed merges / missing targets become ``[MANUAL FILL: …]`` on the pointer
    page so content is never silently discarded when the work ticket is stripped.
    """
    logs: list[str] = []
    source = next((s for s in draft.sections if s.id == source_section_id), None)
    if source is None:
        return draft, logs
    body = source.content or ""
    if not re.search(r"(?i)EDITOR\s+NOTES?|INSERT\s+INTO", body):
        return draft, logs

    sections = list(draft.sections)
    failed_handoffs: list[str] = []
    # Walk INSERT INTO markers
    for match in _INSERT_INTO_RE.finditer(body):
        mark = match.group(1)
        # Table follows within the next ~40 lines
        after = body[match.end() : match.end() + 2500]
        rows = _parse_markdown_table_block(after)
        if len(rows) < 1:
            continue
        target = _find_section_by_mark(draft, mark)
        if target is None:
            tag = _manual_fill_for_unapplied_insert(mark, rows)
            failed_handoffs.append(tag)
            logs.append(
                f"EDITOR NOTES: no tab for §{mark} — converted to MANUAL FILL"
            )
            continue
        replace_blank = None
        blob = " ".join(" ".join(r) for r in rows).casefold()
        if "letitia" in blob or "digital media strategist" in blob:
            replace_blank = "digital media strategist"
        # Prefer the live section from `sections` (may already have prior inserts).
        live = next((s for s in sections if s.id == target.id), target)
        updated, ok = _merge_table_rows_into_section(
            live, rows, replace_blank_name=replace_blank
        )
        if not ok:
            tag = _manual_fill_for_unapplied_insert(mark, rows)
            failed_handoffs.append(tag)
            logs.append(
                f"EDITOR NOTES: could not merge insert into §{mark} — MANUAL FILL"
            )
            continue
        for i, sec in enumerate(sections):
            if sec.id == target.id:
                sections[i] = updated
                break
        logs.append(
            f"EDITOR NOTES: applied insert → §{mark} ({_short_title(target.title or '')})"
        )

    # Refresh source from sections list and strip notes; keep failed handoffs.
    for i, sec in enumerate(sections):
        if sec.id != source_section_id:
            continue
        cleaned, stripped = strip_editor_notes_blocks(sec.content or "")
        if failed_handoffs:
            existing = cleaned.casefold()
            extras = [t for t in failed_handoffs if t.casefold() not in existing]
            if extras:
                cleaned = cleaned.rstrip() + "\n\n" + "\n".join(extras) + "\n"
                logs.append(
                    f"EDITOR NOTES: kept {len(extras)} unapplied insert(s) as MANUAL FILL"
                )
        if stripped or failed_handoffs:
            sections[i] = sec.model_copy(update={"content": cleaned})
            if stripped:
                logs.append(
                    f"EDITOR NOTES: stripped work-ticket block from "
                    f"{_short_title(sec.title or sec.id)}"
                )
        break

    return draft.model_copy(update={"sections": sections}), logs


def apply_pointer_page_integrity(
    draft: ProposalDraft,
    *,
    source_section_id: str,
) -> tuple[ProposalDraft, list[str]]:
    """Fix cross-ref marks + execute/strip EDITOR NOTES on a pointer page."""
    logs: list[str] = []
    source = next((s for s in draft.sections if s.id == source_section_id), None)
    if source is None:
        return draft, logs

    body = source.content or ""
    rewritten, n, unresolved = rewrite_cross_ref_addressed_in_table(
        body, draft, self_section_id=source_section_id
    )
    sections = list(draft.sections)
    if n:
        for i, sec in enumerate(sections):
            if sec.id == source_section_id:
                sections[i] = sec.model_copy(update={"content": rewritten})
                break
        logs.append(
            f"cross-ref table: remapped {n} Addressed-In cell(s) to live § marks"
        )
        draft = draft.model_copy(update={"sections": sections})
    for line in unresolved:
        logs.append(line)

    draft, note_logs = apply_editor_notes_inserts(
        draft, source_section_id=source_section_id
    )
    logs.extend(note_logs)
    return draft, logs


def section_needs_pointer_page_integrity(section: ProposalSection) -> bool:
    """True when the body has a cross-ref table or EDITOR NOTES work ticket."""
    body = section.content or ""
    if not body.strip():
        return False
    if _ADDRESSED_IN_HEADER_RE.search(body):
        return True
    if re.search(r"(?i)EDITOR\s+NOTES?|INSERT\s+INTO\s+(?:§|sec)", body):
        return True
    return False


def apply_pointer_page_integrity_to_draft(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, list[str]]:
    """Run pointer-page integrity on every Technical Proposal / cover-style tab,
    then sweep the whole manuscript for prose Section-N / See **…** citations.

    Used by Improve chat, Complete Scan, and Generate so wrong Section-N
    pointers and EDITOR NOTES work tickets never ship on any path.
    """
    logs: list[str] = []
    working = draft
    for section in list(draft.sections):
        if not section_needs_pointer_page_integrity(section):
            continue
        working, section_logs = apply_pointer_page_integrity(
            working, source_section_id=section.id
        )
        for line in section_logs:
            logs.append(f"{_short_title(section.title or section.id)}: {line}")
    working, prose_logs = rewrite_prose_section_citations_in_draft(working)
    logs.extend(prose_logs)
    return working, logs
