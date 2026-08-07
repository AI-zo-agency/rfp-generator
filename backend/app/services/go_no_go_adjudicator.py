"""Judge capability evidence semantically, but prove it with a verbatim quote.

Two failure modes led here, and they are opposites:

  1. The model was left to assert "Verified" freely -> it fabricated matches for
     CMS, hosting and content migration that the KB never contained.
  2. That was replaced with stemmed keyword overlap -> it could not see that
     WordPress IS a CMS, that "improve clarity and user flow" is UX evidence, or
     that "Articles and Resources page redesign" is information architecture.
     It reported 0 of 13 requirements evidenced when roughly 5 were.

Both come from fusing two separate concerns. Relevance is a semantic judgment a
keyword matcher cannot make; non-fabrication is a mechanical property a model
cannot guarantee. So they are split:

  * the model decides whether a retrieved document evidences a requirement, and
  * must return a VERBATIM quote from that document's retrieved text, which is
    then checked mechanically.

A quote that does not appear in the text it cites is dropped. The model gains
semantic judgment; it gains no ability to invent evidence.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.go_no_go import GoNoGoCapabilityRow
from app.services.go_no_go_capability import _tokens, build_source_index
from app.services.evidence_trust.personnel_grounding import personnel_claim_failure

logger = logging.getLogger(__name__)

ADJUDICATOR_PROMPT = """You decide whether zö agency's knowledge base evidences each RFP requirement.

For every requirement you are given the KB documents retrieved for it. Decide:
  "verified" - a document clearly evidences the capability
  "partial"  - a document evidences a related or narrower form of it
  "gap"      - no document evidences it

Judge by MEANING, not wording. WordPress work IS content-management-system
experience. "Improve clarity and user flow" IS user-experience evidence.
A page-structure redesign IS information architecture. Do not require the
document to repeat the RFP's phrasing.

But do NOT stretch across a real difference: content development is not content
migration; print/brand design is not web development; branding for a city is not
building that city a website; a pricing guide is not proof of delivery
capability. Sector matters — private-sector website work does not evidence
"government website experience", though it does evidence "website redesign".

NEVER invent or confirm staff names that are not verbatim in the cited excerpt.
Known fabrications include Brittany Frazier, Drew Stone, Ben Edwards, Erica
Schultz, Morgan Nivan — if a requirement or your reason names them, status=gap.
The real Creative Director in zö materials is Curt Schultz when the roster says so.

If a document DISCLAIMS the skill ("Web Design/Development (Not Programming)"),
that is a gap for the disclaimed part.

For "verified" or "partial" you MUST return:
  kbSource - the exact document name as given, and
  quote    - a VERBATIM span copied character-for-character from that document's
             excerpt. Do not paraphrase, correct, shorten with ellipses, or
             merge lines. Every quote is checked against the source text and
             dropped if it does not appear, which downgrades the row to a gap.

For "gap" also return evidenceState, because these are different findings:
  "absent"       - nothing in the KB addresses this at all
  "contradicted" - the KB explicitly disclaims it (e.g. a bio reading
                   "Web Design/Development (Not Programming)")
  "adjacent"     - the KB has related but materially different work
                   (content development where migration was asked for;
                    private-sector websites where government is required)

