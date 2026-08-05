"""The rendered budget markdown must be internally consistent.

Observed: "$3,500 professional services fee", "$3,500 travel reimbursables",
"$3,500 total ($3,500 in direct travel expenses)" all shipped together.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalBudget, ProposalDraft, ProposalSection
from app.services.llm import LlmError
from app.services.proposal_budget_sync import (
    collect_prose_arithmetic_violations,
    run_budget_grounding_check,
)

TRIPLET = """## Proposed Investment

**Professional fees: $3,500**
**Direct travel / reimbursables: $3,500**
**Total proposed investment: $3,500**
"""

HEALTHY = """## Proposed Investment

**Professional fees: $60,000**
**Direct travel / reimbursables: $7,500**
**Total proposed investment: $67,500**
"""


class ProseArithmeticTests(unittest.TestCase):
    def test_the_observed_triplet_is_rejected(self) -> None:
        violations = collect_prose_arithmetic_violations(TRIPLET)
        self.assertTrue(violations)
        self.assertIn("3,500", violations[0])

    def test_a_consistent_budget_passes(self) -> None:
        self.assertEqual(collect_prose_arithmetic_violations(HEALTHY), [])

    def test_total_equal_to_its_own_parenthetical_is_rejected(self) -> None:
        text = "Total proposed investment: $3,500 ($3,500 in direct travel expenses)."
        self.assertTrue(collect_prose_arithmetic_violations(text))

    def test_professional_fees_label_is_recognised(self) -> None:
        """The label render_budget_markdown prints had no detector at all."""
        text = "**Professional fees: $10,000**\n**Direct travel / reimbursables: $1,000**\n**Total proposed investment: $99,000**"
        self.assertTrue(collect_prose_arithmetic_violations(text))

    def test_missing_labels_are_not_a_violation(self) -> None:
        """Absence of a total is a different defect; do not report it here."""
        self.assertEqual(collect_prose_arithmetic_violations("**Professional fees: $10,000**"), [])

    def test_tolerance_allows_rounding(self) -> None:
        text = "**Professional fees: $60,000**\n**Direct travel / reimbursables: $7,500**\n**Total proposed investment: $67,501**"
        self.assertEqual(collect_prose_arithmetic_violations(text), [])


def _budget(**overrides) -> ProposalBudget:
    fields = {
        "rfpId": "rfp-e2e",
        "updatedAt": "2026-08-05T00:00:00Z",
        "agencyFeeSubtotal": 3500.0,
        "directExpensesTotal": 3500.0,
        "totalClientInvoicing": 3500.0,
    }
    fields.update(overrides)
    return ProposalBudget(**fields)


def _draft(*, budget_section_content: str) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-e2e",
        updatedAt="2026-08-05T00:00:00Z",
        sections=[
            ProposalSection(
                id="sec-budget",
                title="Budget",
                content=budget_section_content,
            ),
        ],
    )


class GroundingCheckPipelineTests(unittest.IsolatedAsyncioTestCase):
    """Drive the real run_budget_grounding_check pipeline, not just the helper.

    Task 1's lesson: a green unit test for the helper function proves nothing
    about the motivating defect unless it also runs through the entry point
    that production code actually calls. The LLM boundary is mocked to raise
    LlmError (its documented, already-handled failure mode) so these run
    offline and exercise the real deterministic + prose-arithmetic wiring.
    """

    async def test_the_observed_defect_is_rejected_end_to_end(self) -> None:
        """Professional fees == Direct travel == Total, all $3,500.

        collect_deterministic_budget_mismatches has no pattern for "Professional
        fees" at all (only "Agency fee/revenue"), so before this task the old
        deterministic check produced zero findings for this manuscript no matter
        what the canonical budget said. The new prose-arithmetic check must
        catch it directly from the rendered section text.
        """
        draft = _draft(budget_section_content=TRIPLET)
        budget = _budget()
        with patch(
            "app.services.proposal_budget_sync.llm.chat_json",
            new=AsyncMock(side_effect=LlmError("no llm in test")),
        ):
            mismatches = await run_budget_grounding_check(
                rfp_id="rfp-e2e", draft=draft, budget=budget
            )
        self.assertTrue(
            any("3,500" in (m.sentence or "") or "3,500" in (m.note or "") for m in mismatches),
            f"expected a $3,500 arithmetic mismatch, got: {mismatches}",
        )

    async def test_a_healthy_budget_passes_end_to_end(self) -> None:
        draft = _draft(budget_section_content=HEALTHY)
        budget = _budget(
            agencyFeeSubtotal=60_000.0,
            directExpensesTotal=7_500.0,
            totalClientInvoicing=67_500.0,
        )
        with patch(
            "app.services.proposal_budget_sync.llm.chat_json",
            new=AsyncMock(side_effect=LlmError("no llm in test")),
        ):
            mismatches = await run_budget_grounding_check(
                rfp_id="rfp-e2e", draft=draft, budget=budget
            )
        self.assertEqual(mismatches, [])


if __name__ == "__main__":
    unittest.main()
