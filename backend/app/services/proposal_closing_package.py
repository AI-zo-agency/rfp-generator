"""Detect RFP-required closing package items — generic across RFPs, never client-hardcoded.

Government / institutional RFPs usually end with some mix of:
references, signed cert forms, authorized signature, pricing form,
exemplar-agreement acknowledgment, insurance/attachment checklist.

Only emit components whose patterns appear in THIS RFP's text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.services.proposal_rfp_excerpt import (
    extract_reference_requirement_summary,
    rfp_forbids_quotation_form_changes,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClosingComponent:
    """One closing package item demanded by the RFP (if matched)."""

    id: str
    title: str
    section_id: str
    kind: str  # narrative | form | attachment | signature
    match_hint: str
    draft_instructions: str


# Patterns are sector-agnostic. Titles use the RFP's language when possible via match_hint.
_CLOSING_CATALOG: list[tuple[str, str, str, str, tuple[str, ...], str]] = [
    (
        "references",
        "References",
        "rfp-closing-references",
        "form",
        (
            r"\breferences?\b",
            r"\bclient\s+references?\b",
            r"\bthree\s+customers?\b",
            r"\blike\s+institution",
            r"\bprior\s+(?:clients?|customers?|work)\b.*\bcontact",
            r"\breference\s+(?:form|sheet|list)\b",
        ),
        (
            "Provide the references package THIS RFP asks for (count, institution type, "
            "contact fields). Use verified KB clients only — never invent phones/emails. "
            "Include a reference ONLY when name, title, org, phone, AND email are all in KB. "
            "If a contact field is missing, OMIT that reference entirely — do not write "
            "[VERIFY: phone/email] shells. If KB has fewer complete contacts than required, "
            "list what you have and one [MANUAL FILL: Sonja — remaining from ClientList]."
            "If a required institution type (e.g. two-year public) is missing from KB, "
            "state the gap plainly with [MANUAL FILL] rather than substituting a weaker analog."
        ),
    ),
    (
        "addenda_acknowledgement",
        "Acknowledgement of Addenda",
        "rfp-closing-addenda",
        "form",
        (
            r"\backnowledg(?:e|ement|ment)s?\s+of\s+addenda\b",
            r"\baddenda\s+acknowledg(?:e|ement|ment)\b",
            r"\backnowledg(?:e|e)\s+all\s+addenda\b",
            r"\breceipt\s+of\s+addenda\b",
            r"\bforms?\s+.*must be returned.*addenda",
            r"addenda.*must be returned",
            r"\bmust\s+(?:be\s+)?(?:returned|submitted|included).{0,80}\baddenda\b",
            r"\baddendum\b.{0,60}\b(?:acknowledge|sign|return)",
            r"\bproposer\s+must\s+acknowledge\b",
            r"\bissue any addenda\b",
            r"\baddenda\b.{0,100}\b(?:proposal|offer|submission)\b",
        ),
        (
            "Draft the Acknowledgement of Addenda exactly as this RFP requires — "
            "this is often a pass/fail submission item. "
            "If no addenda were issued before submission, state that clearly "
            "(e.g. 'No addenda received as of [draft date]'). "
            "If addenda are listed in the RFP packet, acknowledge each by number/title/date. "
            "[MANUAL FILL: authorized signature if the form requires it]."
        ),
    ),
    (
        "certification_forms",
        "Required Certifications & Compliance Forms",
        "rfp-closing-certifications",
        "form",
        (
            r"\bnon[- ]?collusion\b",
            r"\bdebarment\b",
            r"\bsuspension\b",
            r"\biran\b",
            r"\brussia\b|\bbelarus\b",
            r"\bstatement\s+of\s+ownership\b",
            r"\bownership\s+disclosure\b",
            r"\baffirmative\s+action\b|\bAA[- ]?302\b",
            r"\bassurance\s+of\s+compliance\b",
            r"\bvendor\s+(?:certification|questionnaire)\b",
            r"\bproposer\s+certification\b",
        ),
        (
            "List every certification / disclosure / affidavit THIS RFP names "
            "(Affirmative Action Questionnaire, Assurance of Compliance, Vendor Questionnaire, "
            "Ownership Disclosure, Non-Collusion Affidavit, Iran/Russia/Belarus disclosures, etc.). "
            "Mark each Ready / [MANUAL FILL: attach signed PDF on buyer template]. "
            "Note notarization when the RFP requires it. Do not invent signatures."
        ),
    ),
    (
        "authorized_signature",
        "Authorized Signature",
        "rfp-closing-signature",
        "signature",
        (
            r"\bauthorized\s+(?:representative|signatory|signature)\b",
            r"\bsignature\s+(?:block|page)\b",
            r"\bcorporate\s+seal\b",
            r"\blegally\s+bind(?:ing|s)?\b",
            r"\bexecuted\s+by\b.*\bofficer\b",
        ),
        (
            "Provide an authorized signature block (printed name, title, date). "
            "Use Agency Director / CEO from verified roster when the RFP needs a binding signatory. "
            "Leave signature line as [MANUAL FILL: wet/digital signature]."
        ),
    ),
    (
        "pricing_form",
        "Pricing / Cost Proposal Form",
        "rfp-closing-pricing-form",
        "form",
        (
            r"\bpricing\s+proposal\s+form\b",
            r"\bcost\s+proposal\s+form\b",
            r"\bschedule\s+of\s+fees\b",
            r"\bmust\s+be\s+completed\s+and\s+returned\b.*\bpric",
            r"\bhourly\b.*\bmonthly\b.*\bannual\b",
            r"\bquotation\s*/?\s*pricing\b",
        ),
        (
            "Fill the RFP's OWN pricing/cost form fields (not a substitute narrative budget). "
            "If the form asks for a single hourly / monthly / annual blended rate, provide those "
            "three numbers explicitly. Keep any detailed budget as supporting rationale only."
        ),
    ),
    (
        "exemplar_agreement",
        "Contract / Agreement Acknowledgment",
        "rfp-closing-agreement",
        "form",
        (
            r"\bexemplar\s+agreement\b",
            r"\bsample\s+(?:agreement|contract)\b",
            r"\bno\s+exceptions\b",
            r"\bexceptions\s+to\s+(?:the\s+)?(?:agreement|contract|terms)\b",
            r"\bstandard\s+(?:consulting\s+)?(?:services\s+)?agreement\b",
            r"\bterms\s+and\s+conditions\b.*\baccept",
        ),
        (
            "Acknowledge the RFP's exemplar/sample agreement. State acceptance or list "
            "exceptions clearly. If the RFP wants a marked page returned, note [MANUAL FILL]. "
            "Section 1.5 Insurance Information already states zö's coverage; do NOT restate "
            "limits, carriers or coverage types here — even if the agreement text itself "
            "discusses insurance provisions, acknowledge/except that clause by reference only."
        ),
    ),
    (
        "insurance_attachments",
        "Required Submission Attachments — Document Checklist",
        "rfp-closing-attachments",
        "attachment",
        (
            r"\bcertificate(?:s)?\s+of\s+insurance\b|\bCOI\b",
            r"\badditional\s+insured\b",
            r"\bW[- ]?9\b",
            r"\bbusiness\s+registration\b",
            r"\bbond(?:ing|s)?\b",
            r"\battach(?:ment|ed)\b.*\binsurance",
            r"\brequired\s+attachments?\b",
            r"\bmust\s+(?:be\s+)?(?:attached|included|submitted).{0,80}\b(?:exhibit|appendix|form|schedule)\b",
            r"\bexhibit\s+[A-Z0-9]+\b",
            r"\bappendix\s+[A-Z0-9]+\b",
            r"\battach(?:ment)?\s+\d+\b",
            r"\bsubmission\s+checklist\b",
            r"\bdocuments?\s+to\s+(?:be\s+)?(?:submitted|included|attached)\b",
            r"\bcomplete\s+and\s+return\b",
        ),
        (
            "A CHECKLIST OF DOCUMENTS TO RETURN — not a narrative. Section 1.5 "
            "Insurance Information already states zö's coverage; do NOT restate "
            "limits, carriers or coverage types here. Cross-reference it in one "
            "line if the RFP asks, then list ONLY the items to be submitted "
            "(certificate of insurance, additional-insured endorsement, W-9, "
            "named exhibits/appendices, signed forms) with their status. "
            "Use RFP-stated minimums when present. "
            "Mark physical file attachments as [MANUAL FILL: attach PDF]."
        ),
    ),
    (
        "vendor_certification_cvc",
        "Contractor Vendor Certification (CVC / Exhibit H)",
        "rfp-closing-cvc",
        "form",
        (
            r"\bcontractor vendor certification\b",
            r"\bCVC\b",
            r"\bexhibit\s+h\b",
            r"vendor\s+certification\s+form",
        ),
        (
            "Acknowledge Contractor Vendor Certification (CVC) / Exhibit H if THIS RFP requires it. "
            "Checklist: form completed on buyer template, signed, returned with proposal. "
            "[MANUAL FILL: attach signed Exhibit H / CVC]. Do not invent certification numbers."
        ),
    ),
    (
        "offeror_commitment",
        "Offeror Commitment & Closing Statement",
        "rfp-closing-commitment",
        "narrative",
        (
            r"\bclosing\s+statement\b",
            r"\bofferor.?s?\s+statement\b",
            r"\bstatement\s+of\s+(?:interest|commitment)\b",
            r"\bwhy\s+(?:you|we|the\s+offeror)\s+should\s+(?:be\s+)?(?:award|select|chosen)",
            r"\bcommitment\s+to\s+(?:perform|deliver|the\s+work)\b",
            r"\bsummary\s+of\s+(?:the\s+)?(?:offer|proposal)\b",
            r"\bconclud(?:e|ing)\s+(?:remarks?|statement)\b",
            r"\bwe\s+(?:look\s+forward|welcome\s+the\s+opportunity)\b",
            r"\bauthorization\s+to\s+(?:submit|bind)\b",
            r"\bproposal\s+validity\b",
            r"\boffer\s+remains\s+valid\b",
        ),
        (
            "Write a concise, confident CLOSING for THIS proposal (compulsory end section). "
            "Restate fit to the buyer's stated goals, confirm capacity and timeline, "
            "reaffirm validity period if the RFP states one, and invite next steps. "
            "Use only verified zö strengths (team, method, relevant work). "
            "No invented awards, clients, or metrics. "
            "Do NOT repeat full case studies or Section 1 identity. "
            "End ready for authorized signature if required."
        ),
    ),
]


def _build_closing_component(
    *,
    comp_id: str,
    title: str,
    section_id: str,
    kind: str,
    match_hint: str,
    base_instructions: str,
    rfp_text: str,
) -> ClosingComponent:
    draft_instructions = base_instructions
    if comp_id == "references":
        spec = extract_reference_requirement_summary(rfp_text)
        if spec:
            draft_instructions = (
                "The RFP specifies reference requirements — state them accurately:\n"
                f"{spec}\n\n"
                f"{base_instructions}\n"
                "NEVER write that the RFP does not specify reference count, institution type, "
                "or contact fields when the RFP text above does. If zö lacks a qualifying "
                "reference (e.g. two-year public college), say so plainly and use "
                "[MANUAL FILL: leadership decision before submission]."
            )
    elif comp_id == "pricing_form" and rfp_forbids_quotation_form_changes(rfp_text):
        draft_instructions = (
            f"{base_instructions}\n"
            "CRITICAL: This RFP disqualifies bids that alter the official Quotation/Pricing "
            "Proposal Form. Do NOT invent Section A/B/C/D structure or add commission/scope "
            "clauses on the form. List only the buyer's form field labels with responses; "
            "put narrative budget rationale in a separate subsection."
        )
    return ClosingComponent(
        id=comp_id,
        title=title,
        section_id=section_id,
        kind=kind,
        match_hint=match_hint,
        draft_instructions=draft_instructions,
    )


# A topic being *mentioned* is not a requirement to write a section about it.
# "The County may issue addenda prior to the proposal due date" is procedural
# prose; it does not ask the vendor for an Acknowledgement of Addenda section.
# Requiring an obligation phrase near the topic match is what separates the two.
_OBLIGATION_VERB = (
    r"(?:return(?:ed)?|submit(?:ted)?|includ(?:e|ed)|complet(?:e|ed)|"
    r"sign(?:ed)?|acknowledg(?:e|ed)|provid(?:e|ed)|attach(?:ed)?|"
    r"furnish(?:ed)?|enclos(?:e|ed))"
)

_SUBMISSION_OBLIGATION_RE = re.compile(
    rf"""(?ix)
    (?:
        # "must be returned", "shall acknowledge", "will be submitted"
        \b (?:must|shall|will|is|are) \s+ (?:be \s+)? (?:\w+ \s+){{0,2}}?
          {_OBLIGATION_VERB} \b
        # "is required to submit", "are required"
      | \b (?:is|are) \s+ required \b
        # "Required submission documents:", "required forms", "required exhibit"
      | \b required \s+ (?:\w+ \s+){{0,2}} (?:form|document|attachment|
          submittal|exhibit|item|material)s? \b
        # section headers listing what to send
      | \b submission \s+ (?:document|requirement|material|item)s? \b
        # bare imperative: "Submit three references", "Return the signed form"
      | \b (?:submit|return|enclose|attach|furnish) \b
        # "proposal shall contain", "quote must include"
      | \b (?:proposal|quote|submittal|response|bid) \s+ (?:shall|must|should)
          \s+ (?:contain|includ(?:e)|consist) \b
      | \b failure \s+ to \s+ (?:return|submit|include|provide|acknowledge) \b
        # "...with your proposal", "as part of the submittal"
      | \b (?:with|as \s+ part \s+ of) \s+ (?:the \s+|your \s+)?
          (?:proposal|quote|submittal|response|bid) \b
    )
    """
)

# Characters either side of a topic match to scan for the obligation phrase.
# Wide enough to span a sentence or short clause, narrow enough that an
# unrelated obligation elsewhere in the document does not bleed in.
_OBLIGATION_WINDOW = 320


def rfp_requires_topic(rfp_text: str, topic_terms: list[str]) -> bool:
    """True when the RFP asks the vendor to SUBMIT something about ``topic_terms``.

    Shared with outline filtering: a topic being mentioned is not a request for
    a section about it. "PERA retiree notification" and "sex offender
    registration" appear in the solicitation as standing obligations, not as
    proposal contents — keeping a section for each inflated the manuscript past
    its page limit with content the buyer never asked for.
    """
    body = rfp_text or ""
    if not body or not topic_terms:
        return False
    for term in topic_terms:
        term = (term or "").strip()
        if len(term) < 4:
            continue
        for match in re.finditer(re.escape(term), body, re.IGNORECASE):
            # Sentence-scoped, not a fixed character window. A procedural
            # clause often sits directly beside a real submission requirement
            # ("...notify the County after award. SECTION IV. Each quote shall
            # contain..."), and a wide window reads the neighbour's obligation
            # as this topic's.
            # Boundaries are sentence stops and PARAGRAPH breaks only. Text
            # extracted from a PDF is hard-wrapped, so treating every newline
            # as a boundary splits "Each quote shall contain a company
            # overview, past\nperformance examples, ... methodology" and hides
            # the verb from half its own sentence.
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


def _is_submission_obligation(text: str, match: re.Match[str]) -> bool:
    """True when the matched topic sits near language obliging the vendor to submit it."""
    start = max(0, match.start() - _OBLIGATION_WINDOW)
    end = min(len(text), match.end() + _OBLIGATION_WINDOW)
    return bool(_SUBMISSION_OBLIGATION_RE.search(text[start:end]))


def detect_closing_components(
    rfp_text: str,
    *,
    always_include_commitment: bool = False,
) -> list[ClosingComponent]:
    """Return closing components THIS RFP obliges the vendor to submit.

    A component is emitted only when its topic pattern matches *and* submission
    obligation language sits nearby. Matching on topic alone added sections for
    procedural clauses the RFP never asked vendors to write, inflating the
    manuscript past its page limit.

    ``always_include_commitment`` defaults to False: an unconditional closing
    statement is not an RFP requirement, and under a page cap it displaces
    content that is.
    """
    text = (rfp_text or "").strip()
    if not text:
        # No text means nothing is evidenced as required — emit nothing unless
        # the caller explicitly opts into the compulsory close.
        if always_include_commitment:
            for row in _CLOSING_CATALOG:
                if row[0] == "offeror_commitment":
                    return [
                        _build_closing_component(
                            comp_id=row[0],
                            title=row[1],
                            section_id=row[2],
                            kind=row[3],
                            match_hint="(compulsory proposal close)",
                            base_instructions=row[5],
                            rfp_text="",
                        )
                    ]
        return []

    found: list[ClosingComponent] = []
    found_ids: set[str] = set()
    skipped: list[str] = []
    for comp_id, title, section_id, kind, patterns, base_instructions in _CLOSING_CATALOG:
        matched = None
        mention_only = None
        for pat in patterns:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if not m:
                continue
            if _is_submission_obligation(text, m):
                matched = m.group(0)
                break
            mention_only = mention_only or m.group(0)
        if not matched:
            if mention_only:
                skipped.append(f"{comp_id} (mention only: {mention_only[:60]!r})")
            continue
        found.append(
            _build_closing_component(
                comp_id=comp_id,
                title=title,
                section_id=section_id,
                kind=kind,
                match_hint=matched,
                base_instructions=base_instructions,
                rfp_text=text,
            )
        )
        found_ids.add(comp_id)

    if always_include_commitment and "offeror_commitment" not in found_ids:
        for row in _CLOSING_CATALOG:
            if row[0] == "offeror_commitment":
                found.append(
                    _build_closing_component(
                        comp_id=row[0],
                        title=row[1],
                        section_id=row[2],
                        kind=row[3],
                        match_hint="(compulsory proposal close)",
                        base_instructions=row[5],
                        rfp_text=text,
                    )
                )
                break

    logger.info(
        "Closing package for this RFP: %s",
        ", ".join(c.id for c in found) or "(none matched)",
    )
    if skipped:
        # Visible so a genuinely-required item filtered as a mention can be spotted.
        logger.info(
            "Closing package skipped (topic mentioned, no submission obligation): %s",
            ", ".join(skipped),
        )
    return found


def draft_already_covers_component(
    *,
    draft_section_ids: set[str],
    draft_titles: list[str],
    component: ClosingComponent,
) -> bool:
    if component.section_id in draft_section_ids:
        return True
    needles = {
        "references": ("reference",),
        "addenda_acknowledgement": (
            "acknowledgement of addenda",
            "acknowledgment of addenda",
            "addenda acknowledgment",
        ),
        "certification_forms": (
            "non-collusion",
            "certification",
            "disclosure",
            "compliance form",
            "ownership",
        ),
        "authorized_signature": ("authorized signature", "signature block", "signatory"),
        "pricing_form": (
            "pricing proposal form",
            "cost proposal form",
            "schedule of fees",
            "pricing form",
            "request for qualifications pricing",
            "rfq pricing",
            "quotation form",
        ),
        "exemplar_agreement": (
            "exemplar",
            "no exceptions",
            "agreement acknowledgment",
            "sample agreement",
        ),
        "insurance_attachments": (
            "certificate of insurance",
            "required attachment",
            "coi",
            "w-9",
        ),
        "vendor_certification_cvc": (
            "contractor vendor certification",
            "exhibit h",
            "cvc",
            "vendor certification",
        ),
        "offeror_commitment": (
            "closing statement",
            "offeror commitment",
            "commitment & closing",
            "why award",
            "concluding",
        ),
    }.get(component.id, (component.title.casefold(),))
    blob = " | ".join(t.casefold() for t in draft_titles)
    return any(n in blob for n in needles)
