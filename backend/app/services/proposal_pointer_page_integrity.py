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


def _pipe_cells(line: str) -> list[str]:
    stripped = (line or "").strip()
    if "|" not in stripped:
        return []
    return [c.strip() for c in stripped.strip("|").split("|")]


def _is_cross_ref_location_header(line: str) -> bool:
    """True for Addressed-In / Where-to-Find / Submittal checklist Location tables.

    Structural header check only — no topic synonym tables.
    """
    if _ADDRESSED_IN_HEADER_RE.match((line or "").rstrip("\n")):
        return True
    cells = [c.casefold() for c in _pipe_cells(line)]
    if len(cells) < 2:
        return False
    has_item = any(
        "requirement" in c
        or "component" in c
        or "submittal" in c
        or "submission" in c
        or c == "item"
        or c.endswith(" item")
        or c.startswith("required")
        for c in cells
    )
    has_location = any(
        "location" in c
        or "addressed" in c
        or "where to find" in c
        or c.startswith("see")
        or "find it" in c
        for c in cells
    )
    return has_item and has_location


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
        # (Experience…)" gets remapped to §21. Never fall back to cited_mark
        # when the paren invents a tab that is not in the live TOC (e.g.
        # "Section 3 (Schedule tab)" while §3 is Our Work).
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
                candidate = _toc_entry_for_section(hit)
                if paren and (
                    candidate is None
                    or not _paren_topic_matches_entry(paren, candidate)
                ):
                    # Wrong number + phantom topic — do not re-point at unrelated §N
                    # (including when the live §N title is stopword-only, e.g. Our Work).
                    logs.append(
                        f"phantom prose citation cleared: {match.group(0).strip()[:100]}"
                    )
                    changed += 1
                    fill = (
                        "[MANUAL FILL: map to a live proposal tab — "
                        "cited section does not exist in this manuscript]"
                    )
                    if prefix.strip():
                        return f"{prefix}{fill}"
                    return fill
                if candidate is not None:
                    entry = candidate
        if entry is None:
            logs.append(
                f"unresolved prose citation: {match.group(0).strip()[:100]}"
            )
            return match.group(0)        # Already correct live mark with matching paren short-title — keep.
        if entry.mark == cited_mark and (
            not paren or _paren_topic_matches_entry(paren, entry)
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


def _header_location_column(header_line: str) -> tuple[int, int | None]:
    """Return (location_col_idx, included_col_idx_or_None) for a cross-ref header."""
    cells = [c.casefold() for c in _pipe_cells(header_line)]
    loc_idx = 1 if len(cells) >= 2 else 0
    incl_idx: int | None = None
    for i, c in enumerate(cells):
        if (
            "location" in c
            or "addressed" in c
            or "where to find" in c
            or "find it" in c
        ):
            loc_idx = i
        if "included" in c or c in {"yes/no", "y/n", "status"}:
            incl_idx = i
    return loc_idx, incl_idx


def _location_names_missing_sidebar_tab(location: str, draft: ProposalDraft) -> bool:
    """True when a Location cell names a tab that is not in the live sidebar."""
    text = (location or "").strip()
    if not text or text.casefold() in {"n/a", "na", "—", "-", "tbd"}:
        return False
    from app.services.proposal_outline_dedup import outline_titles_near_duplicate

    topic = re.sub(r"(?i)\s+tabs?\s*$", "", text).strip()
    topic = re.sub(r"(?i)^\s*(?:see|see\s+the)\s+", "", topic).strip()
    paren = re.search(r"\(([^)]{2,120})\)", topic)
    if paren:
        topic = paren.group(1).strip()
        topic = re.sub(r"(?i)\s+tabs?\s*$", "", topic).strip()
    if len(topic) < 3:
        return False
    for section in draft.sections:
        title = section.title or ""
        if outline_titles_near_duplicate(topic, title):
            return False
        bare = re.sub(r"^\s*\d+(?:\.\d+)*\s*[.:—–\-)]\s*", "", title).strip()
        if bare and outline_titles_near_duplicate(topic, bare):
            return False
    return True


def _rewrite_one_cross_ref_table(
    lines: list[str],
    header_idx: int,
    draft: ProposalDraft,
    *,
    self_section_id: str | None,
) -> tuple[int, list[str]]:
    """Rewrite one cross-ref table in ``lines`` starting at ``header_idx``.

    Returns ``(changed_count, unresolved_logs)``. Mutates ``lines`` in place.
    """
    loc_idx, incl_idx = _header_location_column(lines[header_idx])
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
        if len(cells) <= loc_idx:
            continue
        requirement, addressed = cells[0], cells[loc_idx]
        if not requirement or requirement.casefold() in {
            "rfp requirement",
            "requirement",
            "required component",
            "component",
            "submittal item",
            "item",
        }:
            continue
        if re.search(r"\b1\.\d+\b", addressed) and "who we are" in addressed.casefold():
            continue
        if re.search(r"(?i)section\s+iii\b|references?\s+form", addressed):
            continue
        # Submittal checklist (has Included col): Location must name a live tab.
        # Classic Addressed-In tables keep the remap path below.
        if incl_idx is not None and _location_names_missing_sidebar_tab(
            addressed, draft
        ):
            cells[loc_idx] = (
                "[MANUAL FILL: no dedicated sidebar tab — map to a live section "
                "or add the missing tab]"
            )
            if incl_idx is not None and incl_idx < len(cells):
                incl = cells[incl_idx].casefold()
                if incl in {"yes", "y", "included", "✓", "✔"} or "yes" in incl:
                    cells[incl_idx] = "No"
            newline = "\n" if raw.endswith("\n") else ""
            lines[i] = "| " + " | ".join(cells) + " |" + newline
            changed += 1
            unresolved.append(
                f"checklist Location cleared (missing tab): {requirement[:100].strip()}"
            )
            continue
        live_mark = re.search(r"§\s*(\d+(?:\.\d+)?)", addressed)
        if live_mark and _find_section_by_mark(draft, live_mark.group(1)):
            if _addressed_cites_missing_tab(addressed, draft):
                pass
            else:
                continue
        entry = resolve_addressed_in_target(
            draft, requirement, self_section_id=self_section_id
        )
        if entry is None:
            paren = re.search(r"\(([^)]{3,140})\)", addressed or "")
            if paren:
                entry = resolve_addressed_in_target(
                    draft, paren.group(1), self_section_id=self_section_id
                )
        if entry is None:
            if _addressed_cites_missing_tab(addressed, draft) or re.search(
                r"(?i)(?:§\s*|section\s+)\d+", addressed or ""
            ):
                cells[loc_idx] = (
                    "[MANUAL FILL: map this requirement to a live proposal tab — "
                    "cited section does not exist in this manuscript]"
                )
                newline = "\n" if raw.endswith("\n") else ""
                lines[i] = "| " + " | ".join(cells) + " |" + newline
                changed += 1
                unresolved.append(
                    f"phantom cross-ref cleared: {requirement[:100].strip()}"
                )
            else:
                unresolved.append(
                    f"unresolved Addressed-In: {requirement[:100].strip()}"
                )
            continue
        new_cell = format_addressed_in_cell(entry)
        if new_cell.casefold() in addressed.casefold() and f"§{entry.mark}" in addressed:
            if not _addressed_cites_missing_tab(addressed, draft):
                continue
        cells[loc_idx] = new_cell
        newline = "\n" if raw.endswith("\n") else ""
        lines[i] = "| " + " | ".join(cells) + " |" + newline
        changed += 1
    return changed, unresolved


def rewrite_cross_ref_addressed_in_table(
    content: str,
    draft: ProposalDraft,
    *,
    self_section_id: str | None = None,
) -> tuple[str, int, list[str]]:
    """Rewrite location cells to real § marks from the live TOC.

    Covers ``Addressed In`` and ``Where to Find It`` / Required Component tables.
    Processes every matching table in the body. Unresolved rows that point at
    phantom tabs (e.g. ``Section 3 (Schedule tab)`` when no Schedule exists)
    become MANUAL FILL — never ship a citation to a section that is not here.
    """
    body = content or ""
    lines = body.splitlines(keepends=True)
    header_idxs = [
        i
        for i, line in enumerate(lines)
        if _is_cross_ref_location_header(line.rstrip("\n"))
    ]
    if not header_idxs:
        return body, 0, []

    changed = 0
    unresolved: list[str] = []
    for header_idx in header_idxs:
        n, unresolved_chunk = _rewrite_one_cross_ref_table(
            lines, header_idx, draft, self_section_id=self_section_id
        )
        changed += n
        unresolved.extend(unresolved_chunk)

    if not changed and not unresolved:
        return body, 0, []
    return "".join(lines) if changed else body, changed, unresolved


def _paren_topic_matches_entry(paren: str, entry: _TocEntry) -> bool:
    """True when parenthetical topic text plausibly names this TOC entry."""
    topic = (paren or "").strip()
    if not topic:
        return False
    topic_cf = topic.casefold()
    for noise in (" tabs", " tab", " sections", " section"):
        if topic_cf.endswith(noise.strip()):
            topic_cf = topic_cf[: -len(noise.strip())].strip()
            break
    if not topic_cf:
        return False
    title_cf = (entry.title or "").casefold()
    short_cf = _short_title(entry.title).casefold()
    if topic_cf in title_cf or topic_cf in short_cf:
        return True
    if short_cf and short_cf in topic_cf:
        return True
    topic_toks = _tokens(topic)
    if topic_toks and topic_toks <= set(entry.tokens):
        return True
    # Shared substantive tokens (≥2) — same bar as outline eval-weight stamps.
    shared = topic_toks & set(entry.tokens)
    return len(shared) >= 2


def _addressed_cites_missing_tab(addressed: str, draft: ProposalDraft) -> bool:
    """True when the cell invents a tab label that is not in the live TOC titles."""
    text = addressed or ""
    # Parenthetical topic: "Section 3 (Schedule tab)"
    paren = re.search(r"\(([^)]{2,120})\)", text)
    if not paren:
        # Bare "Section 3 tabs" with no live mark match handled elsewhere.
        return bool(re.search(r"(?i)section\s+\d+\s+tabs?\b", text))
    topic = paren.group(1).strip()
    topic_cf = topic.casefold()
    # Strip trailing "tab(s)" noise for matching.
    for noise in (" tabs", " tab", " sections", " section"):
        if topic_cf.endswith(noise.strip()):
            topic_cf = topic_cf[: -len(noise.strip())].strip()
            break
    if not topic_cf or len(topic_cf) < 3:
        return False
    for entry in _toc_entries(draft):
        if _paren_topic_matches_entry(topic, entry):
            return False
    return True


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
    for line in body.splitlines():
        if _is_cross_ref_location_header(line):
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
