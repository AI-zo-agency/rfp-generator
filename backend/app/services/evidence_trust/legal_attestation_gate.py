"""Deterministic legal / attestation gates for Senior Editor KB fact-check.

Converts confident-but-unverified certifications (E-Verify under perjury, conflict
disclosures) into [VERIFY]/[FLAG] tags. Also flags invented staffing hours, filler
credentials, and missing near-direct case studies (e.g. Recovery Network of Oregon).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services.evidence_trust.flags import verify_gap

# Locked VERIFY tags must never be auto-filled by KB blob substitution.
LEGAL_VERIFY_LOCK_RE = re.compile(
    r"(?i)e-?verify|perjury|conflict\s+of\s+interest|disclosure\s+statement|"
    r"attestation|affidavit|staffing\s+hours|invented\s+hours|percent\s*time|"
    r"gross-receipts|sonja|operations\s+confirm|confirm\s+with\s+(?:sonja|ella|operations)",
)

_EVERIFY_ASSERTED_RE = re.compile(
    r"(?is)"
    r"(?:maintains?\s+active\s+participation\s+in\s+(?:the\s+)?(?:federal\s+)?e-?verify"
    r"|(?:actively\s+)?(?:participat(?:es|ing)|enrolled|registered)\s+in\s+"
    r"(?:the\s+)?(?:federal\s+)?e-?verify"
    r"|(?:we|zö|the\s+(?:offeror|agency|firm|company|undersigned))\s+"
    r"(?:are|is|do|does|maintain|maintains|participate|participates|enroll(?:ed|s)?|"
    r"register(?:ed|s)?)\s+"
    r"(?:an?\s+)?(?:active\s+)?"
    r"(?:participant|enrollment|registered|compliant)?\s*"
    r"(?:in\s+)?(?:the\s+)?(?:federal\s+)?e-?verify"
    r"|e-?verify\s+(?:compliance|enrollment|participation)\s+is\s+"
    r"(?:true|current|active|confirmed|complete)"
    r"|information\s+provided\s+regarding\s+e-?verify\s+compliance\s+is\s+"
    r"(?:true|accurate|correct))",
)

_PERJURY_HINT_RE = re.compile(
    r"(?i)penalty\s+of\s+perjury|under\s+penalty|affidavit|attests?\s+under|"
    r"false\s+statements?\s+may\s+result|sworn",
)

_CONFLICT_ASSERTED_RE = re.compile(
    r"(?is)"
    r"(?:we\s+have\s+no\s+(?:known\s+)?"
    r"(?:financial\s+)?(?:relationships?|conflicts?(?:\s+of\s+interest)?)"
    r"|no\s+(?:known\s+)?conflicts?\s+of\s+interest"
    r"|does\s+not\s+have\s+any\s+(?:known\s+)?conflicts?\s+of\s+interest"
    r"|no\s+financial\s+relationships?.{0,60}that\s+would\s+create\s+conflicts?"
    r"|free\s+of\s+(?:any\s+)?conflicts?\s+of\s+interest"
    r"|nothing\s+to\s+disclose\s+(?:regarding\s+)?conflicts?)",
)

_STAFFING_HOURS_RE = re.compile(
    r"(?i)"
    r"(?:\b(?:400|320|280|200|160)\s*hours?\b"
    r"|\b\d{2,4}\s*hours?\s*(?:per\s+year|annually|/yr|/year|each\s+year)\b"
    r"|\b(?:strategy|creative|digital|account|project\s+manag\w*)\b.{0,40}"
    r"\b\d{2,4}\s*hours?\b)",
)

_TEN_YEAR_FILLER_RE = re.compile(
    r"(?i)"
    r"10[-\s]?year\s+(?:corporate[-\s]?creative\s+)?partnership(?:\s+model)?"
    r"|(?:corporate[-\s]?creative\s+)?partnership\s+model.{0,40}10\s*years?"
    r"|ten[-\s]?year\s+(?:corporate[-\s]?creative\s+)?partnership",
)

_HEALTH_COALITION_RFP_RE = re.compile(
    r"(?i)health\s+polic|ARCHI|public\s+health|stigma|coalition|"
    r"social\s+market|behavioral\s+health|recovery|substance|"
    r"community\s+engagement|lived\s+experience",
)

_RNO_MENTION_RE = re.compile(
    r"(?i)recovery\s+network\s+of\s+oregon|\bRNO\b|oregon\s+recovers",
)

_EVERIFY_VERIFY = verify_gap(
    "E-Verify enrollment",
    "unconfirmed in KB — Sonja/Operations must confirm before any sworn affidavit "
    "or penalty-of-perjury attestation",
)

_CONFLICT_VERIFY = verify_gap(
    "conflict-of-interest disclosure",
    "must be confirmed by Sonja/leadership — do not pre-assert 'no conflicts'",
)

_HOURS_VERIFY = verify_gap(
    "staffing hours",
    "hour allocations not found as verified facts in KB — confirm with Ella/pricing "
    "or remove invented annual hours",
)

_PERCENT_TIME_VERIFY = "[VERIFY: percent time]"

_PERCENT_TIME_CONTEXT_RE = re.compile(
    r"(?i)percent[-\s]?time|%\s*time|fte\b|allocation\s*%|"
    r"percent-time\s+commitments|dedicated\s+allocation|"
    r"of\s+(?:their|his|her|our)\s+time",
)

# Markdown table cells like | 10% | or | 25-30% |
_TABLE_PCT_CELL_RE = re.compile(
    r"(\|\s*)(\d{1,3}(?:\s*[-–—]\s*\d{1,3})?\s*%)(\s*\|)"
)

# Prose: "35% of their time" / "commits 25% time"
_PROSE_PCT_TIME_RE = re.compile(
    r"(?i)\b(\d{1,3}(?:\s*[-–—]\s*\d{1,3})?\s*%)(\s+"
    r"(?:of\s+(?:their|his|her|our)\s+time|time(?:\s+commitment)?|FTE|allocation))\b"
)

_RNO_FLAG = (
    "[FLAG FOR SONJA: Add Recovery Network of Oregon (RNO) — near-direct coalition "
    "health/stigma communications proof with metrics; strongest KB match for this RFP "
    "scope; prefer for references / previous experience / case studies]"
)


@dataclass
class LegalAttestationReport:
    everify_flags: int = 0
    conflict_flags: int = 0
    hours_flags: int = 0
    percent_time_flags: int = 0
    filler_flags: int = 0
    rno_flags: int = 0
    logs: list[str] = field(default_factory=list)


def is_locked_legal_verify_tag(tag_inner: str) -> bool:
    """True when a VERIFY tag must not be auto-cleared by KB fill / VERIFY cleanup."""
    return bool(LEGAL_VERIFY_LOCK_RE.search(tag_inner or ""))


def rfp_needs_health_coalition_proof(
    rfp: RfpRecord | object | None,
    rfp_context: str = "",
) -> bool:
    blob = " ".join(
        [
            str(getattr(rfp, "title", "") or ""),
            str(getattr(rfp, "client", "") or ""),
            str(getattr(rfp, "sector", "") or ""),
            rfp_context or "",
        ]
    )
    return bool(_HEALTH_COALITION_RFP_RE.search(blob))


def _replace_asserted_everify(content: str) -> tuple[str, int]:
    if not content or not _EVERIFY_ASSERTED_RE.search(content):
        return content, 0
    if "[VERIFY:" in content and re.search(r"(?i)\[VERIFY:[^\]]*e-?verify", content):
        # Already gated — still strip remaining confident assertions.
        pass
    updated = _EVERIFY_ASSERTED_RE.sub(_EVERIFY_VERIFY, content)
    # If perjury language remains without a VERIFY nearby, prepend a hard stop.
    if _PERJURY_HINT_RE.search(updated) and not re.search(
        r"(?i)\[VERIFY:[^\]]*e-?verify", updated
    ):
        updated = (
            f"{_EVERIFY_VERIFY}\n\n"
            "Do not sign or submit this affidavit until Sonja/Operations confirms "
            "active federal E-Verify enrollment.\n\n"
            + updated
        )
    changes = 0 if updated == content else 1
    return updated, changes


def _replace_asserted_conflicts(content: str) -> tuple[str, int]:
    if not content or not _CONFLICT_ASSERTED_RE.search(content):
        return content, 0
    if re.search(r"(?i)\[VERIFY:[^\]]*conflict", content or ""):
        updated = _CONFLICT_ASSERTED_RE.sub(_CONFLICT_VERIFY, content)
    else:
        updated = _CONFLICT_ASSERTED_RE.sub(_CONFLICT_VERIFY, content)
    return updated, (0 if updated == content else 1)


def _flag_invented_hours(content: str) -> tuple[str, int]:
    if not content or not _STAFFING_HOURS_RE.search(content):
        return content, 0
    if re.search(r"(?i)\[VERIFY:[^\]]*staffing\s+hours", content):
        return content, 0

    def _repl(match: re.Match[str]) -> str:
        return f"{match.group(0)} {_HOURS_VERIFY}"

    updated, n = _STAFFING_HOURS_RE.subn(_repl, content, count=3)
    return updated, n


def _flag_invented_percent_time(content: str) -> tuple[str, int]:
    """Replace invented percent-time / FTE % figures with [VERIFY: percent time]."""
    if not content or not _PERCENT_TIME_CONTEXT_RE.search(content):
        return content, 0

    flags = 0
    updated = content

    def _table_repl(match: re.Match[str]) -> str:
        nonlocal flags
        flags += 1
        return f"{match.group(1)}{_PERCENT_TIME_VERIFY}{match.group(3)}"

    updated = _TABLE_PCT_CELL_RE.sub(_table_repl, updated)

    def _prose_repl(match: re.Match[str]) -> str:
        nonlocal flags
        flags += 1
        return f"{_PERCENT_TIME_VERIFY}{match.group(2)}"

    updated = _PROSE_PCT_TIME_RE.sub(_prose_repl, updated)
    return updated, flags


def scrub_invented_percent_time(content: str) -> tuple[str, int]:
    """Public wrapper — replace invented percent-time cells with [VERIFY: percent time]."""
    return _flag_invented_percent_time(content)


def _fix_ten_year_filler(content: str) -> tuple[str, int]:
    if not content or not _TEN_YEAR_FILLER_RE.search(content):
        return content, 0
    replacement = (
        "zö agency (founded August 21, 2013 — 13 years as of 2026) "
        "[VERIFY: partnership-model phrasing — replace decade filler with a "
        "KB-backed credential]"
    )
    updated, n = _TEN_YEAR_FILLER_RE.subn(replacement, content)
    return updated, n


def _section_is_attestation_like(section: ProposalSection) -> bool:
    title = (section.title or "").casefold()
    body = (section.content or "").casefold()
    hints = (
        "e-verify",
        "affidavit",
        "disclosure",
        "conflict",
        "tax compliance",
        "contractor affidavit",
        "certification",
        "non-collusion",
    )
    return any(h in title or h in body[:500] for h in hints)


def _pick_rno_section(draft: ProposalDraft) -> ProposalSection | None:
    ranked: list[tuple[int, ProposalSection]] = []
    for section in draft.sections:
        title = (section.title or "").casefold()
        sid = (section.id or "").casefold()
        score = 0
        if "reference" in title:
            score = 50
        elif "previous experience" in title or "past performance" in title:
            score = 40
        elif sid.startswith("section-3") or "case stud" in title or "our work" in title:
            score = 30
        elif "experience" in title:
            score = 20
        if score and (section.content or "").strip():
            ranked.append((score, section))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def gate_section_legal_attestations(
    section: ProposalSection,
    *,
    force: bool = False,
) -> tuple[ProposalSection, LegalAttestationReport]:
    """Scrub one section for unverified legal attestations and filler credentials."""
    report = LegalAttestationReport()
    content = section.content or ""
    if not content.strip():
        return section, report

    run_legal = force or _section_is_attestation_like(section) or bool(
        _EVERIFY_ASSERTED_RE.search(content)
        or _CONFLICT_ASSERTED_RE.search(content)
        or _PERJURY_HINT_RE.search(content)
    )

    if run_legal:
        content, n = _replace_asserted_everify(content)
        if n:
            report.everify_flags += n
            report.logs.append(
                f"Gated E-Verify attestation in {section.title} → VERIFY for Sonja/Operations"
            )
        content, n = _replace_asserted_conflicts(content)
        if n:
            report.conflict_flags += n
            report.logs.append(
                f"Gated conflict-disclosure assertion in {section.title} → VERIFY for Sonja"
            )

    content, n = _flag_invented_hours(content)
    if n:
        report.hours_flags += n
        report.logs.append(
            f"Flagged unverified staffing hours in {section.title}"
        )

    content, n = _flag_invented_percent_time(content)
    if n:
        report.percent_time_flags += n
        report.logs.append(
            f"Flagged invented percent-time / FTE figures in {section.title} → VERIFY"
        )

    content, n = _fix_ten_year_filler(content)
    if n:
        report.filler_flags += n
        report.logs.append(
            f"Replaced '10-year partnership' filler in {section.title}"
        )

    if content != (section.content or ""):
        section = section.model_copy(update={"content": content})
    return section, report


def ensure_rno_flagged_for_health_rfp(
    draft: ProposalDraft,
    *,
    rfp: RfpRecord | object | None,
    rfp_context: str = "",
) -> tuple[ProposalDraft, LegalAttestationReport]:
    """If RFP needs coalition-health proof and RNO is absent, FLAG the best section."""
    report = LegalAttestationReport()
    if not rfp_needs_health_coalition_proof(rfp, rfp_context):
        return draft, report

    blob = "\n".join(s.content or "" for s in draft.sections)
    if _RNO_MENTION_RE.search(blob):
        return draft, report

    target = _pick_rno_section(draft)
    if not target:
        report.logs.append(
            "Health/coalition RFP but no section available to FLAG Recovery Network of Oregon"
        )
        return draft, report

    body = (target.content or "").rstrip()
    if _RNO_FLAG in body:
        return draft, report
    updated = f"{body}\n\n{_RNO_FLAG}\n"
    sections = [
        s.model_copy(update={"content": updated}) if s.id == target.id else s
        for s in draft.sections
    ]
    report.rno_flags = 1
    report.logs.append(
        f"FLAGGED missing Recovery Network of Oregon on {target.title} "
        "(near-direct coalition health proof)"
    )
    return draft.model_copy(update={"sections": sections}), report


def strip_rno_flags_when_not_health_rfp(
    draft: ProposalDraft,
    *,
    rfp: RfpRecord | object | None,
    rfp_context: str = "",
) -> tuple[ProposalDraft, LegalAttestationReport]:
    """Remove Recovery Network of Oregon FLAGS left over from health RFPs on other bids."""
    report = LegalAttestationReport()
    if rfp_needs_health_coalition_proof(rfp, rfp_context):
        return draft, report

    flag_re = re.compile(
        r"\n*\s*\[FLAG FOR SONJA:[^\]]*Recovery Network of Oregon[^\]]*\]\s*",
        re.I | re.S,
    )
    changed = False
    sections: list[ProposalSection] = []
    for section in draft.sections:
        body = section.content or ""
        if not flag_re.search(body):
            sections.append(section)
            continue
        updated = flag_re.sub("\n\n", body)
        updated = re.sub(r"\n{3,}", "\n\n", updated).strip() + "\n"
        sections.append(section.model_copy(update={"content": updated}))
        changed = True
        report.logs.append(
            f"Removed cross-RFP Recovery Network of Oregon FLAG from {section.title} "
            "(not a health/coalition RFP)"
        )
        report.rno_flags += 1

    if not changed:
        return draft, report
    return draft.model_copy(update={"sections": sections}), report


def apply_legal_attestation_gates(
    draft: ProposalDraft,
    *,
    rfp: RfpRecord | object | None = None,
    rfp_context: str = "",
) -> tuple[ProposalDraft, LegalAttestationReport]:
    """Run attestation + filler + RNO gates across the manuscript."""
    combined = LegalAttestationReport()
    updated: list[ProposalSection] = []
    for section in draft.sections:
        section, report = gate_section_legal_attestations(section)
        updated.append(section)
        combined.everify_flags += report.everify_flags
        combined.conflict_flags += report.conflict_flags
        combined.hours_flags += report.hours_flags
        combined.filler_flags += report.filler_flags
        combined.logs.extend(report.logs)

    draft = draft.model_copy(update={"sections": updated})
    draft, strip_report = strip_rno_flags_when_not_health_rfp(
        draft, rfp=rfp, rfp_context=rfp_context
    )
    combined.logs.extend(strip_report.logs)
    draft, rno_report = ensure_rno_flagged_for_health_rfp(
        draft, rfp=rfp, rfp_context=rfp_context
    )
    combined.rno_flags += rno_report.rno_flags
    combined.logs.extend(rno_report.logs)
    return draft, combined
