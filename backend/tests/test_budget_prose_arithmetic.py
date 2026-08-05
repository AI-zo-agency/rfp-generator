"""The rendered budget markdown must be internally consistent.

Observed: "$3,500 professional services fee", "$3,500 travel reimbursables",
"$3,500 total ($3,500 in direct travel expenses)" all shipped together.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import (
    BudgetLineItem,
    ProposalBudget,
    ProposalDraft,
    ProposalSection,
)
from app.services.llm import LlmError
from app.services.proposal_budget_content import render_budget_markdown
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


_PARENTHETICAL_NOTE = "equals its own parenthetical breakdown"


class SingleComponentBudgetTests(unittest.TestCase):
    """A one-component budget legitimately restates the total in its parenthetical.

    render_budget_markdown supports fee-only, travel-only and pass-through-only
    shapes. For all three, _rewrite_investment_sentence emits
    "Total proposed investment: $X ($X in <the one component>)." — whole == part,
    but the arithmetic is correct because there is nothing else to add. Flagging
    it is a false positive that never self-resolves: rerender_budget_section_from_canon
    regenerates identical text from the same canonical budget on every retry, so
    it lands as a spurious manual-fill handoff on a correct proposal.
    """

    def test_fee_only_budget_is_not_flagged(self) -> None:
        text = (
            "## Proposed Investment\n\n"
            "**Professional fees: $60,000**\n"
            "**Total proposed investment: $60,000**\n\n"
            "Total proposed investment: $60,000 ($60,000 in professional fees)."
        )
        self.assertEqual(collect_prose_arithmetic_violations(text), [])

    def test_travel_only_budget_is_not_flagged(self) -> None:
        text = (
            "## Proposed Investment\n\n"
            "**Direct travel / reimbursables: $7,500**\n"
            "**Total proposed investment: $7,500**\n\n"
            "Total proposed investment: $7,500 ($7,500 in direct travel expenses)."
        )
        self.assertEqual(collect_prose_arithmetic_violations(text), [])

    def test_passthrough_only_budget_is_not_flagged(self) -> None:
        text = (
            "## Proposed Investment\n\n"
            "**Client media pass-through (net): $12,000**\n"
            "**Total proposed investment: $12,000**\n\n"
            "Total proposed investment: $12,000 "
            "($12,000 in client media pass-through at net)."
        )
        self.assertEqual(collect_prose_arithmetic_violations(text), [])

    def test_two_components_with_swallowing_parenthetical_still_flagged(self) -> None:
        """The genuine defect must survive the single-component carve-out."""
        text = (
            "## Proposed Investment\n\n"
            "**Professional fees: $3,500**\n"
            "**Direct travel / reimbursables: $3,500**\n"
            "**Total proposed investment: $3,500**\n\n"
            "Total proposed investment: $3,500 ($3,500 in direct travel expenses)."
        )
        violations = collect_prose_arithmetic_violations(text)
        self.assertTrue(
            any(_PARENTHETICAL_NOTE in v for v in violations),
            f"parenthetical guard must still fire, got: {violations}",
        )

    def test_embedded_table_label_style_is_parsed(self) -> None:
        """render_embedded_budget_table_markdown puts the colon inside the bold.

        "**Professional fees:** $60,000" matched none of the label patterns, so a
        fee-only budget rendered through that path parsed as zero components —
        which would defeat the component-count carve-out below.
        """
        text = (
            "### Proposed Investment\n\n"
            "**Professional fees:** $60,000\n"
            "**Total proposed investment:** $60,000\n\n"
            "Total proposed investment: $60,000 ($60,000 in professional fees)."
        )
        self.assertEqual(collect_prose_arithmetic_violations(text), [])

    def test_embedded_table_label_style_still_catches_mismatch(self) -> None:
        """Parsing that style must also make its arithmetic enforceable."""
        text = (
            "**Professional fees:** $10,000\n"
            "**Direct travel / reimbursables:** $1,000\n"
            "**Total proposed investment:** $99,000"
        )
        self.assertTrue(collect_prose_arithmetic_violations(text))


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

    async def test_real_fee_only_render_is_not_flagged_end_to_end(self) -> None:
        """C1 regression: drive the REAL renderer, not a hand-written fixture.

        A correct fee-only budget ($60,000 fee, no travel, no pass-through)
        renders, via _rewrite_investment_sentence, as
        "Total proposed investment: $60,000 ($60,000 in professional fees)."
        The first version of the parenthetical guard flagged that as a violation.
        """
        fee_only = ProposalBudget(
            rfpId="rfp-e2e",
            updatedAt="2026-08-05T00:00:00Z",
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    category="Strategy",
                    description="Phase 2 Strategy — brand platform",
                    extended=60_000.0,
                    lineItemType="agency_fee",
                )
            ],
            scopeSummary="zo will deliver a full brand platform.",
        )
        rendered = render_budget_markdown(fee_only)
        # Guard the premise: if the renderer stops emitting the self-referential
        # parenthetical, this test would pass vacuously.
        self.assertIn("($60,000 in professional fees)", rendered)

        draft = _draft(budget_section_content=rendered)
        with patch(
            "app.services.proposal_budget_sync.llm.chat_json",
            new=AsyncMock(side_effect=LlmError("no llm in test")),
        ):
            mismatches = await run_budget_grounding_check(
                rfp_id="rfp-e2e", draft=draft, budget=fee_only
            )
        self.assertEqual(
            mismatches, [], f"correct fee-only budget must not be flagged: {mismatches}"
        )

    async def test_reconciled_three_component_budget_is_not_flagged(self) -> None:
        """Pass-through is additive to the rendered total — confirm on real canon.

        The sum check adds fee + direct + pass-through. That is only correct if
        the renderer's total includes pass-through. Reconcile + render a real
        fee + travel + media budget and confirm it does ($60k + $7.5k + $12k =
        $79.5k), so the formula cannot become a standing false positive on every
        budget that carries media.
        """
        from app.services.proposal_budget_validation import reconcile_proposal_budget

        budget = reconcile_proposal_budget(
            ProposalBudget(
                rfpId="rfp-e2e",
                updatedAt="2026-08-05T00:00:00Z",
                lineItems=[
                    BudgetLineItem(
                        id="L1",
                        category="Strategy",
                        description="Phase 2 Strategy — brand platform",
                        extended=60_000.0,
                        lineItemType="agency_fee",
                    ),
                    BudgetLineItem(
                        id="L2",
                        category="Media",
                        description="Paid media placement (client pass-through at net)",
                        extended=12_000.0,
                        lineItemType="client_passthrough",
                    ),
                ],
                directExpensesTotal=7_500.0,
                scopeSummary="Full program.",
            )
        )
        rendered = render_budget_markdown(budget)
        self.assertIn("$79,500", rendered)
        self.assertEqual(collect_prose_arithmetic_violations(rendered), [])

        draft = _draft(budget_section_content=rendered)
        with patch(
            "app.services.proposal_budget_sync.llm.chat_json",
            new=AsyncMock(side_effect=LlmError("no llm in test")),
        ):
            mismatches = await run_budget_grounding_check(
                rfp_id="rfp-e2e", draft=draft, budget=budget
            )
        # Asserted on prose_arithmetic only, deliberately. This budget also trips
        # the PRE-EXISTING media_passthrough check, because _sync_narrative_total
        # (proposal_budget_content.py:924) rewrites every dollar in the option-term
        # notes to the grand total while protecting only `direct` — so the $60,000
        # fee and $12,000 pass-through both render as $79,500. That is a separate,
        # pre-existing renderer defect, not this check's business, and asserting
        # []-overall here would mean either hiding it or fixing it out of scope.
        self.assertEqual(
            [m for m in mismatches if m.claimed_field == "prose_arithmetic"],
            [],
            f"correct media budget flagged by prose arithmetic: {mismatches}",
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
