"""Deterministic scrub of fabricated / agency-overclaimed certifications.

RFP-agnostic: pattern denylist + verified agency allowlist from company facts.
Removes or rewrites claims; does not invent substitute credentials.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_hallucination_detector import VERIFIED_CERTIFICATIONS

logger = logging.getLogger(__name__)

# Agency-level certs we may state when publicly verifiable (companyfacts / B Lab registry).
PUBLICLY_VERIFIABLE_AGENCY_CERTS = frozenset(
    {*(c.upper() for c in VERIFIED_CERTIFICATIONS), "B CORPORATION", "B CORP"}
)

# Diversity program designations zö does not hold as separate certs (WBENC/WOSB are verified).
_DIVERSITY_FABRICATED_RES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\bMinority\s+Business\s+Enterprise\b(?:\s*\([^)]*\))?",
            re.I,
        ),
        "mbe",
    ),
    (
        re.compile(r"\b(?:certified\s+)?MBE\b(?:\s+certification)?", re.I),
        "mbe",
    ),
    (
        re.compile(
            r"\bDisadvantaged\s+Business\s+Enterprise\b(?:\s*\([^)]*\))?",
            re.I,
        ),
        "dbe",
    ),
    (
        re.compile(r"\b(?:certified\s+)?DBE\b(?:\s+certification)?", re.I),
        "dbe",
    ),
    (
        re.compile(
            r"\b(?:certified\s+)?Veterans?\s+Business\s+Enterprise\b(?:\s*\([^)]*\))?",
            re.I,
        ),
        "vbe",
    ),
]

# Standalone WBE designation — not the WBENC certificate id (WBE-####).
_STANDALONE_WBE_RE = re.compile(
    r"(?<![A-Z/])"
    r"(?:Women['']?s?\s+Business\s+Enterprise\s*\(\s*WBE\s*\)"
    r"|(?<!WBENC\s)\bWBE\b(?!\-\d{3,}))"
    r"(?![A-Z/])",
    re.I,
)

# Marketing / platform badges not on the verified agency cert list.
_UNVERIFIED_MARKETING_CERT_RES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"1\s*%\s*for\s*the\s*Planet(?:\s+membership)?", re.I),
        "one_percent_planet",
    ),
    (
        re.compile(r"LinkedIn\s+Gold(?:[- ]Certified)?(?:\s+status)?", re.I),
        "linkedin_gold",
    ),
]

# Phrases that must never appear as agency-level certifications (individual or invented).
_FABRICATED_OR_INDIVIDUAL_CERT_RES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"Spotify\s+API\s+[Cc]ertification(?:s)?", re.I),
        "spotify_api",
    ),
    (
        re.compile(r"ISO\s+\d+\s+(?:design|review)\s+[Cc]ertification(?:s)?", re.I),
        "iso_design",
    ),
    (
        re.compile(r"State\s+Teaching\s+[Ll]icense(?:s)?", re.I),
        "teaching_license",
    ),
]

# Agency-wide overclaims for individual platform certs.
_AGENCY_OVERCLAIM_RES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?:[Oo]ur\s+(?:certified\s+)?team|[Tt]he\s+agency|[Ww]e)\s+"
            r"(?:holds?|have|has|possess(?:es)?)\s+"
            r"(?:a\s+|an\s+)?"
            r"Google\s+Ads\s+[Cc]ertification(?:s)?",
            re.I,
        ),
        "google_ads_agency_overclaim",
    ),
    (
        re.compile(
            r"(?:[Oo]ur\s+(?:certified\s+)?team|[Tt]he\s+agency|[Ww]e)\s+"
            r"(?:holds?|have|has|possess(?:es)?)\s+"
            r"(?:a\s+|an\s+)?"
            r"Meta\s+(?:Ads\s+)?(?:Blueprint\s+)?[Cc]ertification(?:s)?",
            re.I,
        ),
        "meta_ads_agency_overclaim",
    ),
    (
        re.compile(
            r"agency[- ]wide\s+Google\s+Ads\s+[Cc]ertification(?:s)?",
            re.I,
        ),
        "google_ads_agency_wide",
    ),
    (
        re.compile(
            r"agency[- ]wide\s+Meta\s+(?:Ads\s+)?[Cc]ertification(?:s)?",
            re.I,
        ),
        "meta_ads_agency_wide",
    ),
    # SBA / status overclaim — WBENC/WOSB may be stated factually, but "confirms
    # SBA status" / "SBA-certified" language is not on the verified agency list.
    (
        re.compile(
            r"(?i)(?:SBA[- ]?certified|certified\s+by\s+the\s+SBA|"
            r"SBA\s+(?:8\(a\)\s+)?certification|"
            r"confirms?\s+(?:our\s+)?(?:SBA|WOSB|WBENC)\s+status|"
            r"SBA\s+confirms?\s+(?:our\s+)?status)",
        ),
        "sba_status_overclaim",
    ),
]

# List / matrix cells that enumerate invented certs alongside real ones.
_LIST_ITEM_FABRICATED_RE = re.compile(
    r"(?P<prefix>[,;|/]\s*)?(?P<claim>Spotify\s+API\s+[Cc]ertification(?:s)?)"
    r"(?P<suffix>\s*[,;|/])?",
    re.I,
)


def _is_verified_agency_cert_mention(text: str) -> bool:
    upper = text.upper()
    return any(cert in upper for cert in VERIFIED_CERTIFICATIONS)


def user_asks_cert_claim_scrub(text: str) -> bool:
    """True when chat should deterministically remove fabricated / unverified certs."""
    raw = (text or "").strip()
    if not raw:
        return False
    if re.search(
        r"(?is)\b(?:remove|delete|strip|drop|eliminate)\b.{0,80}\b"
        r"(?:MBE|WBE|DBE|false\s+certif|fabricated\s+certif|unverified\s+certif|"
        r"certifications?\s+that\s+do\s+not\s+exist)",
        raw,
    ):
        return True
    if re.search(
        r"(?is)\b(?:MBE|WBE|DBE)\b.{0,60}\b"
        r"(?:do\s+not\s+exist|not\s+in\s+(?:the\s+)?KB|false|fabricated|remove|delete)",
        raw,
    ):
        return True
    if re.search(
        r"(?is)\bretain\s+only\b.{0,80}\b(?:WBENC|WOSB|B\s+Corp)",
        raw,
    ):
        return True
    return False


def _segment_has_fabricated_cert(segment: str) -> bool:
    seg = segment or ""
    upper = seg.upper()
    if "WBENC" in upper and re.search(r"WBE-\d", seg, re.I):
        return False
    for pattern, _code in _DIVERSITY_FABRICATED_RES:
        if pattern.search(seg):
            return True
    if _STANDALONE_WBE_RE.search(seg):
        return True
    for pattern, _code in _UNVERIFIED_MARKETING_CERT_RES:
        if pattern.search(seg):
            return True
    return False


def _scrub_cert_enumeration(text: str) -> tuple[str, list[str]]:
    """Drop semicolon-separated cert list items that are fabricated or unverified."""
    if ";" not in text and "," not in text:
        return text, []
    logs: list[str] = []
    delimiter = ";" if ";" in text else ","
    parts = [p.strip() for p in re.split(rf"\s*{re.escape(delimiter)}\s*", text)]
    kept: list[str] = []
    for part in parts:
        if not part:
            continue
        if _segment_has_fabricated_cert(part):
            logs.append(f"dropped cert list item: {part[:60]}")
            continue
        kept.append(part)
    if kept == parts:
        return text, []
    rebuilt = f"{delimiter} ".join(kept)
    return rebuilt, logs


def _clean_cert_punctuation(text: str) -> str:
    out = text
    out = re.sub(r"\s*;\s*;\s*", "; ", out)
    out = re.sub(r"\(\s*;\s*", "(", out)
    out = re.sub(r"\s*;\s*\)", ")", out)
    out = re.sub(r"\s*,\s*,\s*", ", ", out)
    out = re.sub(r",\s*and\s*\.", ".", out)
    out = re.sub(r",\s*and\s*,", ",", out)
    out = re.sub(r"\s+,\s+\.", ".", out)
    out = re.sub(r"\|[ \t]*\|", "| |", out)
    out = re.sub(r"  +", " ", out)
    return out


def scrub_section_cert_claims(section: ProposalSection) -> tuple[ProposalSection, list[str]]:
    """Remove fabricated cert strings and soften agency-wide platform overclaims."""
    body = section.content or ""
    if not body.strip():
        return section, []

    logs: list[str] = []
    updated = body

    for pattern, code in _FABRICATED_OR_INDIVIDUAL_CERT_RES:
        if not pattern.search(updated):
            continue

        def _list_repl(match: re.Match[str]) -> str:
            prefix = match.group("prefix") or ""
            suffix = match.group("suffix") or ""
            if prefix and suffix:
                return suffix  # keep one delimiter
            if prefix:
                return ""
            if suffix:
                return ""
            return ""

        before = updated
        updated = _LIST_ITEM_FABRICATED_RE.sub(_list_repl, updated)
        updated = pattern.sub("", updated)
        # Clean doubled delimiters / empty table cells debris
        updated = re.sub(r"\s*,\s*,", ",", updated)
        updated = re.sub(r"\|[ \t]*\|", "| |", updated)
        updated = re.sub(r"  +", " ", updated)
        if updated != before:
            logs.append(f"removed fabricated cert ({code})")

    for pattern, code in _AGENCY_OVERCLAIM_RES:
        if not pattern.search(updated):
            continue
        if code == "sba_status_overclaim":
            replacement = (
                "zö agency holds verified WBENC and WOSB certifications "
                "(agency-level — do not claim separate SBA status confirmation)"
            )
        else:
            replacement = (
                "Named team members hold individual platform certifications "
                "(Google Ads / Meta) where listed in their bios"
            )
        updated2, n = pattern.subn(replacement, updated, count=3)
        if n:
            updated = updated2
            logs.append(f"rewrote agency-overclaim cert ({code})")

    for pattern, code in _DIVERSITY_FABRICATED_RES:
        if not pattern.search(updated):
            continue
        before = updated
        updated = pattern.sub("", updated)
        if updated != before:
            logs.append(f"removed fabricated diversity cert ({code})")

    if _STANDALONE_WBE_RE.search(updated):
        before = updated
        updated = _STANDALONE_WBE_RE.sub("", updated)
        if updated != before:
            logs.append("removed standalone WBE designation")

    for pattern, code in _UNVERIFIED_MARKETING_CERT_RES:
        if not pattern.search(updated):
            continue
        before = updated
        updated = pattern.sub("", updated)
        if updated != before:
            logs.append(f"removed unverified marketing cert ({code})")

    if re.search(r"certif", updated, re.I):
        lines_out: list[str] = []
        for line in updated.split("\n"):
            scrubbed_line, line_logs = _scrub_cert_enumeration(line)
            if line_logs:
                logs.extend(line_logs)
                line = scrubbed_line
            lines_out.append(line)
        updated = "\n".join(lines_out)

    updated = _clean_cert_punctuation(updated)

    # Do not strip verified WBENC/WOSB agency certs.
    if updated == body:
        return section, []

    logger.info(
        "cert_claim_scrub section_id=%s changes=%s",
        section.id,
        logs,
    )
    return section.model_copy(update={"content": updated}), logs


def apply_cert_claim_scrub_to_draft(
    draft: ProposalDraft,
    *,
    skip_section_ids: set[str] | None = None,
) -> tuple[ProposalDraft, list[str]]:
    skip = skip_section_ids or set()
    sections: list[ProposalSection] = []
    all_logs: list[str] = []
    changed = False
    for section in draft.sections:
        if section.id in skip:
            sections.append(section)
            continue
        new_sec, logs = scrub_section_cert_claims(section)
        if logs:
            changed = True
            all_logs.append(f"{section.title or section.id}: " + "; ".join(logs))
        sections.append(new_sec)
    if not changed:
        return draft, []
    from datetime import datetime, timezone

    return (
        draft.model_copy(
            update={
                "sections": sections,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        all_logs,
    )


def finding_is_cert_fabrication(finding: Any) -> bool:
    blob = " ".join(
        str(x or "")
        for x in (
            getattr(finding, "code", None),
            getattr(finding, "category", None),
            getattr(finding, "message", None),
        )
    ).casefold()
    markers = (
        "spotify",
        "fabricated_fact",
        "unverified_cert",
        "google ads certification",
        "meta certification",
        "certification not in verified",
        "individual certs, not agency",
    )
    return any(m in blob for m in markers)