Return ONLY JSON:
{"assessments":[{"requirement":"...","status":"verified|partial|gap",
  "kbSource":"...","quote":"...","evidenceState":"absent|contradicted|adjacent",
  "reason":"one short sentence"}]}"""

_MAX_DOC_CHARS = 2_500
_MAX_DOCS_PER_REQUIREMENT = 8
# Quotes are normalized before comparison: models reliably alter whitespace even
# when copying faithfully, and failing an honest quote on a line break would
# push us straight back into false negatives.
_WS_RE = re.compile(r"\s+")
_MIN_QUOTE_CHARS = 12


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", (text or "")).strip().casefold()


# Documents that describe what zö CHARGES or how it is ORGANISED, never what it
# has DELIVERED. A live run validated "Discovery and stakeholder engagement"
# against 00_Guide_Pricing.docx purely because the guide's text mentioned those
# words. Pricing sheets are not delivery evidence, whatever they contain.
_NON_CAPABILITY_SOURCE_RE = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:00_guide_pricing|05_pricing|pricing[_\s-]*guide|"
    r"rate[_\s-]*card|price[_\s-]*(?:list|sheet))"
)


def source_can_evidence_capability(kb_source: str) -> bool:
    """False for documents that cannot prove delivery capability."""
    return not _NON_CAPABILITY_SOURCE_RE.search(kb_source or "")


def quote_is_grounded(quote: str, source_text: str) -> bool:
    """True when ``quote`` really appears in ``source_text``."""
    needle = _normalize(quote)
    if len(needle) < _MIN_QUOTE_CHARS:
        return False
    return needle in _normalize(source_text)



def best_matching_excerpt(text: str, requirement: str, max_chars: int) -> str:
    """Return the span of ``text`` that best matches ``requirement``.

    A head slice shows whoever is alphabetically first in a combined roster and
    hides everyone else, so the adjudicator never sees the person who proves
    the requirement. Windowing on the requirement's own wording is not enough
    either — the RFP's phrasing rarely appears in zo's documents — so windows
    are scored by how many of the requirement's terms they contain.
    """
    if len(text) <= max_chars:
        return text
    terms = set(_tokens(requirement))
    if not terms:
        return text[:max_chars]

    haystack = text.casefold()
    step = max(1, max_chars // 4)
    best_start, best_score = 0, -1
    for start in range(0, max(1, len(text) - max_chars + 1), step):
        window = haystack[start : start + max_chars]
        score = sum(1 for term in terms if term in window)
        if score > best_score:
            best_score, best_start = score, start
    return text[best_start : best_start + max_chars]


def build_adjudication_payload(
    requirements: list[Any],
    hits_by_requirement: dict[str, list[dict[str, Any]]],
    all_hits: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, dict[str, str]]]:
    """Render requirements + candidate excerpts, and the text to verify against.

    Each requirement sees the documents its own queries returned FIRST, then
    the rest of this run's retrieved corpus. Restricting a requirement to only
    its own hits made evidence invisible across requirements: a bio retrieved
    under "web developer role" could not be cited for "CMS implementation", so
    the model fell back to citing the org-chart document for everything and
    every claim was correctly — but uselessly — rejected.

    Returns (prompt_body, {requirement: {display_name: text}}).
    """
    shared = build_source_index(all_hits or [])
    blocks: list[str] = []
    sources: dict[str, dict[str, str]] = {}

    for requirement in requirements:
        name = getattr(requirement, "requirement", "") or ""
        if not name:
            continue

        own = build_source_index(hits_by_requirement.get(name, []))
        candidates: dict[str, tuple[str, str]] = dict(own)
        for key, value in shared.items():
            if len(candidates) >= _MAX_DOCS_PER_REQUIREMENT:
                break
            candidates.setdefault(key, value)

        per_requirement: dict[str, str] = {}
        lines = [f"### REQUIREMENT: {name}"]
        if not candidates:
            lines.append("(no KB documents retrieved)")
        for _key, (display, text) in list(candidates.items())[
            :_MAX_DOCS_PER_REQUIREMENT
        ]:
            # Window on the part of the document that matches the requirement.
            # Taking the first N characters loses evidence buried later in a
            # long file — a master bio roster puts most people past any fixed
            # head slice.
            excerpt = best_matching_excerpt(text, name, _MAX_DOC_CHARS)
            # Verify quotes against the SAME text the model was shown.
            per_requirement[display] = excerpt
            lines.append(f"--- DOCUMENT: {display}\n{excerpt}")
        sources[name] = per_requirement
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks), sources


def rows_from_assessments(
    requirements: list[Any],
    assessments: list[dict[str, Any]],
    sources: dict[str, dict[str, str]],
) -> tuple[list[GoNoGoCapabilityRow], list[str]]:
    """Turn adjudications into rows, dropping any whose quote is not grounded."""
    by_requirement = {
        str(item.get("requirement") or "").strip().casefold(): item
        for item in assessments
        if isinstance(item, dict)
    }

    rows: list[GoNoGoCapabilityRow] = []
    rejected: list[str] = []

    for requirement in requirements:
        name = getattr(requirement, "requirement", "") or ""
        if not name:
            continue
        is_core = bool(getattr(requirement, "is_core", False))
        item = by_requirement.get(name.casefold())
        available = sources.get(name, {})

        status = str((item or {}).get("status") or "gap").strip().casefold()
        kb_source = str((item or {}).get("kbSource") or "").strip()
        quote = str((item or {}).get("quote") or "").strip()
        reason = str((item or {}).get("reason") or "").strip()
        evidence_state = str((item or {}).get("evidenceState") or "").strip().casefold()
        if evidence_state not in {"absent", "contradicted", "adjacent"}:
            evidence_state = "" if status in {"verified", "partial"} else "absent"

        if status not in {"verified", "partial"}:
            rows.append(
                GoNoGoCapabilityRow(
                    requirement=name,
                    status="gap",
                    isCore=is_core,
                    evidenceState=evidence_state,
                    downgradeReason=reason
                    or (
                        "no KB document evidences this requirement"
                        if available
                        else "no KB results returned for this requirement"
                    ),
                )
            )
            continue

        source_text = available.get(kb_source)
        if source_text is None:
            for display, text in available.items():
                if _normalize(display) == _normalize(kb_source):
                    source_text = text
                    break

        if source_text is None:
            failure = f"cited source '{kb_source}' was not retrieved for this requirement"
        elif not source_can_evidence_capability(kb_source):
            failure = (
                f"'{kb_source}' is a pricing/rate document — it cannot evidence "
                "delivery capability"
            )
        elif not quote_is_grounded(quote, source_text):
            failure = f"quoted evidence does not appear in '{kb_source}'"
        else:
            personnel_fail = personnel_claim_failure(
                requirement=name,
                quote=quote,
                source_text=source_text,
            )
            if personnel_fail:
                failure = personnel_fail
            else:
                rows.append(
                    GoNoGoCapabilityRow(
                        requirement=name,
                        status=status,
                        kbSource=kb_source,
                        evidence=quote[:400],
                        isCore=is_core,
                    )
                )
                continue

        rejected.append(f"{name}: {failure}")
        rows.append(
            GoNoGoCapabilityRow(
                requirement=name,
                status="gap",
                isCore=is_core,
                downgradeReason=failure,
            )
        )

    if rejected:
        logger.info(
            "go_no_go adjudication rejected %d ungrounded claim(s): %s",
            len(rejected),
            "; ".join(rejected[:8]),
        )
    return rows, rejected
