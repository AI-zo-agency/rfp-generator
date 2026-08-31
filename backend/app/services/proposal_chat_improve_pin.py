"""Form-slot + Improve-full-section helpers (chat, Complete Scan, Generate).

When the user pins Improve full section and pastes a multi-issue / missing-subsection
list, edit-scope must not collapse into a single selection patch (that path cannot
insert a missing I.2 heading and fails with DRAFT UNCHANGED).

Required RFP form slots (e.g. I.2 Active Client List) may reuse a client list that
already exists elsewhere in the manuscript — that is not anti-duplication padding.
`fill_all_active_client_lists_from_siblings` and `insert_all_board_roster_verify_flags`
run on Complete Scan (and Generate) so those pipelines match section chat.
"""

from __future__ import annotations

import re
from typing import Sequence

from app.models.proposal import ProposalDraft, ProposalSection

_MISSING_SUBSECTION_RE = re.compile(
    r"(?is)"
    r"(?:active\s+client\s+list|i\.?\s*2\b).{0,80}(?:entirely\s+)?missing"
    r"|jumps?\s+i\.?\s*1\s*(?:→|->|to)\s*i\.?\s*3"
    r"|no\s+client\s+list"
    r"|dangling\s+empty\s+header"
    r"|empty\s+table\s+cells"
    r"|structurally\s+incomplete"
)

_MULTI_ISSUE_RE = re.compile(
    r"(?m)^\s*(?:\d+[\).]|[-*•]|i\.\d+)\s+\S",
)

_ACTIVE_CLIENT_HEADING_RE = re.compile(
    r"(?im)^(?:#{1,3}\s*)?(?:i\.?\s*2\s*[—\-–:]?\s*)?active\s+client\s+lists?\s*$"
    r"|^(?:#{1,3}\s*)?(?:current|active)\s+clients?\s*$"
)

_I2_PRESENT_RE = re.compile(
    r"(?im)^(?:#{1,3}\s*)?i\.?\s*2\b.*(?:active\s+client|client\s+list)"
    r"|^(?:#{1,3}\s*)?active\s+client\s+lists?\s*$"
)

_I1_HEADING_RE = re.compile(r"(?im)^(?:#{1,3}\s*)?i\.?\s*1\b[^\n]*$")
_I3_HEADING_RE = re.compile(r"(?im)^(?:#{1,3}\s*)?i\.?\s*3\b[^\n]*$")
_NEXT_HEADING_RE = re.compile(r"(?m)^#{1,3}\s+\S")


_ADD_TABLE_OR_BLOCK_RE = re.compile(
    r"(?is)"
    r"\b(add|include|insert|put)\b.{0,48}\b(?:table|subsection|heading|block)\b"
    r"|"
    r"\b(?:table|subsection)\b.{0,32}\b(?:as\s+well|too)\b"
    r"|"
    r"\badd\s+(?:an?\s+)?\w+(?:\s+\w+){0,4}\s+table\b"
)

_THOROUGH_REPAIR_RE = re.compile(
    r"(?is)"
    r"\b(?:fix|address|resolve|correct|handle|cover|repair)\b.{0,32}\b(?:all|every|each)\b"
    r"|"
    r"\b(?:thoroughly|completely|fully|properly)\b.{0,40}\b(?:fix|address|resolve|update|repair)"
    r"|"
    r"\b(?:all|every)\s+(?:issue|problem|defect|gap|item|point|concern)s?\b"
    r"|"
    r"\b(?:these|client|following)\s+(?:issue|problem|defect|concern)s?\b"
    r"|"
    r"\bfix\s+everything\b"
    r"|"
    r"\b(?:make\s+sure|ensure)\b.{0,40}\b(?:all|every|each)\b"
)

_AS_WELL_BROADEN_RE = re.compile(r"(?is)\b(?:as\s+well|too|also)\b")


def user_asks_add_table_or_subsection(user_message: str) -> bool:
    """True when the user wants a new table/block — cannot be a selection splice."""
    ask = (user_message or "").strip()
    if not ask:
        return False
    return bool(_ADD_TABLE_OR_BLOCK_RE.search(ask))


