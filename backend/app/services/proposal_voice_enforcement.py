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

# Titles fully owned by zö static Sections 1–3 — do not draft again in Phase 3.
_STATIC_COVERED_TITLE_RES = (
    re.compile(r"\bwho\s+we\s+are\b", re.IGNORECASE),
    re.compile(r"\bour\s+promise\b", re.IGNORECASE),
    re.compile(r"\bcompany\s+history\b", re.IGNORECASE),
    re.compile(r"\bfirm\s+history\b", re.IGNORECASE),
    re.compile(r"\bfirm\s+(?:overview|profile|background)\b", re.IGNORECASE),
    re.compile(r"\babout\s+(?:the\s+)?(?:firm|agency|company|proposer|vendor)\b", re.IGNORECASE),
    re.compile(r"\bclient\s+roster\b", re.IGNORECASE),
    re.compile(r"\bcore\s+services\b", re.IGNORECASE),
    re.compile(r"\borganizational?\s+structure\b", re.IGNORECASE),
    re.compile(r"\bbusiness\s+information\b", re.IGNORECASE),
    # Bare "Company Information" essay — owned by 1.3. Offeror/Vendor Identification
    # *forms* stay in the outline (buyer needs the form) but are compressed at draft/scan.
    re.compile(r"^\s*company\s+information\s*$", re.IGNORECASE),
    re.compile(r"\bcertifications?\b", re.IGNORECASE),
    re.compile(r"\binsurance\s+information\b", re.IGNORECASE),
    # Coverage narrative / COI delivery is owned by Section 1.5 — do not draft a
    # second essay under "Certificate of Insurance" in Phase 3.
    re.compile(r"\bcertificate(?:s)?\s+of\s+insurance\b", re.IGNORECASE),
    re.compile(r"\bproof\s+of\s+insurance\b", re.IGNORECASE),
    re.compile(r"\binsurance\s+certificate(?:s)?\b", re.IGNORECASE),
    re.compile(
        r"^\s*(?:coi|insurance\s+coverage|liability\s+insurance)\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"\bcompany\s+overview\b", re.IGNORECASE),
    # RFP TOC "Company Background" is Sections 1.1–1.5, not a second essay.
    re.compile(r"\bcompany\s+background\b", re.IGNORECASE),
    # Section 2 owns full bios — including RFP TOC titles that restate Team Overview
    # with Contract Manager / POC / Personnel Bios/Resumes.
    re.compile(
        r"\bteam\s+overview\b(?:\s*[—\-–:].*)?\b("
        r"bios?|resumes?|personnel|contract\s+manager|point\s+of\s+contact|"
        r"primary\s+contact|staff(?:ing)?"
        r")\b",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*team\s+overview\s*$", re.IGNORECASE),
    re.compile(r"\bpersonnel\s+bios?(?:\s*/\s*resumes?)?\b", re.IGNORECASE),
    re.compile(r"\b(?:staff|team)\s+(?:member\s+)?(?:bios?|resumes?)\b", re.IGNORECASE),
    # Bios-only tabs (Section 2) — do NOT match scored RFP headings like
    # "Qualifications and Experience of the Firm and Key Personnel".
    re.compile(r"^\s*(?:key\s+personnel|team\s+bios?)\s*$", re.IGNORECASE),
    re.compile(r"^\s*staff(?:ing)?\s+(?:bios|resumes|qualifications)\s*$", re.IGNORECASE),
)

# RFP tables of contents often restate a requirement as a whole SENTENCE rather
# than a label: "A brief description of the firm, including the year the firm
# was established, type of firm (partnership, corporation, etc.)". These asks
# were previously matched by _STATIC_COVERED_TITLE_RES and dropped on the
# ASSUMPTION that static Section 1.3 answers them — but static sections are
# generated before Phase 2, never see the RFP, and nothing verified the
# delegation actually landed. They are kept separate here because, unlike the
# label patterns above (a title that names a whole static section 1:1), these
# ask a specific factual question that only the static section's own TEXT can
# answer — see ``static_section_covers_requirement``.
_UNVERIFIED_STATIC_DELEGATION_RES = (
    re.compile(
        r"\b(?:brief\s+)?description\s+of\s+(?:the\s+)?(?:firm|agency|company|"
        r"organi[sz]ation|proposer|vendor)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\byear\s+(?:the\s+)?(?:firm|agency|company|business)\s+was\s+"
        r"(?:established|founded|formed|incorporated)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bdate\s+(?:the\s+)?(?:firm|agency|company|business)\s+was\s+"
        r"(?:established|founded|formed|incorporated)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\btype\s+of\s+(?:firm|entity|organi[sz]ation)\b", re.IGNORECASE),
    re.compile(r"\bform\s+of\s+(?:business|organi[sz]ation)\b", re.IGNORECASE),
    re.compile(r"\blegal\s+(?:structure|entity|form|status)\b", re.IGNORECASE),
    re.compile(
        r"\byears?\s+in\s+business\b|\bhow\s+long\b.{0,40}\bin\s+business\b",
        re.IGNORECASE,
    ),
)

# Answer-shaped signal each unverified-delegation ask requires in the static
# section's own text before it can be treated as covered.
_FIRM_TYPE_ANSWER_RE = re.compile(
    r"\b(corporation|corp\.?|l\.?l\.?c\.?|partnership|sole\s+proprietorship|"
    r"s-?corp(?:oration)?|c-?corp(?:oration)?|nonprofit|non-profit|"
    r"limited\s+liability)\b",
    re.IGNORECASE,
)
_FOUNDING_FACT_ANSWER_RE = re.compile(
    r"\b(19|20)\d{2}\b|\b(established|founded|formed|incorporated|since)\b",
    re.IGNORECASE,
)

# ``None`` = this ask has NO answer shape to look for, so it can never be
# proven mechanically. An earlier revision used a ">= 200 characters of prose"
# heuristic here; that is failing open — 200 characters of entirely unrelated
# prose passes it — which recreates the exact "assume the delegation landed"
# defect this module exists to stop. An open-ended "describe the firm" is now
# never auto-satisfied on its own. It can only be discharged through the
# specific sub-asks it enumerates ("...including the year the firm was
# established, type of firm (partnership, corporation, etc.)"), each of which
# is checked on its own answer shape below.
_DELEGATION_PROOF_CHECKS: tuple[tuple[re.Pattern[str], re.Pattern[str] | None], ...] = (
    (
        re.compile(
            r"\b(?:brief\s+)?description\s+of\s+(?:the\s+)?(?:firm|agency|company|"
            r"organi[sz]ation|proposer|vendor)\b",
            re.IGNORECASE,
        ),
        None,
    ),
    (
        re.compile(
            r"\byear\s+(?:the\s+)?(?:firm|agency|company|business)\s+was\s+"
            r"(?:established|founded|formed|incorporated)\b",
            re.IGNORECASE,
        ),
        _FOUNDING_FACT_ANSWER_RE,
    ),
    (
        re.compile(
            r"\bdate\s+(?:the\s+)?(?:firm|agency|company|business)\s+was\s+"
            r"(?:established|founded|formed|incorporated)\b",
            re.IGNORECASE,
        ),
        _FOUNDING_FACT_ANSWER_RE,
    ),
    (
        re.compile(r"\btype\s+of\s+(?:firm|entity|organi[sz]ation)\b", re.IGNORECASE),
        _FIRM_TYPE_ANSWER_RE,
    ),
    (
        re.compile(r"\bform\s+of\s+(?:business|organi[sz]ation)\b", re.IGNORECASE),
        _FIRM_TYPE_ANSWER_RE,
    ),
    (
        re.compile(r"\blegal\s+(?:structure|entity|form|status)\b", re.IGNORECASE),
        _FIRM_TYPE_ANSWER_RE,
    ),
    (
        re.compile(
            r"\byears?\s+in\s+business\b|\bhow\s+long\b.{0,40}\bin\s+business\b",
            re.IGNORECASE,
        ),
        _FOUNDING_FACT_ANSWER_RE,
    ),
)


def static_section_covers_requirement(requirement_text: str, static_section_text: str) -> bool:
    """Real proof, not an assumption: true only when the static section's own
    text names the specific fact the requirement asks for (entity type,
    founding year, ...) rather than merely sharing a topic label with it.

    Observed: "type of firm" was deleted from the outline whenever the RFP
    text merely contained that phrase, with no check that static Section 1.3
    ever stated whether zö is an LLC, corporation, etc.

    A compound RFP ask enumerates several facts at once ("a brief description
    of the firm, including the year the firm was established, type of firm..."),
    so **every** enumerated fact with a checkable answer shape must appear in
    the static text — proving one of three does not discharge the other two.
    An ask whose only match is the open-ended "describe the firm" pattern has
    nothing checkable and is never auto-satisfied.
    """
    text = (requirement_text or "").strip()
    haystack = static_section_text or ""
    if not text or not haystack.strip():
        return False
    required_answers = [
        answer_re
        for ask_re, answer_re in _DELEGATION_PROOF_CHECKS
        if answer_re is not None and ask_re.search(text)
    ]
    if not required_answers:
        # Either this is not a delegation ask at all, or its only match was the
        # unprovable open-ended one — fail closed, keep the requirement visible.
        return False
    return all(answer_re.search(haystack) for answer_re in required_answers)


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


def is_duplicate_static_rfp_section(
    title: str, *, static_section_text: str | None = None
) -> bool:
    """RFP-mapped sections that duplicate zö static Sections 1–3 (drafted separately).

    ``static_section_text`` is the actual drafted content of static Sections
    1–3, when available — pass it whenever it exists. Without it, a section
    that only *asks a specific factual question* (type of firm, founding year,
    ...) is never treated as covered; unlike the label patterns below, an
    unverified factual ask stays in the outline / is reported missing by the
    requirement ledger rather than being silently assumed satisfied.
    """
    t = title.strip()
    if not t:
        return False
    # Explicit company/team/identity titles → owned by static 1–3.
    if any(pattern.search(t) for pattern in _STATIC_COVERED_TITLE_RES):
        # Keep scored portfolio / sample-work tabs (need Section 3 depth).
        if re.search(
            r"\b(sample\s+work|portfolio|minimum\s+two|recent\s+campaign)\b",
            t,
            re.IGNORECASE,
        ):
            return False
        # Keep RFP-scored capability / agency-requirements matrices.
        if re.search(
            r"\b(agency\s+requirements?|capability\s+matrix|service\s+capability|"
            r"scope\s+of\s+work|statement\s+of\s+work)\b",
            t,
            re.IGNORECASE,
        ):
            return False
        return True
    if any(pattern.search(t) for pattern in _UNVERIFIED_STATIC_DELEGATION_RES):
        return bool(
            static_section_text
            and static_section_covers_requirement(t, static_section_text)
        )
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


def should_skip_rfp_section_as_static_duplicate(
    *,
    title: str,
    duplicate_of_static_section: str | None = None,
    evaluation_weight: float | None = None,
    static_section_text: str | None = None,
) -> bool:
    """True when intelligence/drafting must omit this tab (already covered by Sections 1–3).

    A section carrying evaluation points is never skipped here — Phase 2 named
    it a scored criterion, and dropping it produced proposals missing
    "Technical Approach" and similar scored asks. Static delegation is a
    convenience for unscored identity boilerplate, not for what evaluators
    score.
    """
    if evaluation_weight is not None:
        try:
            if float(evaluation_weight) > 0:
                return False
        except (TypeError, ValueError):
            pass
    # Cost / Fees / Budget tabs are never "static duplicate" — Phase 3.5 needs them.
    if re.search(
        r"\b("
        r"cost\s+proposal|cost\s+of(?:\s+the)?\s+base|fee\s+schedule|"
        r"price\s+proposal|pricing\s+proposal|compensation\s+schedule|"
        r"budget\s*(?:&|and)\s*pricing|budget\s+and\s+fees|"
        r"\bbudget\b|\bpricing\b|\bfees?\b"
        r")\b",
        title or "",
        re.IGNORECASE,
    ):
        return False
    dup = (duplicate_of_static_section or "").strip().casefold()
    if dup in {"section-1", "section-2", "section-3", "1", "2", "3"}:
        if re.search(
            r"\b(sample\s+work|portfolio|minimum\s+two|recent\s+campaign|"
            r"agency\s+requirements?|capability\s+matrix)\b",
            title or "",
            re.IGNORECASE,
        ):
            return is_duplicate_static_rfp_section(
                title, static_section_text=static_section_text
            )
        return True
    return is_duplicate_static_rfp_section(title, static_section_text=static_section_text)
