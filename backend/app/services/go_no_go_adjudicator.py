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
from app.services.evidence_trust.personnel_grounding import (
    personnel_claim_failure,
    roster_names_in_text,
)

logger = logging.getLogger(__name__)

ADJUDICATOR_PROMPT = """You decide whether zö agency's knowledge base evidences each RFP requirement.

For every requirement you are given the KB documents retrieved for it. Decide:
  "verified" - a document clearly evidences the capability
  "partial"  - a document evidences a related or narrower form of it
  "gap"      - no document evidences it

Judge by MEANING, not wording. A named platform skill in a bio (e.g. WordPress,
Drupal, Salesforce, ArcGIS) IS evidence for that platform / CMS / tool
requirement. "Improve clarity and user flow" IS user-experience evidence.
A page-structure redesign IS information architecture. Do not require the
document to repeat the RFP's phrasing.

EVIDENCE PRINCIPLES (apply to EVERY RFP — never invent absent proof, never
ignore retrieved proof):
- A specialist BIO that states a tool/platform/discipline skill IS "verified"
  for that skill. Do NOT mark gap solely because there is no separately titled
  case study for the same tool.
- A case study that describes the same kind of delivery (redesign, migration,
  campaign, integration) IS "verified" or "partial" for that delivery ask even
  when the client sector differs — use "partial"/"adjacent" when sector is
  material to the ask (e.g. government-only), not when the ask is the craft.
- Thin bench / one specialist plus supporting creatives is staffing DEPTH, not
  proof the skill is missing. Mark depth concerns "partial" or
  evidenceState="adjacent"; never treat them as "absent" for the skill itself.
- Keep orthogonal gaps separate: missing hosting/SLA/office/registration must
  NOT be used to claim missing platform or craft capability, and vice versa.
- WBENC / WOSB / women-owned ownership evidences CERTIFICATION / MWESB asks
  only. It does NOT evidence equal-opportunity policy, ORS/statutory
  contracting, bonding/insurance, audit/evaluation, or cooperative purchasing.
  Leave those as gap or affirm-at-submission partial — never fill with a
  non-sequitur badge.

STRATEGIC COMMUNICATIONS / MEDIA / PUBLIC-HEALTH CAMPAIGNS (common RFP shape):
- Won proposals (06_WON_*) and case studies (03_CS_*) that describe campaign
  strategy, television/radio/digital placement, media planning/buying, bilingual
  outreach, coalition messaging, or public-health education ARE verified or
  partial proof for strategic communications and media-buying requirements —
  even when the RFP also asks for polling, toolkit development, or formal
  evaluation sub-deliverables those documents do not repeat verbatim.
- Mark the core delivery/campaign ask verified/partial from that proof; mark
  evaluation-only sub-asks (polling, pre/post surveys, toolkit handoff) partial
  when campaign delivery is strong but that specific sub-ask is not stated.
- A bio naming media planning, advertising, or broadcast IS verified for media
  buyer / media specialist role requirements when the quote is verbatim.

But do NOT stretch across a real difference: content development is not content
migration; print/brand design is not web development; branding for a city is not
building that city a website; a pricing guide is not proof of delivery
capability. Sector matters when the RFP requires it — private-sector work does
not fully evidence "government website experience", though it does evidence
generic "website redesign".

COMPLIANCE / POLICY / BUYER (these are not staffing rows and not crafts):
- An ownership-certification ask that lists several classes with "or" is met
  when the KB evidences ANY matching class. WBENC / WOSB / women-owned IS
  "verified" for a women-owned / WBE / MWESB-style ask. Do not also require
  minority, SDVOSB, or emerging-small-business certificates the KB does not have.
- Agency certifications you may cite: WBENC and WOSB ONLY. Never invent or
  list 1% for the Planet, B Corp / B-Corporate, LinkedIn Gold-Certified, MBE,
  DBE, or any other badge not present verbatim in the cited document.
- Never stitch metrics across case studies. Festival / campaign proof (e.g.
  Rock the Locks ticket sales / PR reach) must not gain higher-ed enrollment
  language ("accelerated early admissions") from another document.
- Statutory compliance, university policies, EEO / non-discrimination, and
  "ability to work or contract with [the buyer]" are bid affirmations / operating
  commitments. They are not named people. Never read a statute title or the
  buyer's name as a staff member. If 01_companyfacts does not contradict, mark
  "partial" with evidenceState="adjacent" (affirm at submission) — not a
  delivery "gap" — unless the KB shows a specific disqualifier.
- Role / liaison / designated contact asks: evidence the FUNCTION from current
  bios and case studies. Never invent staff. If no current person is named,
  mark partial and FLAG SONJA to assign — that is staffing assignment, not a
  missing delivery craft.
- A quote must evidence THIS requirement only. A general preferred-vendor,
  contract-status, or years-in-business sentence cannot prove an unrelated
  craft, budget ceiling, audit obligation, launch timeline, or platform skill.
- Insurance / COI / policy-limit text evidences ONLY insurance or COI asks.
  Never cite insurance coverage, policy dollars, or expiration dates for
  audit, evaluation, financial reporting, or programmatic compliance asks —
  leave those a gap (or affirm-at-submission partial) instead of filling with
  a non-sequitur.
- Policy dollar amounts and expiration dates are zö facts ONLY when copied
  verbatim from 01_companyfacts / insurance / COI documents. Never treat an
  RFP's vendor insurance *requirements* ($1M/$2M the buyer demands) as zö's
  own policies. Never invent numbers or dates. An expired coverage period
  cannot prove current compliance.

COMMUNICATIONS CRAFT (municipal / public-sector case studies count):
- Media relations, press outreach/releases, stakeholder engagement, relationship
  management, social media strategy, and communications project management ARE
  evidenced by 03_CS / 06_WON work of that kind (cities, counties, labs, libraries).
  A named social-media partner or subcontractor in the KB IS evidence.
- For "media relations" / "press outreach" asks: prefer case studies that
  describe press, media relations, or earned media. Brand strategy / visual
  identity alone is PARTIAL at best — do not mark Strong unless press/media
  outreach is in the quote.
- Crisis / emergency / issues-response communications is verified only when a
  document describes that work; otherwise leave it a gap (do not stretch brand
  or media relations into crisis).

NEVER invent staff. Copy names ONLY if they appear in the KB excerpts for
this requirement (see STAFF NAMES PRESENT). If that list is empty, do not
name anyone — FLAG SONJA. Never abbreviate a person (no "Rad S.").
Retired staff (Ron Comer) must not be assigned as current lead even if an
old bio still contains the name.

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

_MAX_DOC_CHARS = 4_500
_MAX_RECOVER_DOC_CHARS = 12_000
_MAX_DOCS_PER_REQUIREMENT = 16
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


def _requirement_is_pricing_ask(requirement: str) -> bool:
    name = (requirement or "").casefold()
    return any(
        token in name
        for token in (
            "pricing",
            "rate card",
            "hourly rate",
            "fee schedule",
            "rate sheet",
        )
    )


def _prefer_capability_candidates(
    candidates: dict[str, tuple[str, str]],
    *,
    requirement: str,
) -> dict[str, tuple[str, str]]:
    """Drop rate sheets and rank docs by how well they match THIS requirement.

    Showing 00_Guide_Pricing alongside case studies taught the model to cite
    the guide. Prefer 03_CS / 04_Bio / 06_WON / companyfacts, then order by
    requirement-term overlap so press/media docs beat brand-strategy-only
    docs for a media-relations ask (and the same for every other craft).
    """
    if not candidates:
        return candidates
    if not _requirement_is_pricing_ask(requirement):
        capable = {
            key: value
            for key, value in candidates.items()
            if source_can_evidence_capability(value[0])
        }
        candidates = capable or candidates

    req_terms = set(_tokens(requirement))
    if not req_terms or len(candidates) < 2:
        return candidates

    ranked = sorted(
        candidates.items(),
        key=lambda item: (
            sum(1 for t in req_terms if t in (item[1][1] or "").casefold()),
            # Prefer case studies / bios / won proposals over templates.
            (
                1
                if re.search(
                    r"(?i)03_cs|04_bio|06_won|01_companyfacts|clientlist",
                    item[1][0] or "",
                )
                else 0
            ),
        ),
        reverse=True,
    )
    return {key: value for key, value in ranked}


def quote_is_grounded(quote: str, source_text: str) -> bool:
    """True when ``quote`` really appears in ``source_text``."""
    needle = _normalize(quote)
    if len(needle) < _MIN_QUOTE_CHARS:
        return False
    return needle in _normalize(source_text)


_QUOTE_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "a",
        "an",
        "of",
        "to",
        "for",
        "in",
        "on",
        "with",
        "zö",
        "zo",
        "agency",
        "that",
        "this",
        "from",
    }
)
_QUOTE_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _distinctive_quote_anchors(quote: str) -> set[str]:
    """Project / client name tokens that must stay with the salvaged sentence.

    Stops a Rock the Locks paraphrase from salvaging a Benedictine 'early
    admissions' sentence out of a combined case-study dump.
    """
    anchors: set[str] = set()
    for match in re.finditer(
        r"\b(?:Rock\s+the\s+Locks|Benedictine|Umatilla|Maricopa|"
        r"Deschutes|Carbondale|University\s+of\s+\w+)\b",
        quote or "",
        re.I,
    ):
        for tok in _QUOTE_TOKEN_RE.findall(_normalize(match.group(0))):
            if tok not in _QUOTE_STOPWORDS and len(tok) > 2:
                anchors.add(tok)
    return anchors


def salvage_grounded_quote(quote: str, source_text: str) -> str | None:
    """Return a verbatim source sentence when the model paraphrased a real span.

    Combined case-study dumps often contain the proof; the model restates it
    and the mechanical quote check would otherwise freeze a true gap on every
    RFP. Never invent text — only a sentence already in the source that shares
    enough of the model's tokens to be the same claim.
    """
    if quote_is_grounded(quote, source_text):
        return (quote or "").strip()
    source = source_text or ""
    quote_tokens = {
        tok
        for tok in _QUOTE_TOKEN_RE.findall(_normalize(quote))
        if tok not in _QUOTE_STOPWORDS and len(tok) > 2
    }
    if len(quote_tokens) < 3 or len(source) < _MIN_QUOTE_CHARS:
        return None
    anchors = _distinctive_quote_anchors(quote)
    best = ""
    best_score = 0.0
    for part in re.split(r"(?<=[.!?])\s+|\n+", source):
        span = part.strip()
        if len(_normalize(span)) < _MIN_QUOTE_CHARS:
            continue
        if not quote_is_grounded(span, source):
            continue
        tokens = set(_QUOTE_TOKEN_RE.findall(_normalize(span)))
        if anchors and not (anchors & tokens):
            # Quote named a specific project; this sentence is about another one.
            continue
        overlap = len(quote_tokens & tokens)
        score = overlap / len(quote_tokens)
        if score > best_score:
            best_score = score
            best = span
    if best and best_score >= 0.45:
        return best[:400]
    return None


def _is_operational_commitment(requirement: str, category: str = "") -> bool:
    """Bid process / contract terms — not a missing delivery craft.

    Attendance, ownership transfer, and other logistics/compliance asks must
    not become 'Real gap' because the model cited an empty source.
    """
    if (category or "").casefold() in {"logistics", "compliance"}:
        return True
    name = (requirement or "").casefold()
    return any(
        token in name
        for token in (
            "pre-application",
            "pre-bid",
            "pre application",
            "ownership transfer",
            "work made for hire",
            "mandatory meeting",
            "attend",
        )
    )


def _is_staffing_assignment_row(requirement: str, category: str = "") -> bool:
    """Role/liaison designation — a bad name is an assignment flag, not a craft gap.

    Applies to every RFP: planner category ``role``, or wording that asks to
    designate a liaison / director / point of contact.
    """
    if (category or "").casefold() == "role":
        return True
    name = (requirement or "").casefold()
    return any(
        token in name
        for token in (
            "program director",
            "liaison",
            "single point of contact",
            "point of contact",
            "designate",
            "dedicated account",
        )
    )


def _evidence_domain(text: str) -> set[str]:
    """Coarse domains present in requirement or quote text.

    Used only to reject orthogonal fills (insurance for audit, WBENC for EEO).
    Not a capability synonym table — domain membership, not tool matching.
    """
    blob = (text or "").casefold()
    domains: set[str] = set()
    if any(
        tok in blob
        for tok in (
            "insurance",
            "certificate of insurance",
            "coi",
            "acord",
            "general liability",
            "professional liability",
            "workers compensation",
            "workers' compensation",
            "policy limit",
            "per occurrence",
            "aggregate",
            "additional insured",
            "bonding",
            "surety",
            "bond requirement",
        )
    ):
        if any(
            tok in blob
            for tok in (
                "insurance",
                "liability",
                "coverage",
                "coi",
                "acord",
                "policy",
                "insured",
                "bonding",
                "surety",
                "bond",
            )
        ):
            domains.add("insurance_bonding")
    if any(
        tok in blob
        for tok in (
            "audit",
            "evaluation requirement",
            "programmatic evaluation",
            "financial reporting",
            "single audit",
            "performance evaluation",
            "monitoring and evaluation",
        )
    ):
        domains.add("audit_evaluation")
    if any(
        tok in blob
        for tok in (
            "equal opportunity",
            "non-discrimination",
            "nondiscrimination",
            "non discrimination",
            "eeo",
            "affirmative action",
            "civil rights",
        )
    ):
        domains.add("eeo_policy")
    if any(
        tok in blob
        for tok in (
            "oregon revised statutes",
            " ors ",
            "ors)",
            "(ors",
            "public sector contracting",
            "public contracting",
            "state contracting",
            "statutory compliance",
        )
    ) or re.search(r"\bors\b", blob):
        domains.add("statutory_contracting")
    if any(
        tok in blob
        for tok in (
            "cooperative purchasing",
            "piggyback",
            "multi-institution",
            "other oregon public universities",
            "participating agencies",
        )
    ):
        domains.add("cooperative_purchasing")
    if any(
        tok in blob
        for tok in (
            "wbenc",
            "wosb",
            "women-owned",
            "women owned",
            "woman-owned",
            "woman owned",
            "minority",
            "sdvosb",
            "emerging small",
            "mwesb",
            "certification",
            "% ownership",
            "percent ownership",
            "sole owner",
        )
    ):
        domains.add("certification")
    return domains


def quote_evidences_requirement(requirement: str, quote: str) -> bool:
    """False when the quote is an orthogonal non-sequitur for this requirement.

    Principle (every RFP): a quote must address the ask's domain. Examples that
    fail: insurance COI for audit; WBENC badge for EEO or ORS compliance;
    "we serve municipalities" for bonding/insurance; ownership % for cooperative
    purchasing.
    """
    req_domains = _evidence_domain(requirement)
    quote_domains = _evidence_domain(quote)
    if not req_domains:
        # Craft / delivery asks — do not block on domain tags.
        return True
    if not quote_domains:
        # Quote has no compliance-domain signal; if the requirement is a
        # compliance/policy ask, that is not evidence of it.
        compliance_asks = {
            "insurance_bonding",
            "audit_evaluation",
            "eeo_policy",
            "statutory_contracting",
            "cooperative_purchasing",
            "certification",
        }
        if req_domains & compliance_asks:
            return False
        return True

    # Shared domain → ok (women-owned ask + WBENC quote, etc.).
    if req_domains & quote_domains:
        # Certification-only quote cannot satisfy EEO / statute / bonding /
        # audit / cooperative asks even when both are "compliance-ish".
        if quote_domains <= {"certification"} and req_domains & {
            "eeo_policy",
            "statutory_contracting",
            "insurance_bonding",
            "audit_evaluation",
            "cooperative_purchasing",
        }:
            return False
        return True

    # No overlap between requirement domain and quote domain.
    return False


_EXPIRED_COVERAGE_RE = re.compile(
    r"(?i)(?:expir\w*|valid\s+(?:through|until)|coverage\s+period|policy\s+period)"
    r".{0,40}?(?:19\d{2}|20(?:0\d|1\d|2[0-3]))"
    r"|"
    r"(?:19\d{2}|20(?:0\d|1\d|2[0-3]))[-/]\d{1,2}[-/]\d{1,2}"
)


def quote_has_expired_coverage_date(quote: str) -> bool:
    """True when evidence cites a coverage period that ended before 2024.

    Invented or stale COI dates (e.g. 2019-02-13) cannot prove current compliance
    for a 2026+ submission. Applies only when the quote looks like insurance.
    """
    if "insurance_bonding" not in _evidence_domain(quote):
        return False
    return bool(_EXPIRED_COVERAGE_RE.search(quote or ""))


def _ground_quote(
    quote: str,
    kb_source: str,
    available: dict[str, str],
    *,
    full_available: dict[str, str] | None = None,
) -> tuple[bool, str | None]:
    """Check quote against excerpt first, then the full retrieved document."""
    source_text = available.get(kb_source)
    if source_text is None:
        for display, text in available.items():
            if _normalize(display) == _normalize(kb_source):
                source_text = text
                break
    if source_text is not None and quote_is_grounded(quote, source_text):
        return True, source_text

    full_text = None
    if full_available:
        full_text = full_available.get(kb_source)
        if full_text is None:
            for display, text in full_available.items():
                if _normalize(display) == _normalize(kb_source):
                    full_text = text
                    break
    if full_text and quote_is_grounded(quote, full_text):
        return True, full_text
    return False, source_text or full_text



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
) -> tuple[str, dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """Render requirements + candidate excerpts, and the text to verify against.

    Each requirement sees the documents its own queries returned FIRST, then
    the rest of this run's retrieved corpus. Restricting a requirement to only
    its own hits made evidence invisible across requirements: a bio retrieved
    under "web developer role" could not be cited for "CMS implementation", so
    the model fell back to citing the org-chart document for everything and
    every claim was correctly — but uselessly — rejected.

    Returns (prompt_body, excerpt_sources, full_text_sources). Quotes are
    verified against full retrieved text when they fall outside the excerpt
    window shown to the model.
    """
    shared = build_source_index(all_hits or [])
    blocks: list[str] = []
    sources: dict[str, dict[str, str]] = {}
    full_sources: dict[str, dict[str, str]] = {}

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

        candidates = _prefer_capability_candidates(
            candidates, requirement=name
        )

        per_requirement: dict[str, str] = {}
        per_requirement_full: dict[str, str] = {}
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
            # Verify quotes against full retrieved text — not only the excerpt.
            per_requirement[display] = excerpt
            per_requirement_full[display] = text
            lines.append(f"--- DOCUMENT: {display}\n{excerpt}")
        kb_blob = "\n".join(per_requirement_full.values() or per_requirement.values())
        visible = roster_names_in_text(kb_blob)
        if visible:
            lines.append(
                "STAFF NAMES PRESENT IN THESE KB EXCERPTS "
                "(the only names you may write): " + ", ".join(visible)
            )
        else:
            lines.append(
                "STAFF NAMES PRESENT IN THESE KB EXCERPTS: none — "
                "do not invent a person; FLAG SONJA."
            )
        sources[name] = per_requirement
        full_sources[name] = per_requirement_full
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks), sources, full_sources


def rows_from_assessments(
    requirements: list[Any],
    assessments: list[dict[str, Any]],
    sources: dict[str, dict[str, str]],
    *,
    full_sources: dict[str, dict[str, str]] | None = None,
) -> tuple[list[GoNoGoCapabilityRow], list[str], set[str]]:
    """Turn adjudications into rows, dropping any whose quote is not grounded.

    Returns (rows, rejected_messages, recoverable_requirement_names).
    ``recoverable`` are rows eligible for LLM gap re-check — model gaps and
    quote-grounding failures where retrieved docs exist.
    """
    by_requirement = {
        str(item.get("requirement") or "").strip().casefold(): item
        for item in assessments
        if isinstance(item, dict)
    }

    rows: list[GoNoGoCapabilityRow] = []
    rejected: list[str] = []
    recoverable: set[str] = set()
    quote_recoverable: set[str] = set()

    for requirement in requirements:
        name = getattr(requirement, "requirement", "") or ""
        if not name:
            continue
        is_core = bool(getattr(requirement, "is_core", False))
        category = str(getattr(requirement, "category", "") or "service").casefold()
        disqualifying = bool(getattr(requirement, "disqualifying", False))
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
            recoverable.add(name)
            rows.append(
                GoNoGoCapabilityRow(
                    requirement=name,
                    status="gap",
                    isCore=is_core,
                    disqualifying=disqualifying,
                    category=category,
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

        full_available = (full_sources or {}).get(name, {})

        def _source_retrieved() -> bool:
            if not kb_source:
                return False
            for mapping in (available, full_available):
                if kb_source in mapping:
                    return True
                if any(
                    _normalize(display) == _normalize(kb_source)
                    for display in mapping
                ):
                    return True
            return False

        if not _source_retrieved():
            if _is_operational_commitment(name, category):
                recoverable.add(name)
                rows.append(
                    GoNoGoCapabilityRow(
                        requirement=name,
                        status="partial",
                        isCore=is_core,
                        disqualifying=disqualifying,
                        category=category,
                        evidenceState="adjacent",
                        downgradeReason=(
                            "FLAG: confirm at bid — operational commitment, "
                            "not a missing delivery craft"
                        ),
                    )
                )
                continue
            failure = f"cited source '{kb_source}' was not retrieved for this requirement"
        elif not source_can_evidence_capability(kb_source):
            failure = (
                f"'{kb_source}' is a pricing/rate document — it cannot evidence "
                "delivery capability"
            )
        else:
            grounded, grounded_text = _ground_quote(
                quote, kb_source, available, full_available=full_available
            )
            salvage_pool = grounded_text or ""
            if full_available:
                salvage_pool = full_available.get(kb_source) or salvage_pool
                if kb_source not in (full_available or {}):
                    for display, text in full_available.items():
                        if _normalize(display) == _normalize(kb_source):
                            salvage_pool = text or salvage_pool
                            break
            usable_quote = quote
            if not grounded:
                salvaged = salvage_grounded_quote(quote, salvage_pool)
                if salvaged:
                    usable_quote = salvaged
                    grounded = True
                    grounded_text = salvage_pool
            if not grounded:
                failure = f"quoted evidence does not appear in '{kb_source}'"
                quote_recoverable.add(name)
            else:
                personnel_fail = personnel_claim_failure(
                    requirement=name,
                    quote=usable_quote,
                    source_text=grounded_text or "",
                )
                if personnel_fail and _is_staffing_assignment_row(name, category):
                    # Inventing Brittany as Program Director is not proof we
                    # lack a liaison — FLAG assignment, keep the row recoverable.
                    rejected.append(f"{name}: {personnel_fail}")
                    recoverable.add(name)
                    rows.append(
                        GoNoGoCapabilityRow(
                            requirement=name,
                            status="partial",
                            isCore=is_core,
                            disqualifying=disqualifying,
                            category=category,
                            evidenceState="adjacent",
                            downgradeReason=(
                                "FLAG SONJA: assign a current roster person as "
                                "liaison — do not invent staff"
                            ),
                        )
                    )
                    continue
                if personnel_fail:
                    failure = personnel_fail
                elif not quote_evidences_requirement(name, usable_quote):
                    failure = (
                        "quoted evidence does not address this requirement "
                        "(orthogonal fill — e.g. insurance text for an audit ask)"
                    )
                    quote_recoverable.add(name)
                elif quote_has_expired_coverage_date(usable_quote):
                    failure = (
                        "quoted insurance coverage date is expired — "
                        "cannot prove current compliance; FLAG SONJA for COI"
                    )
                    quote_recoverable.add(name)
                else:
                    rows.append(
                        GoNoGoCapabilityRow(
                            requirement=name,
                            status=status,
                            kbSource=kb_source,
                            evidence=usable_quote[:400],
                            isCore=is_core,
                            disqualifying=disqualifying,
                            category=category,
                        )
                    )
                    continue

        rejected.append(f"{name}: {failure}")
        if available or full_available:
            recoverable.add(name)
        rows.append(
            GoNoGoCapabilityRow(
                requirement=name,
                status="gap",
                isCore=is_core,
                disqualifying=disqualifying,
                category=category,
                downgradeReason=failure,
            )
        )

    recoverable |= quote_recoverable
    if rejected:
        logger.info(
            "go_no_go adjudication rejected %d ungrounded claim(s): %s",
            len(rejected),
            "; ".join(rejected[:8]),
        )
    from app.services.go_no_go_evidence_scrub import scrub_capability_rows

    rows = scrub_capability_rows(rows)
    return rows, rejected, recoverable


GAP_RECOVER_PROMPT = """You re-check Go/No-Go requirements that were marked GAP even though
KB documents were retrieved for them.

