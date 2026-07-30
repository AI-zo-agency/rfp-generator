"""Evidence gate steers drafting JIT retrieval."""

from app.services.proposal_evidence_gate import (
    EvidenceDecision,
    decide_evidence_action,
    evidence_policy_prompt_stanza,
)


def test_methodology_gate_is_plan_driven() -> None:
    d = decide_evidence_action(
        section_id="rfp-sec-1",
        section_title="Technical Approach & Methodology",
    )
    assert d.action == EvidenceDecision.WRITE_FROM_PLAN
    stanza = evidence_policy_prompt_stanza(d, section_id="rfp-sec-1")
    assert "write_from_plan" in stanza
    assert "invent" in stanza.casefold() or "company facts" in stanza.casefold()


def test_cost_gate_is_canonical_budget() -> None:
    d = decide_evidence_action(
        section_id="rfp-sec-6",
        section_title="Cost / Price",
    )
    assert d.action == EvidenceDecision.WRITE_FROM_CANONICAL_BUDGET