def user_asks_thorough_section_repair(user_message: str) -> bool:
    """True when the ask needs a full-tab rewrite — not a Revise-content splice."""
    ask = (user_message or "").strip()
    if not ask:
        return False
    if user_asks_add_table_or_subsection(ask):
        return True
    if _THOROUGH_REPAIR_RE.search(ask):
        return True
    if _MISSING_SUBSECTION_RE.search(ask):
        return True
    issue_lines = _MULTI_ISSUE_RE.findall(ask)
    if len(issue_lines) >= 2:
        return True
    if issue_lines and re.search(r"(?is)\b(?:fix|address|resolve|correct|handle)\b", ask):
        return True
    if _AS_WELL_BROADEN_RE.search(ask) and re.search(
        r"(?is)\b(?:add|include|insert|fix|address|table|section|subsection|heading|block)\b",
        ask,
    ):
        return True
    return False


def improve_pin_needs_full_rewrite(user_message: str, section_content: str = "") -> bool:
    """True when Improve pin must rewrite the whole tab (not a selection splice)."""
    ask = (user_message or "").strip()
    if not ask:
        return False
    if user_asks_thorough_section_repair(ask):
        return True
    body = section_content or ""
    if body and _section_skips_i2(body) and re.search(
        r"(?i)active\s+client|client\s+list|i\.?\s*2\b", ask
    ):
        return True
    return False


def should_collapse_edit_scope_to_selection(
    *,
    improve_section_pinned: bool,
    user_message: str,
    section_content: str,
    planned_span_count: int,
) -> bool:
    """Whether a single edit-scope patch may become selection_mode.

    Improve full section never collapses to a selection splice — that path cannot
    insert missing headings (I.2 Active Client List) and previously failed with
    DRAFT UNCHANGED. Multi-patch (span_count > 1) stays on the multi-patch path.
    """
    if planned_span_count != 1:
        return False
    if improve_section_pinned:
        return False
    if user_asks_thorough_section_repair(user_message):
        return False
    return True


def _section_skips_i2(content: str) -> bool:
    body = content or ""
    if _I2_PRESENT_RE.search(body):
        return False
    has_i1 = bool(_I1_HEADING_RE.search(body))
    has_i3 = bool(_I3_HEADING_RE.search(body))
    return has_i1 and has_i3


def extract_active_client_list_block(content: str) -> str | None:
    """Return the Active Client List heading + body from a section, if present."""
    body = content or ""
    if not body.strip():
        return None
    lines = body.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if _ACTIVE_CLIENT_HEADING_RE.match(line.strip()):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if _NEXT_HEADING_RE.match(lines[j]) and not _ACTIVE_CLIENT_HEADING_RE.match(
            lines[j].strip()
        ):
            end = j
            break
    block = "".join(lines[start:end]).strip()
    return block if len(block) >= 12 else None


def insert_missing_active_client_list(
    content: str, client_list_block: str
) -> str | None:
    """Insert ## I.2 Active Client List between I.1 and I.3 when I.2 is missing."""
    body = content or ""
    donor = (client_list_block or "").strip()
    if not body.strip() or not donor:
        return None
    if not _section_skips_i2(body):
        return None

    # Normalize donor to I.2 heading for this form tab.
    donor_body = donor
    if _ACTIVE_CLIENT_HEADING_RE.match(donor.splitlines()[0].strip()):
        rest = "\n".join(donor.splitlines()[1:]).strip()
        donor_body = rest
    insert = f"## I.2 Active Client List\n\n{donor_body.strip()}\n"

    i3 = _I3_HEADING_RE.search(body)
    if i3:
        at = i3.start()
        # Preserve a blank line before I.3.
        prefix = body[:at].rstrip() + "\n\n"
        suffix = body[at:]
        return prefix + insert + "\n" + suffix.lstrip("\n")

    i1 = _I1_HEADING_RE.search(body)
    if not i1:
        return body.rstrip() + "\n\n" + insert
    # Append after I.1 block (before next major heading if any).
    after_i1 = body[i1.end() :]
    next_h = _NEXT_HEADING_RE.search(after_i1)
    if next_h:
        at = i1.end() + next_h.start()
        return body[:at].rstrip() + "\n\n" + insert + "\n" + body[at:].lstrip("\n")
    return body.rstrip() + "\n\n" + insert


def fill_active_client_list_from_siblings(
    section: ProposalSection,
    sections: Sequence[ProposalSection],
) -> ProposalSection | None:
    """Copy Active Client List from another tab into missing I.2 on this section."""
    body = section.content or ""
    if not _section_skips_i2(body):
        return None
    donor_block: str | None = None
    for other in sections:
        if other.id == section.id:
            continue
        block = extract_active_client_list_block(other.content or "")
        if block:
            donor_block = block
            break
    if not donor_block:
        return None
    updated = insert_missing_active_client_list(body, donor_block)
    if not updated or updated == body:
        return None
    return section.model_copy(update={"content": updated, "status": "generated"})


