"""Bind requirement coverage to sections that actually exist in the draft.

Phase 2 can stamp ``satisfied_by`` with ``ledger-*`` ids, then a later lean
filter or section cap drops those tabs. The ledger still says covered.

Atoms (split children of one compliance row) are sub-asks of the parent
requirement. They do not get their own tab. A rewrite ticket is attached only
when the parent requirement already has a live section. Nothing here maps
topics to section titles.
"""

from __future__ import annotations

import re
from typing import Any

from app.models.proposal import ProposalSection
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.services.proposal_rfp_compliance import requirement_likely_covered

_DESIGNER_NOTE = re.compile(r"\[DESIGNER NOTE:[^\]]*\]", re.I)
_MANUAL_FILL = re.compile(r"\[MANUAL FILL:[^\]]*\]", re.I)
_HEADING = re.compile(r"^#{1,6}\s+.*$", re.M)

# A form-attach stub ("Required form per the RFP…") is not an answer.
_STUB_WORD_FLOOR = 45
_STUB_PROSE_FLOOR = 40


def is_stub_answer(content: str) -> bool:
    """True for designer-note shells and other bodies that do not answer an RFP."""
    raw = content or ""
    if len(raw.split()) < _STUB_WORD_FLOOR:
        return True
    stripped = _HEADING.sub(" ", _MANUAL_FILL.sub(" ", _DESIGNER_NOTE.sub(" ", raw)))
    words = [w for w in stripped.split() if w.strip("*_-")]
    if len(words) < _STUB_PROSE_FLOOR:
        return True
    low = " ".join(words).casefold()
    if "required form per the rfp" in low and len(words) < 80:
        return True
    return False


def coverage_haystack(sections: list[ProposalSection]) -> str:
    """Manuscript text the ending-report keyword check is allowed to see.

    Stub sections are omitted. A heading that repeats the requirement inside
    a 30-word "attach the form" note was counting as covered.
    """
    parts: list[str] = []
    for section in sections:
        body = section.content or ""
        if not body.strip() or is_stub_answer(body):
            continue
        parts.append(f"## {section.title}\n{body}")
    return "\n\n".join(parts)


def requirement_is_atom(requirement: LedgerRequirement, requirement_ids: set[str]) -> bool:
    parent = (requirement.atom_of or "").strip()
    if parent:
        return True
    rid = requirement.id or ""
    if len(rid) >= 3 and rid[-2] == "-" and rid[-1].isalpha() and rid[:-2] in requirement_ids:
        return True
    return False


def rebind_ledger_to_section_ids(
    ledger: RequirementLedger | None,
    live_section_ids: set[str],
) -> RequirementLedger:
    """Drop ``satisfied_by`` ids that are not in the live outline or draft."""
    if ledger is None:
        return RequirementLedger()
    rebound: list[LedgerRequirement] = []
    for requirement in ledger.requirements:
        kept = [sid for sid in requirement.satisfied_by if sid in live_section_ids]
        if kept == list(requirement.satisfied_by):
            rebound.append(requirement)
            continue
        rebound.append(requirement.model_copy(update={"satisfied_by": kept}))
    return ledger.model_copy(update={"requirements": rebound})


def _answered_in_substantive(
    text: str, sections: list[ProposalSection]
) -> ProposalSection | None:
    for section in sections:
        body = section.content or ""
        if is_stub_answer(body):
            continue
        if requirement_likely_covered(text, body):
            return section
    return None


def rebind_ledger_to_draft(
    ledger: RequirementLedger | None,
    sections: list[ProposalSection],
) -> RequirementLedger:
    """Point each requirement at a live section that actually answers it.

    Ghost ids are removed. If some other substantive section already answers
    the text, ``satisfied_by`` moves there. Otherwise the claim is cleared.
    """
    live_ids = {s.id for s in sections}
    rebound = rebind_ledger_to_section_ids(ledger, live_ids)
    updated: list[LedgerRequirement] = []
    for requirement in rebound.requirements:
        if requirement.source != "required_content" or not requirement.mandatory:
            updated.append(requirement)
            continue
        owners = [s for s in sections if s.id in requirement.satisfied_by]
        owners_answer = [
            s for s in owners if not is_stub_answer(s.content or "") and requirement_likely_covered(requirement.text, s.content or "")
        ]
        if owners_answer:
            updated.append(
                requirement.model_copy(update={"satisfied_by": [owners_answer[0].id]})
            )
            continue
        elsewhere = _answered_in_substantive(requirement.text, sections)
        if elsewhere is not None:
            updated.append(requirement.model_copy(update={"satisfied_by": [elsewhere.id]}))
            continue
        if requirement.satisfied_by:
            updated.append(requirement.model_copy(update={"satisfied_by": []}))
            continue
        updated.append(requirement)
    return rebound.model_copy(update={"requirements": updated})


