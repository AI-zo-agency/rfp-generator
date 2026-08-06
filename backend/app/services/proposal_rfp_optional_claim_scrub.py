"""RFP-aware removal of optional claim types and handoff-tag noise.

Option B (2026-07-31): strip designer notes, auditor-echo MANUAL FILL tags, and
(when the RFP is silent) invented percent-time / named-subcontractor lines.
Never invent replacement facts.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Sequence

from app.models.proposal import ProposalDraft, ProposalSection

# Single shared definition of "internal handoff tag" — owned by
# proposal_manuscript (a dependency-free leaf module) and reused here so the two
# call sites cannot drift. They already had: the export-side copy matched only
# `FLAG\s+FOR` and therefore missed the bare `[FLAG: ...]` tags this pattern
# always caught, leaking them into client-facing DOCX exports.
from app.services.proposal_manuscript import (
    INTERNAL_HANDOFF_TAG_RE as _HANDOFF_TAG_STRIP_RE,
)

logger = logging.getLogger(__name__)

_DESIGNER_NOTE_RE = re.compile(
    r"\[DESIGNER\s+NOTE:[^\]]*\]",
    re.IGNORECASE,
)

_MANUAL_FILL_TAG_RE = re.compile(
    r"\[MANUAL\s+FILL:[^\]]*\]",
    re.IGNORECASE,
)

# Process / auditor echoes — not submission content Sonja/Ella must fill.
_AUDITOR_ECHO_HINTS = (
    "mid-sentence without terminal punctuation",
    "section ends mid-sentence",
    "dollar amount $",
    "does not match canonical budget",
    "certifications mentioned (should only",
    "should only appear in section 1.4",
    "grammar or pronoun error",
    "narrative section uses \"the vendor\"",
    "possible wrong-client reference",
    "unresolved tag:",
    "adversarial repair loop",
    # Hallucination-detector false positives on verified WBENC/WOSB prose
    "certification not in verified list",
    "deterministic.fabricated_fact",
    "deterministic.unverified",
)

_RFP_PERCENT_TIME_RE = re.compile(
    r"\b(?:percent(?:age)?[\s-]*time|%\s*time|fte|full[\s-]*time\s+equivalent|"
    r"dedicated\s+(?:allocation|percent)|hours?\s+per\s+week\s+allocat)\b",
    re.IGNORECASE,
)

_RFP_NAMED_SUB_RE = re.compile(
    r"\b(?:named?\s+subcontractor|subcontractor\s+name|list\s+(?:all\s+)?subcontractors|"
    r"identify\s+(?:each\s+)?subcontractor|proposed\s+subcontractors?)\b",
    re.IGNORECASE,
)

_NAMED_SUB_LINE_RE = re.compile(
    r"(?im)^[ \t]*(?:[-*]|\d+[.)])?[ \t]*"
    r"subcontractor\s*:\s*[^\n]+$",
)

def strip_handoff_tags_for_scan(content: str) -> str:
    """Remove handoff tags so truncation/punctuation scanners see real prose."""
    text = _HANDOFF_TAG_STRIP_RE.sub("", content or "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_designer_notes(content: str) -> tuple[str, int]:
    """Remove [DESIGNER NOTE: …] except bio-PDF insert handoffs (Option B stubs)."""
    body = content or ""
    removed = 0

    def _repl(match: re.Match[str]) -> str:
        nonlocal removed
        tag = match.group(0)
        # Keep intentional bio PDF insert notes — scrub must not undo Section 2 stubs.
        if re.search(r"Insert approved bio PDF|04_Bio_", tag, re.IGNORECASE):
            return tag
        removed += 1
        return ""

    out = _DESIGNER_NOTE_RE.sub(_repl, body)
    if removed:
        out = re.sub(r"\n{3,}", "\n\n", out).strip()
        if body.endswith("\n") and out:
            out += "\n"
    return out, removed


def strip_auditor_echo_manual_fills(content: str) -> tuple[str, int]:
    """Remove MANUAL FILL tags that only echo auditor findings."""
    body = content or ""
    removed = 0

    def _repl(match: re.Match[str]) -> str:
        nonlocal removed
        inner = match.group(0).casefold()
        if any(hint in inner for hint in _AUDITOR_ECHO_HINTS):
            removed += 1
            return ""
        return match.group(0)

    out = _MANUAL_FILL_TAG_RE.sub(_repl, body)
    if removed:
        out = re.sub(r"\n{3,}", "\n\n", out).strip()
        if body.endswith("\n") and out:
            out += "\n"
    return out, removed


def rfp_requires_percent_time(rfp_text: str) -> bool:
    return bool(_RFP_PERCENT_TIME_RE.search(rfp_text or ""))


def rfp_requires_named_subcontractors(rfp_text: str) -> bool:
    return bool(_RFP_NAMED_SUB_RE.search(rfp_text or ""))


# Extra prose shapes the legal gate may miss (e.g. "35% dedication").
_OPTIONAL_PCT_CLAIM_RE = re.compile(
    r"(?i)\b\d{1,3}(?:\s*[-–—]\s*\d{1,3})?\s*%\s*"
    r"(?:dedication|dedicated|fte|allocation|of\s+(?:their|his|her|our)\s+time|"
    r"time(?:\s+commitment)?)\b"
)


def scrub_percent_time_when_rfp_silent(
    content: str, *, rfp_text: str
) -> tuple[str, int]:
    """If RFP is silent on %-time/FTE, omit invented figures (do not leave VERIFY)."""
    if rfp_requires_percent_time(rfp_text):
        return content or "", 0
    body = content or ""
    flags = 0
    try:
        from app.services.evidence_trust.legal_attestation_gate import (
            scrub_invented_percent_time,
        )

        # Gate replaces with [VERIFY: percent time]; we then drop those tags
        # because the RFP does not require the field at all.
        body, n = scrub_invented_percent_time(body)
        flags += n
    except Exception:
        logger.debug("percent-time scrub unavailable", exc_info=True)

    body, n_extra = _OPTIONAL_PCT_CLAIM_RE.subn("", body)
    flags += n_extra

    before = body
    body = re.sub(
        r"\[VERIFY:\s*percent\s*time[^\]]*\]",
        "",
        body,
        flags=re.IGNORECASE,
    )
    if body != before:
        flags += 1
    if flags:
        body = re.sub(r"[ \t]{2,}", " ", body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body, flags


def scrub_named_subcontractors_when_rfp_silent(
    content: str, *, rfp_text: str
) -> tuple[str, int]:
    """Drop obvious 'Subcontractor: Name' lines when RFP does not require names."""
    if rfp_requires_named_subcontractors(rfp_text):
        return content or "", 0
    body = content or ""
    matches = list(_NAMED_SUB_LINE_RE.finditer(body))
    if not matches:
        return body, 0
    out = _NAMED_SUB_LINE_RE.sub("", body)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out, len(matches)


def scrub_section_optional_claims(
    content: str, *, rfp_text: str = ""
) -> tuple[str, list[str]]:
    """Apply all deterministic Option B scrubs to one section body."""
    logs: list[str] = []
    body = content or ""

    body, n = strip_designer_notes(body)
    if n:
        logs.append(f"removed {n} DESIGNER NOTE tag(s)")

    body, n = strip_auditor_echo_manual_fills(body)
    if n:
        logs.append(f"removed {n} auditor-echo MANUAL FILL tag(s)")

    body, n = scrub_percent_time_when_rfp_silent(body, rfp_text=rfp_text)
    if n:
        logs.append(f"scrubbed percent-time/FTE ({n})")

    body, n = scrub_named_subcontractors_when_rfp_silent(body, rfp_text=rfp_text)
    if n:
        logs.append(f"removed {n} named-subcontractor line(s)")

    return body, logs


def scrub_draft_optional_claims(
    sections: Sequence[ProposalSection],
    *,
    rfp_text: str = "",
    skip_section_ids: set[str] | None = None,
) -> tuple[list[ProposalSection], list[str]]:
    """Run Option B scrub across draft sections. Skips budget ids when provided."""
    skip = skip_section_ids or set()
    out: list[ProposalSection] = []
    logs: list[str] = []
    for section in sections:
        if section.id in skip:
            out.append(section)
            continue
        updated, section_logs = scrub_section_optional_claims(
            section.content or "", rfp_text=rfp_text
        )
        if section_logs:
            logs.append(f"{section.title or section.id}: " + "; ".join(section_logs))
            out.append(section.model_copy(update={"content": updated}))
        else:
            out.append(section)
    if logs:
        logger.info(
            "rfp_optional_claim_scrub sections_changed=%s details=%s",
            len(logs),
            logs[:8],
        )
    return out, logs


def apply_optional_claim_scrub_to_draft(
    draft: ProposalDraft,
    *,
    rfp_text: str = "",
    skip_section_ids: set[str] | None = None,
) -> tuple[ProposalDraft, list[str]]:
    sections, logs = scrub_draft_optional_claims(
        draft.sections, rfp_text=rfp_text, skip_section_ids=skip_section_ids
    )
    if not logs:
        return draft, logs
    return (
        draft.model_copy(
            update={
                "sections": sections,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        logs,
    )
