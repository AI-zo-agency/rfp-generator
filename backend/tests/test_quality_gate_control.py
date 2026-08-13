"""Quality gate control logic: dedup, oscillation, revert, and the no-fabrication rule.

These are the parts that must be correct regardless of what any model returns. A loop
with no revert path can only ratchet downward on a bad round; a loop with no dedup
re-opens the same ticket forever; and "don't invent" as a prompt sentence is a request,
not a guarantee.
"""

from __future__ import annotations

from app.models.proposal import ClaimVerdict, GateTicket, ProposalSection
from app.services.proposal_quality_gate import (
    MAX_ROUNDS,
    dedupe_tickets,
    is_regression,
    is_oscillating,
    may_write_claim,
    resolve_contradiction,
)


def _ticket(section_id="s1", code="slop.filler", **kw) -> GateTicket:
    return GateTicket(sectionId=section_id, code=code, **kw)


def _section(content: str, sid: str = "s1") -> ProposalSection:
    return ProposalSection(id=sid, title="T", content=content)


class TestTicketDedup:
    def test_same_section_and_code_is_opened_once(self):
        seen: set[tuple[str, str]] = set()
        first = dedupe_tickets([_ticket()], seen)
        second = dedupe_tickets([_ticket()], seen)
        assert len(first) == 1
        assert second == []

    def test_same_code_in_a_different_section_is_a_new_ticket(self):
        seen: set[tuple[str, str]] = set()
        dedupe_tickets([_ticket(section_id="s1")], seen)
        out = dedupe_tickets([_ticket(section_id="s2")], seen)
        assert len(out) == 1

    def test_different_code_in_same_section_is_a_new_ticket(self):
        seen: set[tuple[str, str]] = set()
        dedupe_tickets([_ticket(code="slop.filler")], seen)
        out = dedupe_tickets([_ticket(code="repetition.restated")], seen)
        assert len(out) == 1

    def test_dedup_is_structural_not_substring_based(self):
        """Codes that merely share a substring are distinct tickets."""
        seen: set[tuple[str, str]] = set()
        dedupe_tickets([_ticket(code="staffing_hours")], seen)
        out = dedupe_tickets([_ticket(code="staffing_hours_detail")], seen)
        assert len(out) == 1

    def test_batch_with_internal_duplicates_collapses(self):
        seen: set[tuple[str, str]] = set()
        out = dedupe_tickets([_ticket(), _ticket(), _ticket()], seen)
        assert len(out) == 1


class TestRegressionGuard:
    def test_substantial_shortening_is_a_regression(self):
        assert is_regression(before="word " * 100, after="word " * 40)

    def test_normal_tightening_is_not_a_regression(self):
        assert not is_regression(before="word " * 100, after="word " * 92)

    def test_emptying_a_section_is_a_regression(self):
        assert is_regression(before="real content here", after="")

    def test_losing_a_manual_fill_tag_is_a_regression(self):
        """A repair must never quietly drop an unfilled submission gap."""
        before = "Bond amount: [MANUAL FILL: bid bond]"
        after = "Bond amount: to be confirmed."
        assert is_regression(before=before, after=after)

    def test_keeping_the_tag_is_fine(self):
        before = "Bond amount: [MANUAL FILL: bid bond]"
        after = "The bond amount is [MANUAL FILL: bid bond], per RFP 4.2."
        assert not is_regression(before=before, after=after)

    def test_growth_is_never_a_regression(self):
        assert not is_regression(before="short", after="short but rather longer now")


class TestOscillation:
    def test_reverting_to_an_earlier_text_is_oscillation(self):
        history = ["version A", "version B"]
        assert is_oscillating(history=history, candidate="version A")

    def test_a_genuinely_new_version_is_not_oscillation(self):
        history = ["version A", "version B"]
        assert not is_oscillating(history=history, candidate="version C")

    def test_whitespace_only_differences_still_count_as_reverting(self):
        history = ["version   A"]
        assert is_oscillating(history=history, candidate="version A")

    def test_empty_history_never_oscillates(self):
        assert not is_oscillating(history=[], candidate="anything")


class TestNoFabricationRule:
    def test_fact_bound_fix_without_evidence_is_forbidden(self):
        """Mechanical precondition, not a prompt instruction."""
        assert not may_write_claim(requires_evidence=True, evidence="")

    def test_fact_bound_fix_with_evidence_is_allowed(self):
        assert may_write_claim(requires_evidence=True, evidence="Acme paid $4,200.")

    def test_style_only_fix_needs_no_evidence(self):
        assert may_write_claim(requires_evidence=False, evidence="")

    def test_whitespace_evidence_does_not_count(self):
        assert not may_write_claim(requires_evidence=True, evidence="   \n ")


class TestContradictionResolution:
    def test_kb_supported_value_wins(self):
        out = resolve_contradiction(
            values=["12 staff", "15 staff"],
            verdict=ClaimVerdict(
                sectionId="s1", claim="headcount", status="contradicted",
                correctedValue="15 staff", evidence="Roster lists 15.",
            ),
        )
        assert out.winner == "15 staff"
        assert not out.manual_fill

    def test_silent_kb_sends_both_to_manual_fill(self):
        """Guessing is how a confident wrong number reaches an evaluator."""
        out = resolve_contradiction(
            values=["12 staff", "15 staff"],
            verdict=ClaimVerdict(sectionId="s1", claim="headcount", status="unresolved"),
        )
        assert out.winner is None
        assert out.manual_fill

    def test_no_verdict_at_all_sends_both_to_manual_fill(self):
        out = resolve_contradiction(values=["12", "15"], verdict=None)
        assert out.manual_fill

    def test_never_picks_the_first_value_by_default(self):
        out = resolve_contradiction(values=["12", "15"], verdict=None)
        assert out.winner is None


class TestRoundBudget:
    def test_max_rounds_is_three(self):
        assert MAX_ROUNDS == 3
