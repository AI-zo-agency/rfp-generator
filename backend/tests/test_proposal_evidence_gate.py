"""Tests for shared Evidence Decision Gate (KB vs write)."""

from app.models.proposal import AdversarialAuditFinding
from app.services.proposal_evidence_gate import (
    EvidenceDecision,
    decide_evidence_action,
)


def test_fact_bound_finding_retrieves() -> None:
    finding = AdversarialAuditFinding(
        severity="critical",
        category="fabrication",
        code="llm.fabrication.scope",
        message="Unsupported client metric",
        sectionId="rfp-sec-2",
        sectionTitle="Qualifications & Relevant Experience",
        source="llm",
    )
    decision = decide_evidence_action(
        section_id="rfp-sec-2",
        section_title="Qualifications & Relevant Experience",
        finding=finding,
    )
    assert decision.action == EvidenceDecision.RETRIEVE_THEN_WRITE
    assert decision.requires_retrieval is True


def test_methodology_writes_from_plan() -> None:
    finding = AdversarialAuditFinding(
        severity="critical",
        category="coverage",
        code="deterministic.coverage.empty_technical_approach",
        message="Section is empty",
        sectionId="rfp-sec-1",
        sectionTitle="Technical Approach & Methodology",
        source="deterministic",
    )
    decision = decide_evidence_action(
        section_id="rfp-sec-1",
        section_title="Technical Approach & Methodology",
        finding=finding,
    )
    assert decision.action == EvidenceDecision.WRITE_FROM_PLAN
    assert decision.safe_plan_driven is True


def test_money_finding_uses_canonical_budget() -> None:
    finding = AdversarialAuditFinding(
        severity="critical",
        category="budget",
        code="deterministic.budget.mismatch",
        message="Fee contradicts ledger",
        sectionId="rfp-sec-6",
        sectionTitle="Cost / Price",
        source="deterministic",
    )
    decision = decide_evidence_action(
        section_id="rfp-sec-6",
        section_title="Cost / Price",
        finding=finding,
    )
    assert decision.action == EvidenceDecision.WRITE_FROM_CANONICAL_BUDGET


def test_legal_attestation_prefers_manual_fill() -> None:
    finding = AdversarialAuditFinding(
        severity="critical",
        category="compliance",
        code="deterministic.placeholder.unresolved_tag_verify_e_verify",
        message="Unresolved tag: [VERIFY: E-Verify enrollment]",
        sectionId="rfp-sec-3",
        sectionTitle="Project Management Plan & Timeline",
        source="deterministic",
    )
    decision = decide_evidence_action(
        section_id="rfp-sec-3",
        section_title="Project Management Plan & Timeline",
        finding=finding,
    )
    assert decision.action == EvidenceDecision.MANUAL_FILL


def test_truncation_is_deterministic_cleanup() -> None:
    finding = AdversarialAuditFinding(
        severity="critical",
        category="truncation",
        code="t1.truncation.mid_sentence_cutoff",
        message="Section ends mid-sentence",
        sectionId="rfp-sec-1",
        sectionTitle="Technical Approach & Methodology",
        source="deterministic",
    )
    decision = decide_evidence_action(
        section_id="rfp-sec-1",
        section_title="Technical Approach & Methodology",
        finding=finding,
    )
    assert decision.action == EvidenceDecision.DETERMINISTIC_CLEANUP


def test_reference_contact_verify_retrieves() -> None:
    decision = decide_evidence_action(
        section_id="rfp-sec-5",
        section_title="Past Performance & References",
        finding=AdversarialAuditFinding(
            severity="critical",
            category="placeholder",
            code="deterministic.placeholder.unresolved_tag_verify_reference_contact",
            message="Unresolved tag: [VERIFY: reference contact, name, title, email, phone]",
            sectionId="rfp-sec-5",
            sectionTitle="Past Performance & References",
            source="deterministic",
        ),
    )
    assert decision.action == EvidenceDecision.RETRIEVE_THEN_WRITE


def test_drafting_without_finding_uses_section_title() -> None:
    decision = decide_evidence_action(
        section_id="rfp-sec-1",
        section_title="Technical Approach & Methodology",
        finding=None,
    )
    assert decision.action == EvidenceDecision.WRITE_FROM_PLAN
