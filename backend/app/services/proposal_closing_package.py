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
            "exceptions clearly. If the RFP wants a marked page returned, note [MANUAL FILL]."
        ),
    ),
    (
        "insurance_attachments",
        "Insurance Certificates & Required Attachments",
        "rfp-closing-attachments",
        "attachment",
        (
            r"\bcertificate(?:s)?\s+of\s+insurance\b|\bCOI\b",
            r"\badditional\s+insured\b",
            r"\bW[- ]?9\b",
            r"\bbusiness\s+registration\b",
            r"\bbond(?:ing|s)?\b",
            r"\battach(?:ment|ed)\b.*\binsurance",
        ),
        (
            "Checklist of insurance certificates and attachments THIS RFP requires "
            "(limits, additional insured, COI timing). Use RFP-stated minimums when present. "
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
        ),
        (
            "Write a concise, confident closing that helps THIS bid win: restates fit to the "
            "RFP's stated goals, confirms capacity and timeline commitment, and invites next "
            "steps. Use only verified zö strengths (team, method, relevant work). "
            "No invented awards, clients, or metrics. End ready for authorized signature if required."
        ),
    ),
]


def detect_closing_components(rfp_text: str) -> list[ClosingComponent]:
    """Return closing components whose patterns appear in this RFP's text."""
    text = (rfp_text or "").strip()
    if not text:
        return []
    found: list[ClosingComponent] = []
    for comp_id, title, section_id, kind, patterns, base_instructions in _CLOSING_CATALOG:
        matched = None
        for pat in patterns:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                matched = m.group(0)
                break
        if not matched:
            continue
        draft_instructions = base_instructions
        if comp_id == "references":
            spec = extract_reference_requirement_summary(text)
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
        elif comp_id == "pricing_form" and rfp_forbids_quotation_form_changes(text):
            draft_instructions = (
                f"{base_instructions}\n"
                "CRITICAL: This RFP disqualifies bids that alter the official Quotation/Pricing "
                "Proposal Form. Do NOT invent Section A/B/C/D structure or add commission/scope "
                "clauses on the form. List only the buyer's form field labels with responses; "
                "put narrative budget rationale in a separate subsection."
            )
        found.append(
            ClosingComponent(
                id=comp_id,
                title=title,
                section_id=section_id,
                kind=kind,
                match_hint=matched,
                draft_instructions=draft_instructions,
            )
        )
    logger.info(
        "Closing package for this RFP: %s",
        ", ".join(c.id for c in found) or "(none matched)",
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
