"""Ground personnel claims against known fabrications + cited KB text.

Names in Go/No-Go must come from retrieved knowledge-base excerpts only.
Do not invent staff, and do not scan criterion titles for First Last patterns.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.proposal import ProposalDraft

# Observed LLM inventions. Not a scanner — only these strings are blocked.
KNOWN_FABRICATED_PERSONNEL: tuple[str, ...] = (
    "Brittany Frazier",
    "Drew Stone",
    "Ben Edwards",
    "Erica Schultz",
    "Morgan Nivan",
    "Olajide Ojoeyemi",
    "Murilo Mendes",
    "Rad S",
    "Priyal Solanki",
)

# Seed only. Live list comes from Key Personas UI (retired_staff.json).
RETIRED_TEAM_PERSONNEL: tuple[str, ...] = (
    "Ron Comer",
)

# Current roster. A name may be cited only when it also appears in THIS claim's KB excerpt.
DOCUMENTED_TEAM_PERSONNEL: tuple[str, ...] = (
    "Sonja Anderson",
    "Todd Anderson",
    "Haley Neff",
    "Ella Lindau",
    "Curt Schultz",
    "Justin Bronson",
    "Gil Aranowitz",
    "Shawn DiCriscio",
    "Rachael Rice",
    "Sarah Eichhorn",
    "Nicole Anderson",
    "Vishal Nihlani",
    "Marcelle Benevides",
    "Kelvin Kiruthu",
    "Miguel Perez",
    "Alberto Bolaños",
)

_FABRICATED_BY_KEY: dict[str, str] = {
    name.casefold(): name for name in KNOWN_FABRICATED_PERSONNEL
}

_PERSONNEL_MANUAL_FILL = (
    "[MANUAL FILL: Sonja — assign verified team member; fabricated name removed]"
)

_RETIRED_MANUAL_FILL = (
    "[MANUAL FILL: Sonja — assign current staff; retired team member removed]"
)


def _folded(text: str) -> str:
    """Letters/digits/spaces only — no regex, no punctuation tricks."""
    chars: list[str] = []
    for ch in (text or "").casefold():
        if ch.isalnum() or ch.isspace():
            chars.append(ch)
        else:
            chars.append(" ")
    return " ".join("".join(chars).split())


def _name_key(name: str) -> str:
    return _folded(name)


def phrase_in(needle: str, haystack: str) -> bool:
    n = _folded(needle)
    h = _folded(haystack)
    if not n:
        return False
    return f" {n} " in f" {h} "


def documented_name_in_text(name: str, text: str) -> bool:
    """True when the roster name appears, allowing a one-token middle initial."""
    if phrase_in(name, text):
        return True
    parts = _folded(name).split()
    if len(parts) != 2:
        return False
    first, last = parts
    hay = _folded(text).split()
    for i, tok in enumerate(hay):
        if tok != first:
            continue
        window = hay[i + 1 : i + 3]
        if last in window:
            return True
    return False


def retired_team_personnel() -> tuple[str, ...]:
    """Names the UI marked retired — agents must never assign them as current staff."""
    try:
        from app.services.retired_staff_store import retired_names

        names = retired_names()
        if names:
            return names
    except Exception:
        pass
    return RETIRED_TEAM_PERSONNEL


def _retired_by_key() -> dict[str, str]:
    return {name.casefold(): name for name in retired_team_personnel()}


def roster_names_in_text(text: str) -> list[str]:
    """Documented staff whose names actually appear in this KB excerpt."""
    retired = {name.casefold() for name in retired_team_personnel()}
    found: list[str] = []
    for name in DOCUMENTED_TEAM_PERSONNEL:
        if name.casefold() in retired:
            continue
        if documented_name_in_text(name, text) and name not in found:
            found.append(name)
    return found


def _names_from_list(text: str, table: dict[str, str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for key, canonical in table.items():
        if phrase_in(canonical, text) or phrase_in(key, text):
            if key not in seen:
                seen.add(key)
                found.append(canonical)
    return found


def find_known_fabricated_names(text: str) -> list[str]:
    return _names_from_list(text, _FABRICATED_BY_KEY)


def find_retired_team_names(text: str) -> list[str]:
    return _names_from_list(text, _retired_by_key())


def is_retired_team_member(name: str) -> bool:
    return _name_key(name) in _retired_by_key()


def replace_listed_names(text: str, names: tuple[str, ...], replacement: str) -> str:
    """Case-insensitive replace of whole listed names. No regex."""
    if not text:
        return text
    out = text
    for name in names:
        variants = (name, f"{name}.")
        for variant in variants:
            folded_out = _folded(out)
            folded_n = _folded(variant)
            if not folded_n or f" {folded_n} " not in f" {folded_out} ":
                continue
            # Walk original string with same fold alignment is hard; replace
            # by scanning case-insensitive on the original with stripped punct.
            lower = out.casefold()
            needle = variant.casefold()
            pieces: list[str] = []
            i = 0
            while True:
                j = lower.find(needle, i)
                if j < 0:
                    pieces.append(out[i:])
                    break
                pieces.append(out[i:j])
                pieces.append(replacement)
                i = j + len(variant)
            out = "".join(pieces)
    return out


def personnel_claim_failure(
    *,
    requirement: str,
    quote: str = "",
    source_text: str = "",
) -> str | None:
    """Fail when a Verified claim names invented, retired, or uncited staff.

    Criterion titles are not scanned for people. Only listed fabrications,
    retired staff, and documented roster names in the quote are checked —
    and documented names must appear in the cited KB excerpt.
    """
    claim = f"{requirement}\n{quote}"
    fabricated = find_known_fabricated_names(claim)
    if fabricated:
        who = fabricated[0]
        return (
            f"fabricated personnel '{who}' is not a documented zö team member — "
            "FLAG SONJA; do not mark Verified"
        )

    retired = find_retired_team_names(claim)
    if retired:
        who = retired[0]
        return (
            f"'{who}' is retired and is not current zö staff — do not assign "
            "as account lead; FLAG SONJA for the live roster"
        )

    for name in DOCUMENTED_TEAM_PERSONNEL:
        if not documented_name_in_text(name, quote):
            continue
        if source_text and documented_name_in_text(name, source_text):
            continue
        return (
            f"named person '{name}' does not appear in the cited KB source — "
            "cannot Verified a staffing claim without roster evidence"
        )
    return None


def scrub_fabricated_personnel_from_draft(
    draft: "ProposalDraft",
) -> tuple["ProposalDraft", list[str]]:
    """Remove invented and retired team members from every manuscript section.

    Fabrications (Brittany Frazier, …) and Key Personas–retired staff (Ron Comer,
    plus anyone marked retired in the UI) must never ship as current assignees.
    """
    from datetime import datetime, timezone

    from app.models.proposal import ProposalSection

    if not draft.sections:
        return draft, []

    logs: list[str] = []
    sections: list[ProposalSection] = []
    changed = False
    retired = retired_team_personnel()

    for section in draft.sections:
        content = section.content or ""
        title = section.title or ""
        fabricated = find_known_fabricated_names(content) or find_known_fabricated_names(
            title
        )
        retired_hit = find_retired_team_names(content) or find_retired_team_names(title)
        if not fabricated and not retired_hit:
            sections.append(section)
            continue

        new_content = content
        new_title = title
        if fabricated:
            new_content = replace_listed_names(
                new_content, KNOWN_FABRICATED_PERSONNEL, _PERSONNEL_MANUAL_FILL
            )
            new_title = replace_listed_names(
                new_title, KNOWN_FABRICATED_PERSONNEL, "Team Member (assign)"
            )
        if retired_hit:
            new_content = replace_listed_names(
                new_content, retired, _RETIRED_MANUAL_FILL
            )
            new_title = replace_listed_names(
                new_title, retired, "Team Member (assign current staff)"
            )

        if new_content != content or new_title != title:
            changed = True
            who = ", ".join([*(fabricated or [])[:2], *(retired_hit or [])[:2]])
            kind = "retired" if retired_hit and not fabricated else "fabricated"
            if fabricated and retired_hit:
                kind = "fabricated/retired"
            logs.append(f"{title or section.id}: removed {kind} personnel ({who})")
            sections.append(
                section.model_copy(
                    update={"content": new_content, "title": new_title or section.title}
                )
            )
        else:
            sections.append(section)

    update: dict = {}
    if changed:
        update["sections"] = sections
        update["updated_at"] = datetime.now(timezone.utc).isoformat()

    locks = getattr(draft, "manuscript_locks", None)
    if locks is not None and is_retired_team_member(
        getattr(locks, "primary_contact_name", "") or ""
    ):
        changed = True
        logs.append(
            f"manuscript locks: cleared retired primary "
            f"'{locks.primary_contact_name}' → Sonja Anderson (confirm)"
        )
        update["manuscript_locks"] = locks.model_copy(
            update={
                "primary_contact_name": "Sonja Anderson",
                "primary_contact_title": "Agency Director",
                "needs_human_confirm": True,
                "decision_rationale": (
                    f"{getattr(locks, 'decision_rationale', '') or ''} "
                    "Retired staff cannot be primary contact — confirm current lead."
                ).strip(),
            }
        )

    if not changed:
        return draft, logs

    return draft.model_copy(update=update), logs


def _member_in_org_roster(member: str, org_roles: dict[str, str]) -> bool:
    """True when name matches Section 1.2 org chart (same rule as scan compliance)."""
    key = (member or "").casefold().strip()
    if not key:
        return False
    if key in org_roles:
        return True
    parts = key.split()
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        for name in org_roles:
            if first in name and last in name:
                return True
    return False


def _person_heading_starts_block(line: str, name: str) -> bool:
    stripped = (line or "").strip()
    if not stripped:
        return False
    m = re.match(
        r"^(?:#{1,4}\s+|\*\*)(.+?)(?:\*\*)?\s*$",
        stripped,
    )
    if not m:
        return False
    heading = m.group(1).split(",")[0].split("—")[0].split("–")[0].strip()
    name_cf = name.casefold()
    head_cf = heading.casefold()
    return head_cf == name_cf or name_cf in head_cf


def _strip_unverified_person_block(
    content: str,
    name: str,
    replacement_block: str,
) -> tuple[str, bool]:
    """Replace one ### Name bio block (until the next person heading)."""
    lines = (content or "").splitlines()
    if not lines:
        return content, False
    out: list[str] = []
    i = 0
    changed = False
    while i < len(lines):
        line = lines[i]
        if _person_heading_starts_block(line, name):
            if out and out[-1].strip():
                out.append("")
            out.append(replacement_block.strip())
            changed = True
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if nxt and re.match(r"^(?:#{1,4}\s+|\*\*)[A-Z]", nxt):
                    if not _person_heading_starts_block(lines[i], name):
                        break
                i += 1
            continue
        out.append(line)
        i += 1
    if not changed:
        new = replace_listed_names(content, (name,), _PERSONNEL_MANUAL_FILL)
        return new, new != content
    merged = "\n".join(out)
    merged = re.sub(r"\n{3,}", "\n\n", merged)
    return merged, True


