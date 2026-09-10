"""Never assert business registration in a jurisdiction companyfacts does not list.

Companyfacts / the manuscript's own State Registrations inventory is the source of
truth. A signed transmittal that claims Maryland (or any other state) while the
verified list is Oregon, Washington, Texas, Colorado, and California is a
checkable legal falsehood.

Geography names are used only to parse that inventory vs claims — not to match
RFP requirements to KB evidence.

Do NOT expand claim-phrase synonym tables when a new wording slips through.
When this guard previously wrote a "do not assert X" MANUAL FILL and X later
appears on the verified inventory, drop that stale fill — never leave claim +
contradictory fill side by side.
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

# Existing claim shapes only — do not grow this list for new LLM wordings.
# Stale-fill reconciliation below covers the contradiction bug without synonyms.
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

# Phrases THIS module writes into MANUAL FILL tags (artifact identity, not claim detection).
_OWN_FILL_MARKERS: tuple[str, ...] = (
    "do not assert",
    "business registration is not on the verified",
    "until it appears in companyfacts",
)


def _strip_bracket_tags(text: str) -> str:
    """Remove [MANUAL FILL]/[VERIFY]/… so fills cannot seed the inventory."""
    pieces: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        start = text.find("[", i)
        if start < 0:
            pieces.append(text[i:])
            break
        end = text.find("]", start + 1)
        if end < 0:
            pieces.append(text[i:])
            break
        pieces.append(text[i:start])
        i = end + 1
    return "".join(pieces)


def _is_inventory_section(section: ProposalSection) -> bool:
    sid = (section.id or "").casefold()
    title = (section.title or "").casefold()
    if sid.startswith("section-1-business"):
        return True
    if "state registration" in title:
        return True
    # Heading-based weave only — never treat MANUAL FILL prose that mentions
    # "State Registrations" as the verified inventory (that polluted CA claims).
    body = _strip_bracket_tags(section.content or "")
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and "state registration" in stripped.casefold():
            return True
    return False


def _inventory_text(draft: ProposalDraft, research: ProposalResearchCache | None) -> str:
    parts: list[str] = []
    for section in draft.sections or []:
        if _is_inventory_section(section):
            # Bracket tags must not contribute jurisdiction names.
            parts.append(_strip_bracket_tags(section.content or ""))
    if research:
        for item in research.evidence_corpus or []:
            source = f"{getattr(item, 'source', '')} {getattr(item, 'chunk_key', '')}"
            excerpt = getattr(item, "excerpt", "") or ""
            blob = f"{source}\n{excerpt}"
            if "companyfacts" in blob.casefold() or "state registration" in blob.casefold():
                parts.append(_strip_bracket_tags(blob))
    return "\n".join(parts)


def verified_registration_jurisdictions(
    draft: ProposalDraft,
    research: ProposalResearchCache | None = None,
) -> list[str]:
    """Jurisdictions listed in Section 1.3 / companyfacts State Registrations."""
    blob = _inventory_text(draft, research).casefold()
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


def _is_own_registration_fill(inner: str) -> bool:
    cf = inner.casefold()
    if not cf.startswith("manual fill"):
        return False
    return any(marker in cf for marker in _OWN_FILL_MARKERS)


def _fill_targets_verified_jurisdiction(inner: str, verified_cf: set[str]) -> bool:
    """True when this guard's fill names a jurisdiction that is now verified."""
    cf = inner.casefold()
    for name in _US_JURISDICTIONS:
        if name.casefold() not in verified_cf:
            continue
        if name.casefold() in cf:
            return True
    return False


def strip_stale_registration_manual_fills(content: str, verified: list[str]) -> tuple[str, int]:
    """Drop this-guard MANUAL FILLs that contradict a now-verified jurisdiction.

    Root cause of claim + 'do not assert California' sitting together: an earlier
    pass wrote the fill when inventory was empty; a later pass kept a true claim
    once companyfacts listed CA, but left the stale fill in place.
    """
    if not content or not verified:
        return content or "", 0
    verified_cf = {name.casefold() for name in verified}
    pieces: list[str] = []
    i = 0
    n = len(content)
    removed = 0
    while i < n:
        start = content.find("[", i)
        if start < 0:
            pieces.append(content[i:])
            break
        end = content.find("]", start + 1)
        if end < 0:
            pieces.append(content[i:])
            break
        inner = content[start + 1 : end]
        if _is_own_registration_fill(inner) and _fill_targets_verified_jurisdiction(
            inner, verified_cf
        ):
            pieces.append(content[i:start])
            i = end + 1
            if i < n and content[i] in " ,;":
                i += 1
            removed += 1
            continue
        pieces.append(content[i : end + 1])
        i = end + 1
    out = "".join(pieces) if removed else content

    # Also drop bare / table-cell copies of the same instruction (no brackets).
    lines_out: list[str] = []
    for line in out.splitlines(keepends=True):
        body = line.strip()
        cf = body.casefold()
        # Pipe cells: scrub chrome that repeats the do-not-assert instruction.
        if body.startswith("|"):
            cells = [c.strip() for c in body.strip("|").split("|")]
            new_cells: list[str] = []
            row_changed = False
            for cell in cells:
                cell_cf = cell.casefold()
                if any(m in cell_cf for m in _OWN_FILL_MARKERS) and _fill_targets_verified_jurisdiction(
                    cell, verified_cf
                ):
                    row_changed = True
                    removed += 1
                    continue
                new_cells.append(cell)
            if row_changed:
                if not new_cells or all(
                    not c or set(c) <= {"-", ":"} for c in new_cells
                ):
                    continue
                ending = "\n" if line.endswith("\n") else ""
                lines_out.append("| " + " | ".join(new_cells) + " |" + ending)
                continue
        if any(m in cf for m in _OWN_FILL_MARKERS) and _fill_targets_verified_jurisdiction(
            body, verified_cf
        ):
            removed += 1
            continue
        lines_out.append(line)
    if removed:
        out = "".join(lines_out)
    if not removed:
        return content, 0
    while "  " in out:
        out = out.replace("  ", " ")
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out.strip() + ("\n" if content.endswith("\n") else ""), removed


def scrub_unverified_state_registration_claims(
    draft: ProposalDraft,
    research: ProposalResearchCache | None = None,
) -> tuple[ProposalDraft, list[str]]:
    """Replace unverified 'registered in X' facts with MANUAL FILL; drop stale fills."""
    verified = verified_registration_jurisdictions(draft, research)
    verified_cf = {name.casefold() for name in verified}
    logs: list[str] = []
    sections = list(draft.sections or [])
    changed = False

    for idx, section in enumerate(sections):
        body = section.content or ""
        if not body.strip():
            continue

        new_body = body
        # Always reconcile stale fills (including inventory tabs / Strict weave).
        new_body, removed = strip_stale_registration_manual_fills(new_body, verified)
        if removed:
            logs.append(
                f"State registration: removed {removed} stale MANUAL FILL(s) in "
                f"“{section.title or section.id}” (jurisdiction now verified)."
            )

        if not _is_inventory_section(section):
            pieces = _split_keep_delim(new_body)
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
            if section_hits:
                new_body = "".join(new_pieces)
                uniq = ", ".join(dict.fromkeys(section_hits))
                logs.append(
                    f"State registration: removed unverified {uniq} claim in "
                    f"“{section.title or section.id}” (not on verified list)."
                )

        if new_body == body:
            continue
        sections[idx] = section.model_copy(update={"content": new_body})
        changed = True

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