Judge by MEANING only — never by keyword lists:
- A specialist bio that names a tool/platform (e.g. WordPress) evidences that
  platform and related CMS/web-development asks.
- A delivery case study that describes the same kind of work evidences that
  craft ask (redesign, UX, migration, etc.).
- Campaign / media / strategic-communications won proposals and case studies
  evidence media buying, placement, and health/education outreach requirements
  even when evaluation or toolkit sub-asks are not spelled out in the doc.
- Do NOT require a separately titled case study when a bio already proves the skill.
- Never cite a pricing/rate guide as delivery proof; use 03_CS / 04_Bio / 06_WON /
  01_companyfacts instead.
- Women-owned / WBENC / WOSB meets an OR-list MWESB certification ask.
- Policy, EEO, and "work with [buyer]" rows are affirmations, not named people.
- Role / liaison designation: never invent a person. Quote a current bio for
  the function, or keep partial with FLAG SONJA.
- A quote must evidence THIS requirement. Do not reuse a generic vendor-status
  sentence for budget, audit, launch timing, or an unrelated platform skill.
- Insurance / COI / policy dollars evidence ONLY insurance asks — never audit
  or evaluation compliance. Never invent policy numbers or expired dates.
- Copy quotes EXACTLY from the document text below.
- Name people ONLY if STAFF NAMES PRESENT lists them. Never invent a name.
- Do NOT invent. If nothing in the documents supports the ask, keep status=gap.
- Copy quotes EXACTLY from the document text below — long proposals often place
  the proof outside the first paragraph; search the full excerpt carefully.