def _parent_requirement_id(
    requirement: LedgerRequirement, requirement_ids: set[str]
) -> str:
    parent = (requirement.atom_of or "").strip()
    if parent:
        return parent
    rid = requirement.id or ""
    if len(rid) >= 3 and rid[-2] == "-" and rid[-1].isalpha() and rid[:-2] in requirement_ids:
        return rid[:-2]
    return ""


def _live_substantive_id(
    requirement: LedgerRequirement, by_id: dict[str, ProposalSection]
) -> str | None:
    for sid in requirement.satisfied_by:
        owner = by_id.get(sid)
        if owner is not None and not is_stub_answer(owner.content or ""):
            return sid
    return None


def _route_section_id(
    requirement: LedgerRequirement,
    ledger: RequirementLedger,
    sections: list[ProposalSection],
) -> str | None:
    """Section that already owns this ask, from the ledger's own links.

    An atom follows its parent requirement's live section. A requirement
    follows a sibling that shares its claimed section id and already has a
    live section. There is no topic-to-title map.
    """
    by_id = {s.id: s for s in sections}
    requirement_ids = {r.id for r in ledger.requirements}
    by_req = {r.id: r for r in ledger.requirements}
    parent_id = _parent_requirement_id(requirement, requirement_ids)
    if parent_id and parent_id in by_req:
        live = _live_substantive_id(by_req[parent_id], by_id)
        if live:
            return live
    ghosts = [sid for sid in requirement.satisfied_by if sid not in by_id]
    if not ghosts:
        return _live_substantive_id(requirement, by_id)
    for other in ledger.requirements:
        if other.id == requirement.id:
            continue
        if not any(ghost in other.satisfied_by for ghost in ghosts):
            continue
        live = _live_substantive_id(other, by_id)
        if live:
            return live
    return None


def unbound_requirement_tickets(
    ledger: RequirementLedger | None,
    sections: list[ProposalSection],
) -> list[dict[str, Any]]:
    """One rewrite ticket per target section for requirements the draft does not answer.

    Fires when ``satisfied_by`` named a section that is gone or is only a stub,
    and when an atom's live section never mentions that atom. Requirements that
    are already answered in substantive prose are not ticketed.
    """
    if ledger is None or not sections:
        return []
    requirement_ids = {r.id for r in ledger.requirements}
    by_id = {s.id: s for s in sections}
    by_target: dict[str, list[str]] = {}
    for requirement in ledger.requirements:
        if requirement.source != "required_content" or not requirement.mandatory:
            continue
        if _answered_in_substantive(requirement.text, sections) is not None:
            continue
        claimed = list(requirement.satisfied_by)
        atom = requirement_is_atom(requirement, requirement_ids)
        substantive_claim = False
        for sid in claimed:
            owner = by_id.get(sid)
            if owner is not None and not is_stub_answer(owner.content or ""):
                substantive_claim = True
                break
        # Non-empty satisfied_by that is not a live substantive section is the
        # false "covered" stamp. Atoms are ticketed even with an empty stamp,
        # because they never receive their own tab.
        false_claim = bool(claimed) and not substantive_claim
        if not atom and not false_claim:
            continue
        target = _route_section_id(requirement, ledger, sections)
        if not target:
            continue
        by_target.setdefault(target, [])
        if requirement.text not in by_target[target]:
            by_target[target].append(requirement.text)

    tickets: list[dict[str, Any]] = []
    for section_id, texts in by_target.items():
        lines = "\n".join(f"- {text}" for text in texts[:24])
        tickets.append(
            {
                "sectionId": section_id,
                "source": "unbound-requirement",
                "unmetRequirements": texts[:24],
                "rewriteBrief": (
                    "Answer each of these RFP requirements inside this section. "
                    "Do not add a new tab. Do not invent staff, certifications, "
                    "clients, or technical claims. Where the knowledge base has "
                    "no proof, write one [MANUAL FILL: Sonja — …] tag for that item.\n"
                    f"{lines}"
                ),
            }
        )
    return tickets
