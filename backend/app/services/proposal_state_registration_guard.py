"""Never assert business registration in a jurisdiction companyfacts does not list.

Companyfacts / the manuscript's own State Registrations inventory is the source of
truth. A signed transmittal that claims Maryland (or any other state) while the
verified list is Oregon, Washington, Texas, Colorado, and California is a
checkable legal falsehood.

Geography names are used only to parse that inventory vs claims — not to match
RFP requirements to KB evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection

# Official names only (no postal abbreviations — those false-hit ordinary words).
_US_JURISDICTIONS: tuple[str, ...] = (
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "District of Columbia",
    "Florida",
    "Georgia",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "West Virginia",
    "Wisconsin",
    "Wyoming",
)

_REGISTRATION_CLAIM_PHRASES: tuple[str, ...] = (
    "registered to conduct business",
    "registered to do business",
    "registered to transact business",
    "authorized to conduct business",
    "authorized to do business",
    "qualified to do business",
    "foreign qualified",
    "foreign qualification",
)

_MANUAL_FILL_PREFIX = "[MANUAL FILL: Sonja —"


def _is_inventory_section(section: ProposalSection) -> bool:
    sid = (section.id or "").casefold()
    title = (section.title or "").casefold()
    body = (section.content or "").casefold()
    if sid.startswith("section-1-business"):
        return True
    if "state registration" in title:
        return True
    if "state registrations" in body:
        return True
    return False


def _inventory_text(draft: ProposalDraft, research: ProposalResearchCache | None) -> str:
    parts: list[str] = []
    for section in draft.sections or []:
        if _is_inventory_section(section):
            parts.append(section.content or "")
    if research:
        for item in research.evidence_corpus or []:
            source = f"{getattr(item, 'source', '')} {getattr(item, 'chunk_key', '')}"
            if "companyfacts" in source.casefold():
                parts.append(getattr(item, "excerpt", "") or "")
    return "\n".join(parts)


def verified_registration_jurisdictions(
    draft: ProposalDraft,
    research: ProposalResearchCache | None = None,
) -> list[str]:
    """Jurisdictions listed in Section 1.3 / companyfacts State Registrations."""
    blob = _inventory_text(draft, research).casefold()
    if "state registration" not in blob and "registered" not in blob:
        # Still accept a bare list of states in the business-info tab.
        pass
    found: list[str] = []
    for name in _US_JURISDICTIONS:
        if name.casefold() in blob:
            found.append(name)
    return found


def _sentence_is_registration_claim(sentence: str) -> bool:
    cf = sentence.casefold()
    if _MANUAL_FILL_PREFIX.casefold() in cf:
        return False
    if "[verify:" in cf:
        return False
    return any(phrase in cf for phrase in _REGISTRATION_CLAIM_PHRASES)


def _claimed_jurisdictions(sentence: str) -> list[str]:
    cf = sentence.casefold()
    return [name for name in _US_JURISDICTIONS if name.casefold() in cf]


def _honest_replacement(unlisted: list[str], verified: list[str]) -> str:
    claimed = ", ".join(unlisted)
    if verified:
        listed = ", ".join(verified)
        return (
            f"{_MANUAL_FILL_PREFIX} {claimed} business registration is not on the "
            f"verified State Registrations list ({listed}). Confirm the filing "
            f"(public record) before asserting it in a signed letter, or delete "
            f"this sentence.]"
        )
    return (
        f"{_MANUAL_FILL_PREFIX} do not assert {claimed} business registration "
        f"until it appears in companyfacts / Section 1.3 State Registrations.]"
    )


def _split_keep_delim(text: str) -> list[str]:
    chunks: list[str] = []
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if ch in ".!?\n":
            chunks.append("".join(buf))
            buf = []
    if buf:
        chunks.append("".join(buf))
    return chunks


def scrub_unverified_state_registration_claims(
    draft: ProposalDraft,
    research: ProposalResearchCache | None = None,
) -> tuple[ProposalDraft, list[str]]:
    """Replace unverified 'registered in X' facts with MANUAL FILL. Never invent X."""
    verified = verified_registration_jurisdictions(draft, research)
    verified_cf = {name.casefold() for name in verified}
    logs: list[str] = []
    sections = list(draft.sections or [])
    changed = False

    for idx, section in enumerate(sections):
        if _is_inventory_section(section):
            continue
        body = section.content or ""
        if not body.strip():
            continue
        pieces = _split_keep_delim(body)
        new_pieces: list[str] = []
        section_hits: list[str] = []
        for piece in pieces:
            if not _sentence_is_registration_claim(piece):
                new_pieces.append(piece)
                continue
            claimed = _claimed_jurisdictions(piece)
            unlisted = [n for n in claimed if n.casefold() not in verified_cf]
            if not unlisted:
                new_pieces.append(piece)
                continue
            new_pieces.append(_honest_replacement(unlisted, verified))
            section_hits.extend(unlisted)
        if not section_hits:
            continue
        new_body = "".join(new_pieces)
        if new_body == body:
            continue
        sections[idx] = section.model_copy(update={"content": new_body})
        changed = True
        uniq = ", ".join(dict.fromkeys(section_hits))
        logs.append(
            f"State registration: removed unverified {uniq} claim in "
            f"“{section.title or section.id}” (not on verified list)."
        )

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
