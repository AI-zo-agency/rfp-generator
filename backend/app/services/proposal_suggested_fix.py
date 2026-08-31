"""Structured suggested fixes for advisory chat → Apply the fix UX."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.models.proposal import ProposalDraft


@dataclass(frozen=True)
class SuggestedFix:
    section_id: str
    instruction: str
    summary: str
    section_title: str = ""

    def to_api_dict(self) -> dict[str, str]:
        out = {
            "sectionId": self.section_id,
            "instruction": self.instruction,
            "summary": self.summary,
        }
        if self.section_title:
            out["sectionTitle"] = self.section_title
        return out


# Reply looks like an audit that proposed draft changes (even if hasFix was omitted).
# "Cannot confirm" alone is NOT actionable — it means KB is silent, not that contacts
# need scrubbing.
_RECOMMENDATION_REPLY_RE = re.compile(
    r"(?is)\b("
    r"recommendation(?:s)?|"
    r"recommended\s+action|"
    r"apply\s+these\s+fixes|"
    r"factual\s+errors?|"
    r"\*\*incorrect\*\*|"
    r"incorrect\s*:|"
    r"problem\s*:|"
    r"kb\s+coverage\s*:\s*none|"
    r"does\s+not\s+(?:provide|mention)|"
    r"invented\s+these\s+details|"
    r"remove\s+or\s+verify|"
    r"flag\s+for\s+sonja|"
    r"\[pricing\s+flag:|"
    r"\[verify:"
    r")"
)

_CONTACT_AUDIT_RE = re.compile(
    r"(?is)\b("
    r"contact|phone|email|reference|clientlist|"
    r"invented\s+contact|wrong\s+client"
    r")\b"
)

_CONTACTS_VERIFIED_OK_RE = re.compile(
    r"(?is)\b("
    r"contact\s+fields?\s+(?:in\s+the\s+draft\s+)?(?:are\s+)?verified\s+correct|"
    r"contact\s+details\s+are\s+correct|"
    r"no\s+changes?\s+needed\s+there|"
    r"no\s+structural\s+edits?\s+needed"
    r")\b"
)

_VERIFY_TAG_RECOMMEND_RE = re.compile(
    r"(?is)"
    r"(?:recommended\s+action|before\s+submission|board\s+roster).{0,120}"
    r"\[VERIFY:[^\]]+\]"
    r"|\[VERIFY:[^\]]+\].{0,120}"
    r"(?:confirm|board\s+roster|cross-?check|before\s+(?:you\s+)?sign)"
)

_PRICING_CAPACITY_AUDIT_RE = re.compile(
    r"(?is)\b("
    r"\[pricing\s+flag:|pricing\s+flag|"
    r"capacity\s+allocation|hours?\s*/\s*month|monthly\s+capacity|"
    r"hour\s+breakdown|fee\s+table|budget|pricing\s+playbook|"
    r"reverse-engineered|blended\s+hour"
    r")\b"
)

_CLEAN_PASS_RE = re.compile(
    r"(?is)^\s*(?:\*\*)?(?:correct|accurate|looks\s+good|meets\s+the\s+rfp)(?:\*\*)?\b"
)


def parse_advisory_suggested_fix(
    raw: dict[str, Any] | None,
    *,
    fallback_section_id: str,
) -> SuggestedFix | None:
    """Extract a suggested fix from advisory LLM JSON, if present."""
    if not isinstance(raw, dict):
        return None
    has_fix = raw.get("hasFix")
    if has_fix is False or has_fix is None:
        # Also accept truthy string / 1 from loose models
        if has_fix not in (True, "true", "True", 1, "1"):
            return None
    instruction = str(raw.get("applyInstruction") or raw.get("instruction") or "").strip()
    if not instruction:
        return None
    section_id = str(raw.get("sectionId") or "").strip() or fallback_section_id
    if not section_id:
        return None
    summary = str(raw.get("summary") or "").strip() or instruction[:160]
    section_title = str(raw.get("sectionTitle") or "").strip()
    return SuggestedFix(
        section_id=section_id,
        instruction=instruction,
        summary=summary,
        section_title=section_title,
    )


def validate_suggested_fix_section(
    fix: SuggestedFix | None,
    draft: ProposalDraft | None,
) -> SuggestedFix | None:
    """Keep only fixes that target a real sidebar section; remap title if known."""
    if fix is None or draft is None:
        return None
    hit = next((s for s in draft.sections if s.id == fix.section_id), None)
    if hit is None:
        # Try title match when model invented an id
        title = (fix.section_title or "").strip().lower()
        if title:
            hit = next(
                (s for s in draft.sections if (s.title or "").strip().lower() == title),
                None,
            )
        if hit is None:
            return None
        return SuggestedFix(
            section_id=hit.id,
            instruction=fix.instruction,
            summary=fix.summary,
            section_title=hit.title or fix.section_title,
        )
    return SuggestedFix(
        section_id=hit.id,
        instruction=fix.instruction,
        summary=fix.summary,
        section_title=hit.title or fix.section_title,
    )


def reply_offers_actionable_fixes(reply: str) -> bool:
    """True when an advisory reply lists errors/recommendations worth applying."""
    text = (reply or "").strip()
    if not text or len(text) < 80:
        return False
    if _VERIFY_TAG_RECOMMEND_RE.search(text):
        return True
    if _CLEAN_PASS_RE.search(text) and not _RECOMMENDATION_REPLY_RE.search(text):
        if not re.search(r"(?is)\[pricing\s+flag:", text):
            return False
    if re.search(r"(?is)\[pricing\s+flag:", text) and _PRICING_CAPACITY_AUDIT_RE.search(
        text
    ):
        return True
    return bool(_RECOMMENDATION_REPLY_RE.search(text))


def reply_recommends_verify_tag_insert(reply: str) -> bool:
    """True when the reply asks to plant a [VERIFY: …] flag (not rewrite contacts)."""
    return bool(_VERIFY_TAG_RECOMMEND_RE.search(reply or ""))


_RFP_FIT_AUDIT_RE = re.compile(
    r"(?is)\b("
    r"not\s+(?:the\s+)?best\s+fit|not\s+well[- ]suited|"
    r"replace\s+this\s+case\s+study|weak\s+fit|"
    r"does\s+not\s+(?:meet|match)\s+(?:the\s+)?rfp|"
    r"one-time\s+(?:event|festival)\b.{0,40}\bcampaign|"
    r"ongoing\s+(?:social\s+media|destination)\b.{0,40}\bmanagement"
    r")\b"
)


def reply_recommends_rfp_fit_replacement(reply: str) -> bool:
    """True when audit says an example is a weak RFP fit and should be swapped."""
    return bool(_RFP_FIT_AUDIT_RE.search(reply or ""))


def build_verify_tag_instruction(
    *,
    section_title: str,
    reply: str,
) -> str:
    """Insert the recommended [VERIFY: …] flag; do not invent board/contact facts."""
    title = (section_title or "this section").strip() or "this section"
    audit = re.sub(r"\s+", " ", (reply or "").strip())[:1800]
    tags = re.findall(r"\[VERIFY:[^\]]+\]", reply or "", flags=re.I)
    tag_line = tags[0] if tags else "[VERIFY: confirm before submission]"
    return (
        f"Edit ONLY the sidebar section titled “{title}”. "
        "Do NOT invent board members, contacts, or compliance facts.\n"
        f"1) Near the board roster / trustees list (or at the top of the form), insert "
        f"exactly once: {tag_line}\n"
        "2) If that VERIFY tag is already present, leave it — do not duplicate.\n"
        "3) Keep disclosure table answers and verified contact fields unchanged "
        "(email/phone/address/legal name that the audit marked correct).\n"
        "4) Do NOT remove or rewrite board names — human confirmation only.\n\n"
        f"Audit to follow:\n{audit}"
    )


def build_rfp_fit_replace_instruction(
    *,
    section_title: str,
    reply: str,
) -> str:
    """Replace weak tourism/event examples on the open RFP section from KB evidence."""
    title = (section_title or "this section").strip() or "this section"
    audit = re.sub(r"\s+", " ", (reply or "").strip())[:1800]
    return (
        f"Edit ONLY the sidebar section titled “{title}”. "
        "The audit says at least one example here is a weak fit for the RFP. "
        "Using PACKED KB EVIDENCE from this turn:\n"
        "1) Replace or rewrite weak event/festival examples with a better-matching "
        "KB-backed tourism or destination case study (strategy + KPIs from evidence).\n"
        "2) Remove cross-refs to misfit Our Work pieces when this section should "
        "stand on its own.\n"
        "3) Keep [VERIFY] only for fields truly missing from KB.\n"
        "4) Do NOT invent overnight visitation, conversion, or visitor-spending metrics.\n"
        "5) Preserve entries the audit marked as correct or well-evidenced.\n\n"
        f"Audit to follow:\n{audit}"
    )


def build_pricing_flag_instruction(
    *,
    section_title: str,
    reply: str,
) -> str:
    """Insert PRICING FLAG from audit — capacity/hours/budget, not contact scrub."""
    title = (section_title or "this section").strip() or "this section"
    audit = re.sub(r"\s+", " ", (reply or "").strip())[:1800]
    return (
        f"Edit ONLY the sidebar section titled “{title}”. "
        "The audit could not verify capacity/pricing numbers against KB.\n"
        "1) Do NOT rewrite the hour table or dollar amounts unless the audit marks "
        "them **Incorrect**.\n"
        "2) If the audit includes a [PRICING FLAG: …] line, insert it once at the top "
        "of this section (or next to the capacity table) exactly as written.\n"
        "3) If no PRICING FLAG text is in the audit, add: "
        "[PRICING FLAG: Monthly capacity allocation not verified against KB guide — "
        "Sonja review required]\n"
        "4) Do NOT remove contacts, clients, or unrelated prose.\n\n"
        f"Audit to follow:\n{audit}"
    )


def build_safe_scrub_instruction(
    *,
    section_title: str,
    reply: str,
) -> str:
    """Imperative single-section scrub — never invent KB contacts or clients."""
    title = (section_title or "this section").strip() or "this section"
    # Keep a truncated audit for the rewriter; full reply can be huge.
    audit = re.sub(r"\s+", " ", (reply or "").strip())[:1800]
    return (
        f"Edit ONLY the sidebar section titled “{title}”. "
        "Apply the safe scrub from this audit — do not invent facts:\n"
        "1) Remove contact name/title/phone/email/address that the audit says are "
        "missing from KB or invented; keep the client name only if ClientList-approved, "
        "or replace contact lines with [VERIFY: Sonja confirm reference contact].\n"
        "2) Remove clients the audit says are absent from ClientList / case studies, "
        "unless you can cite KB evidence in this turn; otherwise delete those entries "
        "or mark [VERIFY: Sonja — not on approved ClientList].\n"
        "3) Soften or remove result claims the audit flags as unverifiable.\n"
        "4) Do NOT add new reference contacts, phones, emails, or addresses from memory.\n"
        "5) Keep RFP-aligned structure; preserve any entries the audit marked Correct "
        "when contact data is actually evidenced.\n\n"
        f"Audit to follow:\n{audit}"
    )


def resolve_advisory_suggested_fix(
    raw: dict[str, Any] | None,
    *,
    fallback_section_id: str,
    section_title: str = "",
    draft: ProposalDraft | None = None,
) -> SuggestedFix | None:
    """Model hasFix wins; else synthesize a safe scrub when the reply recommends fixes."""
    parsed = validate_suggested_fix_section(
        parse_advisory_suggested_fix(raw, fallback_section_id=fallback_section_id),
        draft,
    )
    if parsed is not None:
        return parsed

    reply = ""
    if isinstance(raw, dict):
        reply = str(raw.get("reply") or "").strip()
    if not reply_offers_actionable_fixes(reply):
        return None

    title = (section_title or "").strip()
    if not title and draft is not None:
        hit = next((s for s in draft.sections if s.id == fallback_section_id), None)
        title = (hit.title if hit else "") or ""

    if reply_recommends_verify_tag_insert(reply):
        instruction = build_verify_tag_instruction(section_title=title, reply=reply)
        summary = "Insert recommended [VERIFY] flag (no invented facts)"
    elif reply_recommends_rfp_fit_replacement(reply):
        instruction = build_rfp_fit_replace_instruction(
            section_title=title, reply=reply
        )
        summary = "Replace weak example with KB-backed tourism case study"
    elif _PRICING_CAPACITY_AUDIT_RE.search(reply) and not _CONTACT_AUDIT_RE.search(
        reply
    ):
        instruction = build_pricing_flag_instruction(section_title=title, reply=reply)
        summary = "Add PRICING FLAG from audit (capacity/pricing)"
    elif _CONTACT_AUDIT_RE.search(reply) and not _CONTACTS_VERIFIED_OK_RE.search(reply):
        instruction = build_safe_scrub_instruction(section_title=title, reply=reply)
        summary = "Apply safe scrub from audit (no invented contacts)"
    else:
        # Generic audit with no contact/pricing/VERIFY shape — do not offer a
        # misleading button (including when contacts were marked correct).
        return None

    synthesized = SuggestedFix(
        section_id=fallback_section_id,
        instruction=instruction,
        summary=summary,
        section_title=title,
    )
    return validate_suggested_fix_section(synthesized, draft)
