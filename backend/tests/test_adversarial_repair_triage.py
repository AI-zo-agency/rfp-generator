"""Repair triage / cost-control helpers."""

from app.models.proposal import AdversarialAuditFinding
from app.services.proposal_adversarial_repair import (
    _finding_priority,
    _triage_actionable_findings,
)


def test_triage_caps_and_prioritizes_budget_over_llm() -> None:
    findings = [
        AdversarialAuditFinding(
            severity="critical",
            category="inconsistency",
            code="llm.inconsistency.bio",
            message="bio odd",
            sectionId="s1",
            source="llm",
        ),
        AdversarialAuditFinding(
            severity="critical",
            category="consistency",
            code="t5.free_currency",
            message="budget money",
            sectionId="s2",
            source="deterministic",
        ),
        AdversarialAuditFinding(
            severity="critical",
            category="placeholder",
            code="deterministic.placeholder.x",
            message="VERIFY tag",
            sectionId="s3",
            source="deterministic",
        ),
    ]
    assert _finding_priority(findings[1]) < _finding_priority(findings[0])
    triaged = _triage_actionable_findings(findings, max_findings=2)
    assert len(triaged) == 2
    assert triaged[0].code == "t5.free_currency"
    assert triaged[1].code.startswith("deterministic.placeholder")