async def _build_verified_roster_keys(draft: "ProposalDraft") -> set[str]:
    """Org chart + documented roster + MasterTemplate team list."""
    from app.services.proposal_scan_fact_repairs import parse_org_chart_roles

    keys: set[str] = set()
    org_roles = parse_org_chart_roles(draft)
    keys.update(org_roles.keys())
    for name in DOCUMENTED_TEAM_PERSONNEL:
        keys.add(name.casefold())
    retired = {n.casefold() for n in retired_team_personnel()}
    keys -= retired
    try:
        from app.services.company_qualification.agents.team_selection import (
            build_roster_profiles,
        )
        from app.services.proposal_knowledge_base_tools import fetch_master_team_roster

        roster_text, _ = await fetch_master_team_roster()
        for profile in build_roster_profiles(roster_text or ""):
            name = str(profile.get("name") or "").strip()
            if name:
                keys.add(name.casefold())
    except Exception:  # noqa: BLE001
        pass
    return keys


async def _person_has_bio_evidence(name: str) -> bool:
    try:
        from app.services.proposal_sections_graph import _find_member_bio_document

        doc = await _find_member_bio_document(name)
        return bool(doc)
    except Exception:  # noqa: BLE001
        return False


async def scrub_unverified_personnel_from_draft(
    draft: "ProposalDraft",
) -> tuple["ProposalDraft", list[str]]:
    """Remove invented staff from Team Bios / personnel tabs — not only blocklist hits.

    Names must appear on the org chart, documented roster, MasterTemplate list,
    or a retrievable 04_Bio document. Otherwise the person's block is MANUAL FILL.
    """
    from datetime import datetime, timezone

    from app.models.proposal import ProposalSection
    from app.services.proposal_capability_bio_grounding import (
        is_personnel_bio_section,
        named_people_in_section,
    )
    from app.services.proposal_scan_fact_repairs import parse_org_chart_roles

    if not draft.sections:
        return draft, []

    verified_keys = await _build_verified_roster_keys(draft)
    org_roles = parse_org_chart_roles(draft)
    logs: list[str] = []
    sections: list[ProposalSection] = []
    changed = False

    for section in draft.sections:
        if not is_personnel_bio_section(section):
            sections.append(section)
            continue
        body = section.content or ""
        title = section.title or ""
        people = named_people_in_section(section)
        new_body = body
        section_changed = False

        for name in people:
            fabricated = find_known_fabricated_names(name)
            retired_hit = find_retired_team_names(name)
            if fabricated or retired_hit:
                continue
            key = name.casefold()
            if key in verified_keys or _member_in_org_roster(name, org_roles):
                continue
            if await _person_has_bio_evidence(name):
                verified_keys.add(key)
                continue

            replacement = (
                f"### {name}\n\n"
                f"**Role on this engagement:** [MANUAL FILL: Sonja — assign verified "
                f"team member from Section 2 / 04_Bio roster]\n\n"
                f"{_PERSONNEL_MANUAL_FILL}"
            )
            new_body, hit = _strip_unverified_person_block(new_body, name, replacement)
            if hit:
                section_changed = True
                logs.append(
                    f"{title or section.id}: removed unverified personnel '{name}' "
                    "(not on org chart / MasterTemplate / 04_Bio)"
                )

        if section_changed:
            changed = True
            sections.append(
                section.model_copy(
                    update={"content": new_body, "status": "generated"}
                )
            )
        else:
            sections.append(section)

    if not changed:
        return draft, logs
    return draft.model_copy(
        update={
            "sections": sections,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ), logs