def fill_all_active_client_lists_from_siblings(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, list[str]]:
    """Draft-wide form-slot fill for Complete Scan + Generate (and chat).

    Required RFP evaluation/form tabs that jump I.1 → I.3 without I.2 Active
    Client List get the verified list copied from a sibling tab. Returns
    (draft, log lines). No-op when nothing to fill.
    """
    if not draft.sections:
        return draft, []
    logs: list[str] = []
    sections = list(draft.sections)
    changed = False
    for i, section in enumerate(sections):
        filled = fill_active_client_list_from_siblings(section, sections)
        if filled is None:
            continue
        sections[i] = filled
        changed = True
        logs.append(
            f"Form slot: copied Active Client List into missing I.2 on "
            f"“{(section.title or section.id).strip()}”"
        )
    if not changed:
        return draft, []
    return draft.model_copy(update={"sections": sections}), logs


BOARD_ROSTER_VERIFY_TAG = (
    "[VERIFY: Ella / Rachel — confirm this is the buyer's current board roster "
    "before submission]"
)

_BOARD_DISCLOSURE_TITLE_RE = re.compile(
    r"(?i)"
    r"campaign\s+contribution|"
    r"contribution\s+disclosure|"
    r"board\s+of\s+trustees|"
    r"governing\s+board|"
    r"exhibit\s*5\b"
)

_BOARD_LIST_HEADING_RE = re.compile(
    r"(?im)^(?:#{1,3}\s*)?(?:"
    r"governing\s+board(?:\s+members?)?|"
    r"board\s+of\s+trustees|"
    r"board\s+members?|"
    r"current\s+board(?:\s+roster)?"
    r")\s*$"
)

_EXISTING_BOARD_VERIFY_RE = re.compile(
    r"(?is)\[VERIFY:[^\]]*(?:board|trustee|roster)[^\]]*\]"
)


def insert_board_roster_verify_flag(
    section: ProposalSection,
) -> ProposalSection | None:
    """Plant one board-roster VERIFY on campaign/board disclosure tabs.

    Buyer board names are not zö KB facts — Complete Scan / Apply-the-fix must
    flag them for Ella/Rachel without inventing or rewriting the roster.
    """
    title = section.title or ""
    body = section.content or ""
    if not body.strip():
        return None
    if not (
        _BOARD_DISCLOSURE_TITLE_RE.search(title)
        or _BOARD_DISCLOSURE_TITLE_RE.search(body[:400])
        or _BOARD_LIST_HEADING_RE.search(body)
    ):
        return None
    if _EXISTING_BOARD_VERIFY_RE.search(body):
        return None
    # Need an actual roster signal — heading or several capitalized name lines.
    heading = _BOARD_LIST_HEADING_RE.search(body)
    if not heading and not re.search(
        r"(?im)^(?:[-*•]\s+)?[A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+){0,3}\s*$",
        body,
    ):
        return None

    insert = f"{BOARD_ROSTER_VERIFY_TAG}\n\n"
    if heading:
        at = heading.start()
        updated = body[:at].rstrip() + "\n\n" + insert + body[at:].lstrip("\n")
    else:
        # Place after first heading block if present.
        first_h = re.search(r"(?m)^#{1,3}\s+\S.*$", body)
        if first_h:
            # After the first heading line + following blank
            after = first_h.end()
            updated = body[:after].rstrip() + "\n\n" + insert + body[after:].lstrip("\n")
        else:
            updated = insert + body.lstrip("\n")
    if updated == body:
        return None
    return section.model_copy(update={"content": updated, "status": "generated"})


def insert_all_board_roster_verify_flags(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, list[str]]:
    """Draft-wide board-roster VERIFY for Complete Scan (and Generate parity)."""
    if not draft.sections:
        return draft, []
    logs: list[str] = []
    sections = list(draft.sections)
    changed = False
    for i, section in enumerate(sections):
        updated = insert_board_roster_verify_flag(section)
        if updated is None:
            continue
        sections[i] = updated
        changed = True
        logs.append(
            f"Form slot: inserted board-roster VERIFY on "
            f"“{(section.title or section.id).strip()}”"
        )
    if not changed:
        return draft, []
    return draft.model_copy(update={"sections": sections}), logs
