"""Deterministic narrative voice enforcement — fixes Vendor register regardless of LLM model."""

from __future__ import annotations

import re

from app.services.proposal_brand_voice import Register, classify_section_register

_PROCUREMENT_ENTITY = re.compile(
    r"\b([Tt])he (Vendor|Offeror|Proposer|Respondent|Contractor)('s)?\b"
)
_AGENCY_THIRD = re.compile(r"\b([Tt])he agency('s)?\b")
_FIRM_THIRD = re.compile(r"\b([Tt])he firm('s)?\b")
_ZO_THIRD = re.compile(
    r"\bzö agency (?:delivers|brings|provides|maintains|confirms|offers|has|is)\b",
    re.IGNORECASE,
)

_SUBSECTION_VENDOR_HEADER = re.compile(
    r"(\d+(?:\.\d+)*\s+)(Vendor Identification\b)",
    re.IGNORECASE,
)

_STATIC_RFP_DUPLICATE_RES = (
    re.compile(r"section\s*1\b", re.IGNORECASE),
    re.compile(r"company\s+overview", re.IGNORECASE),
    re.compile(r"section\s*2\b", re.IGNORECASE),
    re.compile(r"team\s+(overview|bios|qualifications|experience)", re.IGNORECASE),
    re.compile(r"section\s*3\b", re.IGNORECASE),
    re.compile(r"(case\s+stud|our\s+work|past\s+performance|relevant\s+experience)", re.IGNORECASE),
)


def contains_vendor_language(content: str) -> bool:
    return bool(
        _PROCUREMENT_ENTITY.search(content)
        or _AGENCY_THIRD.search(content)
        or _FIRM_THIRD.search(content)
    )


def _swap_entity(match: re.Match[str]) -> str:
    cap = match.group(1)
    possessive = match.group(3)
    if possessive:
        return "Our" if cap == "T" else "our"
    return "We" if cap == "T" else "we"


def _swap_agency_firm(match: re.Match[str]) -> str:
    cap = match.group(1)
    possessive = match.group(2)
    if possessive:
        return "Our" if cap == "T" else "our"
    return "We" if cap == "T" else "we"


_WE_VERB_AGREEMENT = re.compile(
    r"\bWe (is|was|confirms|maintains|operates|has|brings|delivers|provides|"
    r"offers|includes|submits)\b",
    re.IGNORECASE,
)

_VERB_TO_PLURAL = {
    "is": "are",
    "was": "were",
    "confirms": "confirm",
    "maintains": "maintain",
    "operates": "operate",
    "has": "have",
    "brings": "bring",
    "delivers": "deliver",
    "provides": "provide",
    "offers": "offer",
    "includes": "include",
    "submits": "submit",
}


def _fix_we_verb_agreement(text: str) -> str:
    def fix(match: re.Match[str]) -> str:
        verb = match.group(1)
        fixed = _VERB_TO_PLURAL.get(verb.lower(), verb.lower())
        if verb[0].isupper():
            fixed = fixed.capitalize()
        return f"We {fixed}"

    return _WE_VERB_AGREEMENT.sub(fix, text)


def apply_writing_standards_mechanics(content: str) -> str:
    """Deterministic rev 3 mechanics: company name + no em dashes."""
    if not content.strip():
        return content

    text = content
    # Em dashes / en dashes used as clause breaks → comma or hyphen for ranges.
    text = text.replace("—", ",")
    text = text.replace("–", "-")
    # Common wrong company-name spellings → zö agency
    text = re.sub(r"\bZO\s+Agency\b", "zö agency", text)
    text = re.sub(r"\bZÖ\s+Agency\b", "zö agency", text)
    text = re.sub(r"\bZö\s+Agency\b", "zö agency", text)
    text = re.sub(r"\bZo\s+Agency\b", "zö agency", text)
    text = re.sub(r"\bzo\s+agency\b", "zö agency", text)
    # Cleanup double commas / spaces from dash swaps
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"[ \t]+,", ",", text)
    return text


def fix_narrative_register(content: str) -> str:
    """Rewrite third-person procurement phrasing to first-person zö voice."""
    if not content.strip():
        return content

    text = apply_writing_standards_mechanics(content)
    text = _SUBSECTION_VENDOR_HEADER.sub(
        r"\1Company Identification",
        text,
    )
    text = _PROCUREMENT_ENTITY.sub(_swap_entity, text)
    text = _AGENCY_THIRD.sub(_swap_agency_firm, text)
    text = _FIRM_THIRD.sub(_swap_agency_firm, text)
    text = re.sub(r"\bOur's\b", "Our", text)
    text = re.sub(r"\bour's\b", "our", text)

    text = _ZO_THIRD.sub(
        lambda m: "We " + m.group(0).split(maxsplit=2)[-1].lower(),
        text,
    )
    text = _fix_we_verb_agreement(text)

    return text


def enforce_narrative_voice(
    content: str,
    *,
    section_id: str = "",
    title: str = "",
    zo_mode: str = "write",
    register: Register | None = None,
) -> str:
    reg = register or classify_section_register(
        section_id=section_id,
        title=title,
        zo_mode=zo_mode,
    )
    if reg != "narrative":
        return apply_writing_standards_mechanics(content)
    return fix_narrative_register(content)


def is_duplicate_static_rfp_section(title: str) -> bool:
    """RFP-mapped sections that duplicate zö static Sections 1–3 (drafted separately)."""
    t = title.strip()
    if not t:
        return False
    hits = sum(1 for pattern in _STATIC_RFP_DUPLICATE_RES if pattern.search(t))
    if hits >= 2:
        return True
    if re.search(r"section\s*[123]\b", t, re.IGNORECASE) and re.search(
        r"overview|company|team|work|case", t, re.IGNORECASE
    ):
        return True
    if re.fullmatch(
        r"section\s*1\s*[—\-–:]\s*company\s+overview",
        t,
        re.IGNORECASE,
    ):
        return True
    if re.fullmatch(
        r"section\s*2\s*[—\-–:]\s*team\s+overview",
        t,
        re.IGNORECASE,
    ):
        return True
    if re.fullmatch(
        r"section\s*3\s*[—\-–:]\s*our\s+work.*",
        t,
        re.IGNORECASE,
    ):
        return True
    return False
