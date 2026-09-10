"""Client-facing manuscript integrity — role sync, staffing-history scrub, echo repair.

Deterministic. Never invents people or titles: Section 2 bio engagement roles win,
then Section 1.2 org chart. Used by Generate / Complete Scan / chat persist via
``apply_zero_fabrication_guards``.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.models.proposal import ProposalDraft, ProposalSection

# Truncated dedup pointer left mid-paren by shared-paragraph strip.
_TRUNCATED_COVERED_RE = re.compile(
    r"\(already covered there\.?(?!\s*—)",
    re.IGNORECASE,
)
_FULL_COVERED_POINTER = "(already covered there — not restated here)."

# Prompt bleed from TEAM / SPECIALIST ROLES rule: ")generalist." glued on.
_GENERALIST_BLEED_RE = re.compile(
    r"\)\s*generalist\b[^.!?\n]{0,80}\.?",
    re.IGNORECASE,
)

# Internal staffing history that must never ship to a buyer.
_STAFFING_HISTORY_SENTENCE_RE = re.compile(
    r"(?is)(?:^|(?<=[.!?]\s)|(?<=\n))"
    r"("
    r"[^.!?\n]{0,220}?\b(?:has|have)\s+retired\b[^.!?\n]*[.!]?"
    r"|[^.!?\n]{0,160}?\bformerly\s+(?:our|the)\b[^.!?\n]*[.!]?"
    r"|[^.!?\n]{0,100}?\[blank\][^.!?\n]*[.!]?"
    r"|[^.!?\n]{0,180}?\bnow\s+carries\s+(?:his|her|their)\s+accounts?\b[^.!?\n]*[.!]?"
    r"|[^.!?\n]{0,160}?\breplaced\s+(?:him|her|them)\s+as\b[^.!?\n]*[.!]?"
    r")"
)

_ROLEISH_RE = re.compile(
    r"(?i)\b(?:"
    r"account\s+manager|senior\s+account\s+manager|"
    r"executive\s+assistant|operations?\s+coordinator|"
    r"development\s+coordinator|project\s+coordinator|"
    r"creative\s+director|operations?\s+director|"
    r"agency\s+director|founder|strategist|producer|"
    r"media\s+buyer|brand\s+strategist|designer|"
    r"coordinator|manager|director|assistant|specialist|lead"
    r")\b"
)


# Floor roles from MasterTemplate / 04_Bio — used when Strict RFP has no Zo Section 2
# so garbled titles (e.g. Oyetola as Account Manager) still get corrected on persist.
_MASTERTEMPLATE_FLOOR_ROLES: dict[str, str] = {
    "oyetola oyewunmi": "Operations Coordinator",
    "haley neff": "Account Manager",
    "timi oyewunmi": "Executive Assistant",
    "rachel rice": "Development Coordinator",
    "ella lindau": "Operations Director",
    "sonja anderson": "Agency Director",
}


def canonical_roster_roles(draft: ProposalDraft) -> dict[str, str]:
    """Name (casefold) → canonical role. Section 2 engagement role wins over org chart."""
    from app.services.proposal_scan_fact_repairs import (
        _named_roster_from_section2,
        parse_org_chart_roles,
    )

    roles = dict(_MASTERTEMPLATE_FLOOR_ROLES)
    roles.update(parse_org_chart_roles(draft))
    for name, role in _named_roster_from_section2(draft, roles):
        role_cf = (role or "").casefold()
        if not role or role_cf.startswith("assigned to this engagement"):
            continue
        if role_cf.startswith("bio "):
            continue
        roles[name.casefold()] = role.strip()
    return roles


def _display_names(roles: dict[str, str], draft: ProposalDraft) -> dict[str, str]:
    """casefold → Title Case display name from Section 2 / org keys."""
    from app.services.proposal_bio_stub import is_plausible_person_name
    from app.services.proposal_kb_fact_checker import _member_name_from_bio_section

    out: dict[str, str] = {}
    for section in draft.sections:
        if (section.id or "").startswith("section-2-bio-"):
            member = _member_name_from_bio_section(section.title or "")
            if member and is_plausible_person_name(member):
                out[member.casefold()] = member
    for key in roles:
        out.setdefault(key, " ".join(p.capitalize() for p in key.split()))
    return out


def _role_for_name_in_cell(cell: str, roles: dict[str, str]) -> tuple[str, str] | None:
    """If cell is (or contains) a roster name, return (casefold_key, canonical_role)."""
    plain = re.sub(r"\*+", "", cell or "").strip()
    if not plain:
        return None
    key = plain.casefold()
    if key in roles:
        return key, roles[key]
    # Cell may be "Name — note" or "Name (email)"
    head = re.split(r"[—–,(|/]", plain, maxsplit=1)[0].strip()
    head_key = head.casefold()
    if head_key in roles:
        return head_key, roles[head_key]
    parts = head_key.split()
    if len(parts) >= 2:
        for roster_key, role in roles.items():
            if parts[0] in roster_key and parts[-1] in roster_key:
                return roster_key, role
    return None


def _cells_look_like_header(cells: list[str]) -> bool:
    joined = " ".join(cells).casefold()
    if "---" in joined:
        return True
    return any(
        h in joined
        for h in ("name", "role", "title", "position", "staff", "responsibility")
    ) and all(len(c) < 40 for c in cells)


def align_staff_table_roles_to_roster(
    content: str,
    roles: dict[str, str],
) -> tuple[str, list[str]]:
    """Rewrite wrong role cells in Staff / team tables to match the roster."""
    if not roles or not (content or "").strip():
        return content or "", []
    logs: list[str] = []
    lines = (content or "").splitlines(keepends=True)
    out: list[str] = []
    for line in lines:
        raw = line.rstrip("\n")
        ending = line[len(raw) :]
        if "|" not in raw or "---" in raw:
            out.append(line)
            continue
        leading = raw[: len(raw) - len(raw.lstrip())]
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        if len(cells) < 2 or _cells_look_like_header(cells):
            out.append(line)
            continue
        name_hits: list[tuple[int, str, str]] = []
        for i, cell in enumerate(cells):
            hit = _role_for_name_in_cell(cell, roles)
            if hit:
                name_hits.append((i, hit[0], hit[1]))
        if not name_hits:
            out.append(line)
            continue
        new_cells = list(cells)
        changed_row = False
        for name_idx, name_key, canonical in name_hits:
            for i, cell in enumerate(new_cells):
                if i == name_idx:
                    continue
                plain = re.sub(r"\*+", "", cell).strip()
                if not plain or not _ROLEISH_RE.search(plain):
                    continue
                # Skip long prose cells — only short title-like cells.
                if len(plain) > 80 or plain.count(" ") > 8:
                    continue
                if plain.casefold() == canonical.casefold():
                    continue
                # Don't overwrite another roster person's name cell.
                if _role_for_name_in_cell(plain, roles):
                    continue
                logs.append(
                    f"Staff role for {name_key}: '{plain}' → '{canonical}'"
                )
                new_cells[i] = canonical
                changed_row = True
        if changed_row:
            rebuilt = leading + "| " + " | ".join(new_cells) + " |" + ending
            out.append(rebuilt)
        else:
            out.append(line)
    return "".join(out), logs


def align_staff_prose_roles_to_roster(
    content: str,
    roles: dict[str, str],
    display_names: dict[str, str],
) -> tuple[str, list[str]]:
    """Fix 'Name as Wrong Title' prose using the canonical roster."""
    if not roles:
        return content or "", []
    text = content or ""
    logs: list[str] = []
    # Longest names first so "Haley Neff" beats "Haley".
    names = sorted(display_names.values(), key=len, reverse=True)
    if not names:
        return text, []
    name_alt = "|".join(re.escape(n) for n in names)
    roleish = (
        r"(?:Account\s+Manager|Senior\s+Account\s+Manager|Executive\s+Assistant|"
        r"Operations?\s+Coordinator|Development\s+Coordinator|"
        r"Project\s+Coordinator|Creative\s+Director|Operations?\s+Director|"
        r"Agency\s+Director|Founder|[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})"
    )
    pattern = re.compile(
        rf"(?i)\b({name_alt})\b(?:'s|\u2019s)?\s+(?:as|is|serves\s+as)\s+"
        rf"(?:the\s+)?({roleish})",
    )
    # "Name, Wrong Title," / "Name — Wrong Title" (staffing table prose).
    comma_pattern = re.compile(
        rf"(?i)\b({name_alt})\b\s*[,—–-]\s*(?:the\s+)?({roleish})\b",
    )

    def _repl(match: re.Match[str]) -> str:
        name = match.group(1)
        stated = match.group(2).strip()
        canonical = roles.get(name.casefold(), "")
        if not canonical or stated.casefold() == canonical.casefold():
            return match.group(0)
        logs.append(f"Prose role for {name}: '{stated}' → '{canonical}'")
        bridge = " as " if " as " in match.group(0).casefold() else " is "
        if "serves as" in match.group(0).casefold():
            return f"{name} serves as {canonical}"
        if bridge.strip() == "is":
            return f"{name} is {canonical}"
        return f"{name} as {canonical}"

    def _comma_repl(match: re.Match[str]) -> str:
        name = match.group(1)
        stated = match.group(2).strip()
        canonical = roles.get(name.casefold(), "")
        if not canonical or stated.casefold() == canonical.casefold():
            return match.group(0)
        logs.append(f"Prose role for {name}: '{stated}' → '{canonical}'")
        sep = "," if "," in match.group(0) else " —"
        return f"{name}{sep} {canonical}"

    text = pattern.sub(_repl, text)
    text = comma_pattern.sub(_comma_repl, text)
    return text, logs


def scrub_client_facing_staffing_history(content: str) -> tuple[str, list[str]]:
    """Drop retirement / formerly-our / blank-name staffing-history sentences."""
    text = content or ""
    if not text.strip():
        return text, []
    logs: list[str] = []
    guard = 0
    while guard < 20:
        guard += 1
        match = _STAFFING_HISTORY_SENTENCE_RE.search(text)
        if not match:
            break
        logs.append("Removed client-facing staffing-history sentence")
        text = (text[: match.start()] + text[match.end() :]).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"  +", " ", text)
    return text, logs


def repair_truncated_covered_pointers(content: str) -> tuple[str, list[str]]:
    """Complete truncated '(already covered there.' dedup pointers."""
    text = content or ""
    if "already covered there" not in text.casefold():
        return text, []
    if not _TRUNCATED_COVERED_RE.search(text):
        return text, []
    fixed = _TRUNCATED_COVERED_RE.sub(_FULL_COVERED_POINTER.rstrip("."), text)
    # Ensure closing paren+period once.
    fixed = re.sub(
        r"\(already covered there — not restated here\)\.?",
        _FULL_COVERED_POINTER,
        fixed,
        flags=re.I,
    )
    if fixed == text:
        return text, []
    return fixed, ["Repaired truncated 'already covered there' pointer"]


def scrub_generalist_prompt_bleed(content: str) -> tuple[str, list[str]]:
    """Strip ')generalist…' prompt bleed glued onto specialization sentences."""
    text = content or ""
    if "generalist" not in text.casefold():
        return text, []
    fixed, n = _GENERALIST_BLEED_RE.subn(").", text)
    if n:
        fixed = re.sub(r"\)\.\.+", ").", fixed)
        return fixed, ["Removed specialist-rule 'generalist' prompt bleed"]
    return text, []


def _near_duplicate(a: str, b: str, *, threshold: float = 0.92) -> bool:
    aa = re.sub(r"\s+", " ", (a or "").strip().casefold())
    bb = re.sub(r"\s+", " ", (b or "").strip().casefold())
    if not aa or not bb:
        return False
    if aa == bb:
        return True
    return SequenceMatcher(None, aa, bb).ratio() >= threshold


def collapse_duplicate_adjacent_table_rows(content: str) -> tuple[str, list[str]]:
    """Drop consecutive near-identical markdown table data rows."""
    text = content or ""
    if "|" not in text:
        return text, []
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    logs: list[str] = []
    prev_data_norm = ""
    for line in lines:
        raw = line.strip()
        if raw.startswith("|") and "---" not in raw:
            cells = [c.strip() for c in raw.strip("|").split("|")]
            if not _cells_look_like_header(cells):
                norm = " | ".join(cells).casefold()
                if prev_data_norm and _near_duplicate(prev_data_norm, norm):
                    logs.append("Collapsed duplicate adjacent table row")
                    continue
                prev_data_norm = norm
                out.append(line)
                continue
        prev_data_norm = ""
        out.append(line)
    return "".join(out), logs


def collapse_repeated_adjacent_sentences(content: str) -> tuple[str, list[str]]:
    """Collapse back-to-back near-identical sentences / duplicated half-blocks."""
    text = content or ""
    if not text.strip():
        return text, []
    logs: list[str] = []

    def _collapse_sentences(chunk: str) -> str:
        pieces = re.split(r"(?<=[.!?])\s+", chunk.strip()) if chunk.strip() else []
        if len(pieces) < 2:
            return chunk
        # ABAB → AB (duplicated deliverable block pasted twice).
        if len(pieces) >= 4 and len(pieces) % 2 == 0:
            half = len(pieces) // 2
            left = " ".join(pieces[:half])
            right = " ".join(pieces[half:])
            if _near_duplicate(left, right, threshold=0.88):
                logs.append("Collapsed duplicated sentence block")
                pieces = pieces[:half]
        kept: list[str] = []
        for piece in pieces:
            if kept and _near_duplicate(kept[-1], piece, threshold=0.90):
                logs.append("Collapsed repeated adjacent sentence")
                continue
            kept.append(piece)
        joined = " ".join(kept)
        if chunk.startswith(" ") or chunk.startswith("\t"):
            return chunk[: len(chunk) - len(chunk.lstrip())] + joined
        if chunk.endswith(" ") or chunk.endswith("\t"):
            return joined + chunk[len(chunk.rstrip()) :]
        return joined

    def _collapse_chunk(chunk: str) -> str:
        if chunk.strip().startswith("|") and chunk.count("|") >= 2:
            parts = chunk.split("|")
            return "|".join(_collapse_sentences(part) for part in parts)
        return _collapse_sentences(chunk)

    lines = text.splitlines(keepends=True)
    rebuilt: list[str] = []
    for line in lines:
        raw = line.rstrip("\n\r")
        ending = line[len(raw) :]
        rebuilt.append(_collapse_chunk(raw) + ending)
    return "".join(rebuilt), logs


def apply_client_facing_integrity_to_content(
    content: str,
    *,
    roles: dict[str, str] | None = None,
    display_names: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Run all per-section client-facing integrity repairs."""
    text = content or ""
    logs: list[str] = []

    fixed, hist = scrub_client_facing_staffing_history(text)
    if hist:
        text = fixed
        logs.extend(hist)

    fixed, ptr = repair_truncated_covered_pointers(text)
    if ptr:
        text = fixed
        logs.extend(ptr)

    fixed, gen = scrub_generalist_prompt_bleed(text)
    if gen:
        text = fixed
        logs.extend(gen)

    if roles:
        fixed, role_logs = align_staff_table_roles_to_roster(text, roles)
        if role_logs:
            text = fixed
            logs.extend(role_logs)
        fixed, prose_logs = align_staff_prose_roles_to_roster(
            text, roles, display_names or {}
        )
        if prose_logs:
            text = fixed
            logs.extend(prose_logs)

    fixed, row_logs = collapse_duplicate_adjacent_table_rows(text)
    if row_logs:
        text = fixed
        logs.extend(row_logs)

    fixed, sent_logs = collapse_repeated_adjacent_sentences(text)
    if sent_logs:
        text = fixed
        logs.extend(sent_logs)

    return text, logs


def apply_client_facing_integrity_to_draft(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, list[str]]:
    """Draft-wide client-facing integrity for Generate / Scan / chat persist."""
    roles = canonical_roster_roles(draft)
    display = _display_names(roles, draft)
    sections: list[ProposalSection] = []
    all_logs: list[str] = []
    changed = False
    for section in draft.sections:
        body = section.content or ""
        fixed, logs = apply_client_facing_integrity_to_content(
            body, roles=roles, display_names=display
        )
        if logs and fixed != body:
            changed = True
            label = section.title or section.id
            # Deduplicate log lines for the summary.
            uniq = list(dict.fromkeys(logs))
            all_logs.append(f"{label}: " + "; ".join(uniq[:6]))
            sections.append(section.model_copy(update={"content": fixed}))
        else:
            sections.append(section)
    if not changed:
        return draft, []
    return draft.model_copy(update={"sections": sections}), all_logs
