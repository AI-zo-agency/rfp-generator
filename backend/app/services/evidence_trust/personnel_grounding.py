"""Ground named personnel claims against known fabrications + cited KB text.

Go/No-Go repeatedly marks invented people as Verified (Brittany Frazier as
Creative Director citing MasterTemplate) while the Drew Stone scrub fires on
the next row. The Drew-only regex was the inconsistency; known fabrications
must be shared, and any First Last in a Verified claim must appear in the
cited source (MasterTemplate naming Curt Schultz does not evidence Brittany).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.proposal import ProposalDraft

# Names already observed as LLM inventions (docs + live Go/No-Go / proposal runs).
# Keep lowercase keys for matching; display uses canonical Title Case.
KNOWN_FABRICATED_PERSONNEL: tuple[str, ...] = (
    "Brittany Frazier",
    "Drew Stone",
    "Ben Edwards",
    "Erica Schultz",
    "Morgan Nivan",
    "Olajide Ojoeyemi",
    "Murilo Mendes",
)

# Documented zö leads commonly cited in Go/No-Go (bios / MasterTemplate).
# Used only as a hint for role-replacement copy — grounding still requires the
# name to appear in the cited source text for THIS claim.
DOCUMENTED_TEAM_PERSONNEL: tuple[str, ...] = (
    "Sonja Anderson",
    "Todd Anderson",
    "Ron Comer",
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
)

_FABRICATED_BY_KEY: dict[str, str] = {
    name.casefold(): name for name in KNOWN_FABRICATED_PERSONNEL
}

# First Last (optional middle initial). Skips ALL-CAPS acronyms.
_PERSON_NAME_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z]\.)?\s+[A-Z][a-z]+)\b"
)

# Tokens that look like Title Case pairs but are roles/places, not people.
_NON_PERSON_TOKENS = frozenset(
    {
        "creative",
        "director",
        "project",
        "lead",
        "account",
        "manager",
        "senior",
        "junior",
        "agency",
        "director",
        "new",
        "york",
        "north",
        "carolina",
        "south",
        "carolina",
        "los",
        "angeles",
        "san",
        "francisco",
        "master",
        "template",
        "public",
        "sector",
        "media",
        "buying",
        "united",
        "states",
        "case",
        "study",
        "knowledge",
        "base",
    }
)


def _name_key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


def find_known_fabricated_names(text: str) -> list[str]:
    """Return canonical fabricated names present in ``text`` (order preserved)."""
    blob = text or ""
    if not blob.strip():
        return []
    found: list[str] = []
    seen: set[str] = set()
    for key, canonical in _FABRICATED_BY_KEY.items():
        # Word-boundary-ish: allow hyphen/space variants via simple search.
        if re.search(rf"\b{re.escape(canonical)}\b", blob, re.IGNORECASE):
            if key not in seen:
                seen.add(key)
                found.append(canonical)
    return found


def fabricated_personnel_regex() -> re.Pattern[str]:
    """Single regex matching any known fabricated First Last."""
    parts = [re.escape(n).replace(r"\ ", r"\s+") for n in KNOWN_FABRICATED_PERSONNEL]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)


def extract_person_name_candidates(text: str) -> list[str]:
    """Heuristic First Last spans that are not obvious role/place pairs."""
    out: list[str] = []
    seen: set[str] = set()
    for match in _PERSON_NAME_RE.finditer(text or ""):
        name = re.sub(r"\s+", " ", match.group(1)).strip()
        parts = name.replace(".", "").split()
        if len(parts) < 2:
            continue
        if any(p.casefold() in _NON_PERSON_TOKENS for p in parts):
            continue
        key = _name_key(name)
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def name_appears_in_source(name: str, source_text: str) -> bool:
    """True when both name tokens appear (order-tolerant) in the source."""
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p and p != "."]
    if len(parts) < 2:
        return False
    blob = (source_text or "").casefold()
    return all(p.casefold().rstrip(".") in blob for p in parts if len(p) > 1)


def personnel_claim_failure(
    *,
    requirement: str,
    quote: str = "",
    source_text: str = "",
) -> str | None:
    """Return a downgrade reason when a Verified claim invents or mis-cites a person.

    - Known fabrications (Brittany Frazier, Drew Stone, …) always fail.
    - Any First Last named in the requirement must appear in the cited source
      text. Citing MasterTemplate for Creative Director does not evidence
      Brittany Frazier when the roster names Curt Schultz.
    """
    claim = f"{requirement}\n{quote}"
    fabricated = find_known_fabricated_names(claim)
    if fabricated:
        who = fabricated[0]
        return (
            f"fabricated personnel '{who}' is not a documented zö team member — "
            "FLAG SONJA; do not mark Verified"
        )

    for person in extract_person_name_candidates(requirement):
        if find_known_fabricated_names(person):
            continue  # already handled above
        if source_text and name_appears_in_source(person, source_text):
            continue
        return (
            f"named person '{person}' does not appear in the cited KB source — "
            "cannot Verified a staffing claim without roster evidence"
        )
    return None


_PERSONNEL_MANUAL_FILL = (
    "[MANUAL FILL: Sonja — assign verified team member; fabricated name removed]"
)


def scrub_fabricated_personnel_from_draft(
    draft: "ProposalDraft",
) -> tuple["ProposalDraft", list[str]]:
    """Remove known invented team members from every manuscript section."""
    from app.models.proposal import ProposalDraft, ProposalSection
    from datetime import datetime, timezone

    if not draft.sections:
        return draft, []

    pattern = fabricated_personnel_regex()
    logs: list[str] = []
    sections: list[ProposalSection] = []
    changed = False

    for section in draft.sections:
        content = section.content or ""
        found = find_known_fabricated_names(content)
        if not found:
            sections.append(section)
            continue
        new_content = pattern.sub(_PERSONNEL_MANUAL_FILL, content)
        if new_content != content:
            changed = True
            who = ", ".join(found[:3])
            logs.append(
                f"{section.title or section.id}: removed fabricated personnel ({who})"
            )
            sections.append(section.model_copy(update={"content": new_content}))
        else:
            sections.append(section)

    if not changed:
        return draft, logs

    return (
        draft.model_copy(
            update={
                "sections": sections,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        logs,
    )
