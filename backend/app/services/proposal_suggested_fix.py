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
_RECOMMENDATION_REPLY_RE = re.compile(
    r"(?is)\b("
    r"recommendation(?:s)?|"
    r"apply\s+these\s+fixes|"
    r"factual\s+errors?|"
    r"unverifiable|"
    r"cannot\s+confirm|"
    r"incorrect\s*:|"
    r"problem\s*:|"
    r"kb\s+coverage\s*:\s*none|"
    r"does\s+not\s+(?:provide|mention)|"
    r"invented\s+these\s+details|"
    r"remove\s+or\s+verify|"
    r"flag\s+for\s+sonja"
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
    if _CLEAN_PASS_RE.search(text) and not _RECOMMENDATION_REPLY_RE.search(text):
        return False
    return bool(_RECOMMENDATION_REPLY_RE.search(text))


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

    synthesized = SuggestedFix(
        section_id=fallback_section_id,
        instruction=build_safe_scrub_instruction(section_title=title, reply=reply),
        summary="Apply safe scrub from audit (no invented contacts)",
        section_title=title,
    )
    return validate_suggested_fix_section(synthesized, draft)
