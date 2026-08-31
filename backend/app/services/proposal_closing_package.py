"""Closing package adapters — ledger is authority; this module exposes draft helpers.

Closing *detection* lives in ``proposal_closing_ledger`` (LLM extract). This module
keeps ``ClosingComponent`` for existing draft/fulfill callers and converts ledger
rows. The regex ``_CLOSING_CATALOG`` is retired as authority.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.models.proposal import ProposalDraft
from app.services.proposal_closing_ledger import (
    ClosingRequirement,
    ClosingRequirementLedger,
    extract_closing_requirement_ledger,
    find_covering_section,
    ledger_from_fixture,
)
from app.services.proposal_rfp_excerpt import (
    extract_reference_requirement_summary,
    rfp_forbids_quotation_form_changes,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClosingComponent:
    """One closing package item demanded by the RFP (from the ledger)."""

    id: str
    title: str
    section_id: str
    kind: str  # narrative | form | attachment | signature
    match_hint: str
    draft_instructions: str


def requirement_to_closing_component(
    requirement: ClosingRequirement,
    *,
    rfp_text: str = "",
) -> ClosingComponent:
    instructions = requirement.draft_instructions or (
        f"Address '{requirement.title}' exactly as THIS RFP requires. "
        "Use [MANUAL FILL] for signatures and attach-PDF handoffs. "
        "Do not invent certifications or Ready status."
    )
    if requirement.id == "references":
        spec = extract_reference_requirement_summary(rfp_text)
        if spec:
            instructions = (
                "The RFP specifies reference requirements — state them accurately:\n"
                f"{spec}\n\n{instructions}"
            )
    elif "pric" in requirement.id or "quotation" in requirement.id:
        if rfp_forbids_quotation_form_changes(rfp_text):
            instructions = (
                f"{instructions}\n"
                "CRITICAL: This RFP disqualifies bids that alter the official "
                "Quotation/Pricing Proposal Form. Do NOT invent Section A/B/C/D structure."
            )
    blob = f"{requirement.id} {requirement.title} {requirement.kind}".casefold()
    if any(
        tok in blob
        for tok in (
            "insurance",
            "coi",
            "w-9",
            "w9",
            "attachment",
            "exemplar",
            "agreement",
        )
    ):
        if "do not restate" not in instructions.casefold():
            instructions = (
                f"{instructions}\n"
                "Section 1.5 Insurance Information already states zö's coverage; "
                "do NOT restate limits, carriers or coverage types here — "
                "cross-reference Section 1.5 only. NEVER mark insurance rows Compliant "
                "unless Section 1.5 lists that coverage — use [MANUAL FILL: Sonja — "
                "confirm on COI] instead."
            )
    return ClosingComponent(
        id=requirement.id,
        title=requirement.title,
        section_id=requirement.section_id,
        kind=requirement.kind,
        match_hint=requirement.rfp_label or requirement.title,
        draft_instructions=instructions,
    )



def components_from_ledger(
    ledger: ClosingRequirementLedger,
    *,
    rfp_text: str = "",
    always_include_commitment: bool = False,
) -> list[ClosingComponent]:
    """Convert ledger rows to ClosingComponent list for draft/outline paths."""
    from app.services.proposal_closing_ledger import _commitment_requirement

    reqs = list(ledger.requirements)
    if always_include_commitment and not any(r.id == "offeror_commitment" for r in reqs):
        reqs.append(_commitment_requirement())
    return [requirement_to_closing_component(r, rfp_text=rfp_text) for r in reqs]


async def detect_closing_components_async(
    rfp_text: str,
    *,
    always_include_commitment: bool = False,
) -> list[ClosingComponent]:
    """Ledger-backed closing detection (authority)."""
    ledger = await extract_closing_requirement_ledger(
        rfp_text,
        always_include_commitment=always_include_commitment,
    )
    return components_from_ledger(
        ledger,
        rfp_text=rfp_text,
        always_include_commitment=False,
    )


def detect_closing_components(
    rfp_text: str,
    *,
    always_include_commitment: bool = False,
    ledger: ClosingRequirementLedger | None = None,
) -> list[ClosingComponent]:
    """Sync adapter. Prefer ``ledger=`` or ``detect_closing_components_async``.

    Without a ledger, returns empty (regex catalog retired). Callers in async
    pipelines must extract the ledger first.
    """
    if ledger is not None:
        return components_from_ledger(
            ledger,
            rfp_text=rfp_text,
            always_include_commitment=always_include_commitment,
        )
    if always_include_commitment:
        return components_from_ledger(
            ledger_from_fixture([]),
            rfp_text=rfp_text or "",
            always_include_commitment=True,
        )
    logger.info(
        "detect_closing_components called without ledger — returning empty "
        "(regex catalog retired; use extract_closing_requirement_ledger)."
    )
    return []


def draft_already_covers_component(
    *,
    draft_section_ids: set[str],
    draft_titles: list[str],
    component: ClosingComponent,
    draft: ProposalDraft | None = None,
) -> bool:
    """True when the manuscript already has a tab for this ledger component."""
    if component.section_id in draft_section_ids and draft is not None:
        by_id = {s.id: s for s in draft.sections}
        direct = by_id.get(component.section_id)
        if direct is not None:
            from app.services.proposal_draft_structure_stubs import (
                cover_letter_lacks_letter_body,
                is_cover_letter_section_title,
            )

            component_blob = f"{component.title} {component.match_hint}".casefold()
            is_cover_ask = any(
                tok in component_blob
                for tok in ("cover letter", "transmittal", "letter of offer")
            )
            if is_cover_ask or is_cover_letter_section_title(direct.title or ""):
                if cover_letter_lacks_letter_body(direct.content or ""):
                    return False
            return True
    elif component.section_id in draft_section_ids:
        return True
    if draft is not None:
        kind = component.kind if component.kind in {
            "narrative", "form", "attachment", "signature"
        } else "form"
        req = ClosingRequirement(
            id=component.id,
            title=component.title,
            kind=kind,  # type: ignore[arg-type]
            rfpLabel=component.match_hint,
            sectionId=component.section_id,
            draftInstructions=component.draft_instructions,
        )
        covering = find_covering_section(draft, req)
        if covering is None:
            return False
        from app.services.proposal_draft_structure_stubs import (
            cover_letter_lacks_letter_body,
            is_cover_letter_section_title,
        )

        component_blob = f"{component.title} {component.match_hint}".casefold()
        is_cover_ask = any(
            tok in component_blob for tok in ("cover letter", "transmittal", "letter of offer")
        )
        if is_cover_ask or is_cover_letter_section_title(covering.title or ""):
            if cover_letter_lacks_letter_body(covering.content or ""):
                return False
        return True
    from app.services.proposal_closing_ledger import _tokens

    needles = _tokens(component.title) | _tokens(component.match_hint) | _tokens(
        component.id.replace("_", " ")
    )
    if not needles:
        return False
    for title in draft_titles:
        if len(needles & _tokens(title)) >= 2:
            return True
        title_cf = (title or "").casefold()
        hint = (component.match_hint or component.title or "").casefold()
        if hint and (hint in title_cf or title_cf in hint):
            return True
    return False


_OBLIGATION_VERB = (
    r"(?:return(?:ed)?|submit(?:ted)?|includ(?:e|ed)|complet(?:e|ed)|"
    r"sign(?:ed)?|acknowledg(?:e|ed)|provid(?:e|ed)|attach(?:ed)?|"
    r"furnish(?:ed)?|enclos(?:e|ed))"
)

_SUBMISSION_OBLIGATION_RE = re.compile(
    rf"""(?ix)
    (?:
        \b (?:must|shall|will|is|are) \s+ (?:be \s+)? (?:\w+ \s+){{0,2}}?
          {_OBLIGATION_VERB} \b
      | \b (?:is|are) \s+ required \b
      | \b required \s+ (?:\w+ \s+){{0,2}} (?:form|document|attachment|
          submittal|exhibit|item|material)s? \b
      | \b submission \s+ (?:document|requirement|material|item)s? \b
      | \b (?:submit|return|enclose|attach|furnish) \b
      | \b (?:proposal|quote|submittal|response|bid) \s+ (?:shall|must|should)
          \s+ (?:contain|includ(?:e)|consist) \b
      | \b failure \s+ to \s+ (?:return|submit|include|provide|acknowledge) \b
      | \b (?:with|as \s+ part \s+ of) \s+ (?:the \s+|your \s+)?
          (?:proposal|quote|submittal|response|bid) \b
    )
    """
)


def rfp_requires_topic(rfp_text: str, topic_terms: list[str]) -> bool:
    """True when the RFP asks the vendor to SUBMIT something about ``topic_terms``."""
    body = rfp_text or ""
    if not body or not topic_terms:
        return False
    for term in topic_terms:
        term = (term or "").strip()
        if len(term) < 4:
            continue
        for match in re.finditer(re.escape(term), body, re.IGNORECASE):
            start = max(
                body.rfind(".", 0, match.start()),
                body.rfind("\n\n", 0, match.start()),
            )
            end_dot = body.find(".", match.end())
            end_para = body.find("\n\n", match.end())
            candidates = [e for e in (end_dot, end_para) if e != -1]
            end = min(candidates) if candidates else len(body)
            sentence = body[start + 1 : end]
            if _SUBMISSION_OBLIGATION_RE.search(sentence):
                return True
    return False
