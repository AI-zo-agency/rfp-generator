from app.models.proposal import AdversarialAuditFinding, ProposalSection
from app.services.proposal_adversarial_repair_verifier import verify_repair_attempt


def test_verify_repair_marks_improved_when_verify_count_drops() -> None:
    finding = AdversarialAuditFinding(
        severity="critical",
        category="other",
        code="deterministic.coverage.empty_section",
        message="Section is empty.",
        sectionId="section-21",
        sectionTitle="Technical Approach",
        source="deterministic",
    )
    before = ProposalSection(
        id="section-21",
        title="Technical Approach",
        content="[VERIFY: Draft content for Technical Approach]",
    )
    after = ProposalSection(
        id="section-21",
        title="Technical Approach",
        content="We will deliver the work in four phases with monthly QA checkpoints.",
    )
    result = verify_repair_attempt(
        finding=finding,
        before=before,
        after=after,
        new_findings=[],
    )
    assert result.improved is True
    assert result.resolved is True
    assert result.verify_count_delta < 0
    assert result.outcome in {"resolved", "improved_but_unresolved"}


def test_verify_repair_no_change_when_same_text() -> None:
    finding = AdversarialAuditFinding(
        severity="critical",
        category="fabrication",
        code="llm.fabrication.scope",
        message="Unsupported scope.",
        sectionId="section-20",
        sectionTitle="Portfolio",
        source="llm",
    )
    section = ProposalSection(
        id="section-20",
        title="Portfolio",
        content="Invented project description.",
    )
    remaining = AdversarialAuditFinding(
        severity="critical",
        category="fabrication",
        code="llm.fabrication.scope",
        message="Unsupported scope.",
        sectionId="section-20",
        sectionTitle="Portfolio",
        source="llm",
    )
    result = verify_repair_attempt(
        finding=finding,
        before=section,
        after=section,
        new_findings=[remaining],
    )
    assert result.improved is False
    assert result.resolved is False
    assert result.outcome == "no_change"


def test_verify_repair_emptiness_secondary_rule_resolves_when_stale_code_lingers() -> None:
    """If the auditor re-emits the *same* empty-section code even though the section now
    has substantial content (e.g. a stale check), the secondary emptiness rule can still
    mark it resolved as long as no *other* critical finding remains for that section."""
    finding = AdversarialAuditFinding(
        severity="critical",
        category="other",
        code="deterministic.coverage.empty_section",
        message="Section is empty.",
        sectionId="section-21",
        sectionTitle="Technical Approach",
        source="deterministic",
    )
    before = ProposalSection(id="section-21", title="Technical Approach", content="")
    after = ProposalSection(
        id="section-21",
        title="Technical Approach",
        content="We will deliver the work in four phases with monthly QA checkpoints.",
    )
    stale_same_code = AdversarialAuditFinding(
        severity="critical",
        category="other",
        code="deterministic.coverage.empty_section",
        message="Section is empty.",
        sectionId="section-21",
        sectionTitle="Technical Approach",
        source="deterministic",
    )
    result = verify_repair_attempt(
        finding=finding,
        before=before,
        after=after,
        new_findings=[stale_same_code],
    )
    assert result.resolved is True


def test_verify_repair_emptiness_secondary_rule_blocked_by_other_remaining_critical() -> None:
    """The secondary emptiness rule must not mark resolved when a *different* critical
    finding remains for the same section — that critical still needs a repair pass,
    even though the emptiness code itself is stale."""
    finding = AdversarialAuditFinding(
        severity="critical",
        category="other",
        code="deterministic.coverage.empty_section",
        message="Section is empty.",
        sectionId="section-21",
        sectionTitle="Technical Approach",
        source="deterministic",
    )
    before = ProposalSection(id="section-21", title="Technical Approach", content="")
    after = ProposalSection(
        id="section-21",
        title="Technical Approach",
        content="We invented a $2M contract win for Client X.",
    )
    stale_same_code = AdversarialAuditFinding(
        severity="critical",
        category="other",
        code="deterministic.coverage.empty_section",
        message="Section is empty.",
        sectionId="section-21",
        sectionTitle="Technical Approach",
        source="deterministic",
    )
    other_critical = AdversarialAuditFinding(
        severity="critical",
        category="fabrication",
        code="llm.fabrication.invented_metric",
        message="Invented contract value.",
        sectionId="section-21",
        sectionTitle="Technical Approach",
        source="llm",
    )
    result = verify_repair_attempt(
        finding=finding,
        before=before,
        after=after,
        new_findings=[stale_same_code, other_critical],
    )
    assert result.resolved is False


def test_verify_repair_detects_introduced_critical() -> None:
    finding = AdversarialAuditFinding(
        severity="critical",
        category="other",
        code="deterministic.coverage.empty_section",
        message="Section is empty.",
        sectionId="section-21",
        sectionTitle="Technical Approach",
        source="deterministic",
    )
    before = ProposalSection(id="section-21", title="Technical Approach", content="")
    after = ProposalSection(
        id="section-21",
        title="Technical Approach",
        content="We invented a $2M contract win for Client X.",
    )
    new_critical = AdversarialAuditFinding(
        severity="critical",
        category="fabrication",
        code="llm.fabrication.invented_metric",
        message="Invented contract value.",
        sectionId="section-21",
        sectionTitle="Technical Approach",
        source="llm",
    )
    result = verify_repair_attempt(
        finding=finding,
        before=before,
        after=after,
        new_findings=[new_critical],
    )
    assert result.introduced_critical is True
