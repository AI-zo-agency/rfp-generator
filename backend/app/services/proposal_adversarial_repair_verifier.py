"""Per-finding verification after each adversarial repair attempt."""

from __future__ import annotations

import re

from app.models.proposal import (
    AdversarialAuditFinding,
    ProposalSection,
    RepairVerificationResult,
)

_VERIFY_RE = re.compile(r"\[VERIFY:", re.I)


def verify_repair_attempt(
    *,
    finding: AdversarialAuditFinding,
    before: ProposalSection | None,
    after: ProposalSection | None,
    new_findings: list[AdversarialAuditFinding],
) -> RepairVerificationResult:
    before_text = (before.content if before else "") or ""
    after_text = (after.content if after else "") or ""
    before_verify = len(_VERIFY_RE.findall(before_text))
    after_verify = len(_VERIFY_RE.findall(after_text))

    # Primary rule: resolved only if no finding with the same code+section remains in
    # new_findings. This is the sole source of truth — it must not be short-circuited
    # by an empty `new_findings` list, since callers may (incorrectly) pass a
    # section-filtered or otherwise incomplete findings list where "empty" does not
    # actually mean "nothing left to fix."
    resolved = not any(
        f.code == finding.code and (f.section_id or "") == (finding.section_id or "")
        for f in new_findings
    )

    # Secondary rule: an emptiness-type finding (message/code says the section was
    # required-but-empty) can additionally be marked resolved once the section has
    # substantial content and carries no *other* critical finding for that section —
    # this covers auditors that stop emitting the original "empty" code once content
    # exists but might not re-run in the same verification pass.
    is_emptiness_finding = "empty" in (finding.message or "").casefold() or "empty" in (
        finding.code or ""
    ).casefold()
    if not resolved and is_emptiness_finding and after_text.strip():
        no_other_critical_for_section = not any(
            f.severity == "critical"
            and (f.section_id or "") == (finding.section_id or "")
            and f.code != finding.code
            for f in new_findings
        )
        if no_other_critical_for_section:
            resolved = True

    introduced_critical = any(
        f.severity == "critical"
        and (
            f.code != finding.code
            or (f.section_id or "") != (finding.section_id or "")
        )
        for f in new_findings
    )

    text_changed = after_text.strip() != before_text.strip()
    improved = text_changed and after_verify <= before_verify and bool(after_text.strip())

    if resolved and (improved or bool(after_text.strip())):
        outcome = "resolved"
    elif improved:
        outcome = "improved_but_unresolved"
    else:
        outcome = "no_change"

    return RepairVerificationResult(
        findingCode=finding.code,
        sectionId=finding.section_id,
        resolved=resolved,
        improved=improved,
        verifyCountDelta=after_verify - before_verify,
        introducedCritical=introduced_critical,
        outcome=outcome,
    )