For verified or partial you MUST return kbSource (exact document name) and a
VERBATIM quote copied from that document's text. Quotes are checked mechanically.

Return ONLY JSON:
{"assessments":[{"requirement":"...","status":"verified|partial|gap",
  "kbSource":"...","quote":"...","evidenceState":"absent|contradicted|adjacent",
  "reason":"one short sentence"}]}
Only include requirements listed below."""


def build_gap_recover_payload(
    gap_requirements: list[Any],
    sources: dict[str, dict[str, str]],
    *,
    full_sources: dict[str, dict[str, str]] | None = None,
) -> str:
    blocks: list[str] = []
    for requirement in gap_requirements:
        name = getattr(requirement, "requirement", "") or ""
        if not name:
            continue
        docs = full_sources.get(name, {}) if full_sources else sources.get(name, {})
        if not docs:
            docs = sources.get(name, {})
        if not docs:
            continue
        lines = [f"### REQUIREMENT: {name}"]
        usable = {
            display: text
            for display, text in docs.items()
            if source_can_evidence_capability(display) or len(docs) == 1
        }
        if not usable:
            continue
        for display, text in list(usable.items())[:_MAX_DOCS_PER_REQUIREMENT]:
            lines.append(
                f"--- DOCUMENT: {display}\n"
                f"{(text or '')[:_MAX_RECOVER_DOC_CHARS]}"
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def apply_gap_recover_assessments(
    rows: list[GoNoGoCapabilityRow],
    *,
    recoverable: set[str],
    assessments: list[dict[str, Any]],
    sources: dict[str, dict[str, str]],
    requirements: list[Any],
    full_sources: dict[str, dict[str, str]] | None = None,
) -> list[GoNoGoCapabilityRow]:
    """Apply LLM gap re-checks onto recoverable rows; quote failures stay frozen."""
    if not recoverable or not assessments:
        return rows
    req_by_name = {
        (getattr(r, "requirement", "") or ""): r
        for r in requirements
        if getattr(r, "requirement", "")
    }
    subset = [req_by_name[name] for name in recoverable if name in req_by_name]
    if not subset:
        return rows
    recovered_rows, _rejected, _rec = rows_from_assessments(
        subset, assessments, sources, full_sources=full_sources
    )
    by_name = {r.requirement: r for r in recovered_rows}
    out: list[GoNoGoCapabilityRow] = []
    upgraded = 0
    for row in rows:
        if row.requirement not in recoverable:
            out.append(row)
            continue
        nxt = by_name.get(row.requirement, row)
        # Only accept upgrades — never let a second pass invent new gaps over verified.
        if row.status in {"verified", "partial"}:
            out.append(row)
            continue
        if nxt.status in {"verified", "partial"}:
            upgraded += 1
            out.append(nxt)
        else:
            out.append(row)
    if upgraded:
        logger.info(
            "go_no_go LLM gap recover upgraded %d row(s) from grounded evidence",
            upgraded,
        )
    return out
