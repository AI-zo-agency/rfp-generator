"""Post-adjudication scrub for Go/No-Go capability evidence.

Quote grounding stops inventing *whole* sentences when the KB is clean. It does
not stop:
  * known fabricated cert strings that already appear in polluted KB chunks, or
  * salvage picking a higher-ed sentence from a combined case-study dump when the
    model paraphrased Rock the Locks + enrollment language together.

This module is mechanical denylist + rewrite. It never invents new credentials.
Verified agency certs remain WBENC / WOSB only (see companyfacts).
"""

from __future__ import annotations

import logging
import re

from app.models.go_no_go import GoNoGoCapabilityRow

logger = logging.getLogger(__name__)

# Agency-verified only. B Corp / 1% Planet / LinkedIn Gold are repeat fabrications.
_FABRICATED_CERT_RES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"1\s*%\s*for\s*the\s*Planet(?:\s+membership)?", re.I), "one_percent_planet"),
    (re.compile(r"LinkedIn\s+Gold(?:[- ]Certified)?(?:\s+status)?", re.I), "linkedin_gold"),
    (
        re.compile(
            r"\bB[\s-]?Corp(?:oration)?(?:\s+certification)?\b|"
            r"\bB[\s-]?Corporate\b",
            re.I,
        ),
        "b_corp",
    ),
]

_CANONICAL_WBENC_WOSB = (
    "zö agency holds WBENC (Women's Business Enterprise National Council) and "
    "WOSB (Women-Owned Small Business, SBA) certifications, both valid through "
    "April 30, 2027."
)

# Higher-ed enrollment language wrongly stitched onto festival / campaign studies.
_FOREIGN_METRIC_RES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?:,\s*)?(?:and\s+)?accelerated\s+early\s+admissions?\b",
            re.I,
        ),
        "early_admissions",
    ),
    (
        re.compile(
            r"(?:,\s*)?(?:and\s+)?(?:boosted|increased|grew)\s+"
            r"(?:early\s+)?(?:admissions?|enrollment)\b",
            re.I,
        ),
        "enrollment_metric",
    ),
]

_CERT_REQUIREMENT_RE = re.compile(
    r"(?i)\b(?:certif|WBENC|WOSB|WBE|MBE|DBE|MWESB|women[\s-]?owned|"
    r"minority|veteran[\s-]?owned|emerging\s+small)\b"
)

_LIST_GLUE_RE = re.compile(
    r"(?i)\s*,\s*and\s*,|\s*,\s*,+|\s+and\s+and\s+|"
    r"certified\s+as\s+,|certified\s+as\s+and\b|"
    r",\s*and\s*\.|,\s*\."
)


def evidence_has_fabricated_certs(text: str) -> bool:
    return any(p.search(text or "") for p, _ in _FABRICATED_CERT_RES)


def scrub_evidence_text(text: str) -> tuple[str, list[str]]:
    """Strip known fabrications from an evidence string. Returns (text, log codes)."""
    updated = text or ""
    logs: list[str] = []
    for pattern, code in _FABRICATED_CERT_RES:
        if not pattern.search(updated):
            continue
        updated = pattern.sub("", updated)
        logs.append(code)
    for pattern, code in _FOREIGN_METRIC_RES:
        if not pattern.search(updated):
            continue
        updated = pattern.sub("", updated)
        logs.append(code)
    updated = _LIST_GLUE_RE.sub(", ", updated)
    updated = re.sub(r"\s{2,}", " ", updated)
    updated = re.sub(r"\s+,", ",", updated)
    updated = re.sub(r",\s*and\s*$", "", updated.strip(" ,;"))
    updated = updated.strip(" ,;")
    # "certified as WBENC." after dropping three invented certs
    updated = re.sub(
        r"(?i)\bcertified\s+as\s+(WBENC)\s*$",
        r"holds \1 certification",
        updated,
    )
    return updated, logs


def _is_cert_requirement(requirement: str) -> bool:
    return bool(_CERT_REQUIREMENT_RE.search(requirement or ""))


def scrub_capability_row(row: GoNoGoCapabilityRow) -> GoNoGoCapabilityRow:
    """Scrub one capability row; rewrite cert rows that listed invented badges."""
    if row.status not in {"verified", "partial"}:
        return row
    evidence = (row.evidence or "").strip()
    if not evidence:
        return row

    scrubbed, logs = scrub_evidence_text(evidence)

    # MWESB / women-owned certification ask: prefer WBENC+WOSB statement over
    # ownership-% alone (ownership is real but incomplete vs companyfacts certs).
    if _is_cert_requirement(row.requirement) and not re.search(
        r"(?i)\b(?:WBENC|WOSB)\b", evidence
    ):
        if re.search(
            r"(?i)(?:ownership|sole\s+owner|woman[\s-]?owned|women[\s-]?owned)",
            evidence,
        ):
            logger.info(
                "go_no_go evidence scrub requirement=%r ownership→WBENC/WOSB",
                (row.requirement or "")[:80],
            )
            return row.model_copy(
                update={
                    "status": "verified",
                    "evidence": _CANONICAL_WBENC_WOSB,
                    "downgrade_reason": "",
                }
            )

    if not logs:
        return row

    logger.info(
        "go_no_go evidence scrub requirement=%r removed=%s",
        (row.requirement or "")[:80],
        ",".join(logs),
    )

    had_fabricated_certs = any(
        code in {"one_percent_planet", "linkedin_gold", "b_corp"} for code in logs
    )
    mentions_verified = bool(
        re.search(r"(?i)\b(?:WBENC|WOSB)\b", evidence)
        or re.search(r"(?i)\b(?:WBENC|WOSB)\b", scrubbed)
    )

    if had_fabricated_certs and (
        _is_cert_requirement(row.requirement) or mentions_verified
    ):
        # Prefer the grounded agency facts — never leave invented badges as Strong.
        return row.model_copy(
            update={
                "evidence": _CANONICAL_WBENC_WOSB,
                "downgrade_reason": "",
            }
        )

    if not scrubbed or len(scrubbed) < 12:
        return row.model_copy(
            update={
                "status": "gap",
                "evidence": "",
                "kb_source": "",
                "downgrade_reason": (
                    "evidence contained fabricated claims that were removed; "
                    "no remaining grounded span"
                ),
            }
        )

    return row.model_copy(update={"evidence": scrubbed[:400]})


def scrub_capability_rows(
    rows: list[GoNoGoCapabilityRow],
) -> list[GoNoGoCapabilityRow]:
    return [scrub_capability_row(row) for row in rows]
