"""T2 manuscript validators — typed FactLedger consistency (W4 T4.4).

Detection only in scan_manuscript_consistency; blocking gated separately.
"""

from __future__ import annotations

import logging
import re
from typing import Literal, TypedDict

from app.models.fact_ledger import ClaimClass, FactLedger, LedgerClaim
from app.models.proposal import ProposalDraft, ProposalSection

logger = logging.getLogger(__name__)

T2Severity = Literal["warning", "critical"]


class T2Finding(TypedDict):
    code: str
    severity: T2Severity
    category: Literal["fact_ledger"]
    section_id: str | None
    section_title: str | None
    message: str
    excerpt: str | None
    blocker: bool


# "35+ years" / "38 years" / "almost 40 years"
_YEARS_NEAR_NAME_RE = re.compile(
    r"(\d{1,2}(?:\.\d+)?)\s*\+?\s*years?",
    re.IGNORECASE,
)


def scan_years_experience_against_ledger(
    draft: ProposalDraft,
    ledger: FactLedger,
) -> list[T2Finding]:
    """Flag manuscript years near a ledger person that disagree with authoritative claim(s)."""
    findings: list[T2Finding] = []
    year_claims = [
        c
        for c in ledger.claims
        if c.claim_class == ClaimClass.YEARS_EXPERIENCE and c.value_number is not None
    ]
    if not year_claims:
        return findings

    # If ledger already has blocking conflicts for a person, any render is suspect —
    # report manuscript divergence from *each* claimed number only when a single
    # authoritative number exists; otherwise report the ledger conflict itself.
    by_person: dict[str, list[LedgerClaim]] = {}
    for claim in year_claims:
        by_person.setdefault(claim.subject_id, []).append(claim)

    name_by_id = {p.person_id: p.name for p in ledger.people}

    for person_id, claims in by_person.items():
        name = name_by_id.get(person_id) or person_id
        nums = sorted({round(float(c.value_number), 2) for c in claims if c.value_number is not None})
        if len(nums) > 1:
            # Ledger itself unresolved — surface once at manuscript level.
            findings.append(
                T2Finding(
                    code="t2.fact_ledger.years_unresolved",
                    severity="critical",
                    category="fact_ledger",
                    section_id=None,
                    section_title=None,
                    message=(
                        f"Fact ledger has unresolved years_experience for {name}: "
                        + " vs ".join(str(n) for n in nums)
                    ),
                    excerpt=None,
                    blocker=True,
                )
            )
            # Still scan for manuscript stating multiple different years for this name.
            manuscript_nums = _extract_years_near_name(draft, name)
            if len(set(manuscript_nums)) > 1:
                for section in draft.sections:
                    found = _years_in_section_near_name(section, name)
                    if len(set(found)) > 1:
                        findings.append(
                            T2Finding(
                                code="t2.fact_ledger.years_manuscript_conflict",
                                severity="critical",
                                category="fact_ledger",
                                section_id=section.id,
                                section_title=section.title,
                                message=(
                                    f"Manuscript states conflicting years for {name} "
                                    f"in one section: {sorted(set(found))}"
                                ),
                                excerpt=(section.content or "")[:120],
                                blocker=True,
                            )
                        )
            continue

        auth = nums[0]
        for section in draft.sections:
            found = _years_in_section_near_name(section, name)
            for n in found:
                if abs(n - auth) > 0.51:
                    findings.append(
                        T2Finding(
                            code="t2.fact_ledger.years_mismatch",
                            severity="critical",
                            category="fact_ledger",
                            section_id=section.id,
                            section_title=section.title,
                            message=(
                                f"{name}: manuscript states {n:g} years but ledger "
                                f"authoritative value is {auth:g}"
                            ),
                            excerpt=_excerpt_around_years(section.content or "", name),
                            blocker=True,
                        )
                    )

    # Cross-section: same person, different years in manuscript even without ledger auth.
    for person_id, claims in by_person.items():
        name = name_by_id.get(person_id) or person_id
        by_section: dict[str, list[float]] = {}
        for section in draft.sections:
            found = _years_in_section_near_name(section, name)
            if found:
                by_section[section.id] = found
        flat = [n for vals in by_section.values() for n in vals]
        if len(set(round(n, 2) for n in flat)) > 1:
            findings.append(
                T2Finding(
                    code="t2.fact_ledger.years_cross_section",
                    severity="critical",
                    category="fact_ledger",
                    section_id=None,
                    section_title=None,
                    message=(
                        f"{name}: conflicting years across sections "
                        f"{sorted({round(n, 2) for n in flat})}"
                    ),
                    excerpt=None,
                    blocker=True,
                )
            )

    if findings:
        logger.info(
            "t2_years_scan findings=%s draft_sections=%s",
            len(findings),
            len(draft.sections),
        )
    return findings


def scan_all_t2(draft: ProposalDraft, ledger: FactLedger | None) -> list[T2Finding]:
    if ledger is None:
        return []
    return scan_years_experience_against_ledger(draft, ledger)


def _years_in_section_near_name(section: ProposalSection, name: str) -> list[float]:
    text = section.content or ""
    if not name or name.casefold() not in text.casefold():
        return []
    # Window: ±120 chars around each name occurrence
    out: list[float] = []
    lower = text.casefold()
    needle = name.casefold()
    start = 0
    while True:
        idx = lower.find(needle, start)
        if idx < 0:
            break
        window = text[max(0, idx - 120) : idx + len(name) + 120]
        for match in _YEARS_NEAR_NAME_RE.finditer(window):
            out.append(float(match.group(1)))
        start = idx + len(needle)
    return out


def _extract_years_near_name(draft: ProposalDraft, name: str) -> list[float]:
    found: list[float] = []
    for section in draft.sections:
        found.extend(_years_in_section_near_name(section, name))
    return found


def _excerpt_around_years(text: str, name: str, *, limit: int = 120) -> str:
    idx = text.casefold().find(name.casefold())
    if idx < 0:
        return text[:limit]
    snippet = text[max(0, idx - 40) : idx + len(name) + 80].strip()
    if len(snippet) > limit:
        return snippet[: limit - 1] + "…"
    return snippet
