"""Budget section targeting + Ralph protection + static-skip exemptions."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalBudget, ProposalDraft, ProposalSection
from app.services.proposal_budget_content import (
    budget_section_score,
    fill_hollow_pricing_stubs_from_canon_budget,
    find_budget_section_index,
)
from app.services.proposal_ralph import apply_ralph_to_draft
from app.services.proposal_voice_enforcement import (
    should_skip_rfp_section_as_static_duplicate,
)


def _sec(sid: str, title: str, content: str = "x", *, word_target: int | None = None) -> ProposalSection:
    kwargs: dict = {"id": sid, "title": title, "content": content}
    if word_target is not None:
        kwargs["wordTarget"] = word_target
    return ProposalSection(**kwargs)


class BudgetSectionTargetingTests(unittest.TestCase):
    def test_cost_proposal_beats_incidental_budgets_in_sow_title(self) -> None:
        sections = [
            _sec("sow", "Technical Approach — Timelines, Budgets, Reporting"),
            _sec("cost", "Cost Proposal"),
        ]
        idx = find_budget_section_index(sections)
        self.assertEqual(idx, 1)
        self.assertGreater(
            budget_section_score("Cost Proposal"),
            budget_section_score("Technical Approach — Timelines, Budgets, Reporting"),
        )

    def test_fee_schedule_and_budget_pricing_score_high(self) -> None:
        self.assertGreaterEqual(budget_section_score("Fee Schedule"), 6)
        self.assertGreaterEqual(budget_section_score("Budget & Pricing"), 6)
        self.assertGreaterEqual(budget_section_score("Cost of the Base Bid"), 6)

    def test_incidental_budgets_list_scores_low_or_zero(self) -> None:
        self.assertLessEqual(
            budget_section_score("Budgets, Timelines, and Reporting Requirements"),
            2,
        )

    def test_hollow_rfp_fee_schedule_stub_beats_zo_budget_pricing(self) -> None:
        """Strict RFP Rate/Fee Schedule stub must receive Budget phase write."""
        stub_body = (
            "## PROPOSAL RATE/FEE SCHEDULE\n\n"
            "[MANUAL FILL: Draft this RFP-required section — PROPOSAL RATE/FEE SCHEDULE]\n\n"
            "RFP instructions: Required in this RFP's submission sequence."
        )
        sections = [
            _sec("bp", "Budget & Pricing", "Zo template fee shell"),
            _sec("fee", "PROPOSAL RATE/FEE SCHEDULE", stub_body),
        ]
        self.assertEqual(find_budget_section_index(sections), 1)


class BudgetStaticSkipTests(unittest.TestCase):
    def test_cost_proposal_never_skipped_even_as_static_duplicate(self) -> None:
        self.assertFalse(
            should_skip_rfp_section_as_static_duplicate(
                title="Cost Proposal",
                duplicate_of_static_section="section-1",
                evaluation_weight=0,
            )
        )

    def test_budget_pricing_never_skipped(self) -> None:
        self.assertFalse(
            should_skip_rfp_section_as_static_duplicate(
                title="Budget & Pricing",
                duplicate_of_static_section="section-3",
            )
        )


class RalphBudgetExemptTests(unittest.TestCase):
    def test_ralph_does_not_soft_trim_budget_fee_table(self) -> None:
        # Narrative + trailing fee table; overshoots wordTarget×1.25.
        fee_table = (
            "## Fee Detail by Phase\n\n"
            "| Role | Hours | Rate | Total |\n|---|---|---|---|\n"
            + "\n".join(
                f"| Strategist | {i} | $150 | ${i * 150} |" for i in range(1, 40)
            )
        )
        body = ("Transparency and pass-through media. " * 20) + "\n\n" + fee_table
        draft = ProposalDraft(
            rfpId="rfp-budget-ralph",
            sections=[_sec("budget", "Budget & Pricing", body, word_target=80)],
            updatedAt="2026-01-01T00:00:00Z",
        )
        updated, logs = apply_ralph_to_draft(draft, page_limit=12)
        self.assertIn("Fee Detail by Phase", updated.sections[0].content or "")
        self.assertFalse(any("trim-overshoot" in x for x in logs))


if __name__ == "__main__":
    unittest.main()


class HollowPricingStubFillTests(unittest.TestCase):
    def test_senior_editor_late_fee_schedule_stub_gets_canon_budget(self) -> None:
        """Reproduce staging: Budget green, then Rate/Fee stub minted empty."""
        bp = _sec(
            "bp",
            "Budget & Pricing",
            "## Proposed Investment\n\n"
            "| Phase | Fee |\n|---|---|\n"
            "| Discovery | $12,000 |\n| Build | $40,000 |\n\n"
            "Total proposed investment: $52,000. " * 3,
        )
        stub = _sec(
            "fee",
            "PROPOSAL RATE/FEE SCHEDULE",
            "## PROPOSAL RATE/FEE SCHEDULE\n\n"
            "[MANUAL FILL: Draft this RFP-required section — PROPOSAL RATE/FEE SCHEDULE]\n\n"
            "RFP instructions: Required.",
        )
        draft = ProposalDraft(rfpId="r1", sections=[bp, stub], updatedAt="t")
        budget = ProposalBudget(
            rfpId="r1", updatedAt="t", budgetFormat="phased", lumpSumTotal=52000
        )
        updated, logs = fill_hollow_pricing_stubs_from_canon_budget(draft, budget)
        fee = next(s for s in updated.sections if s.id == "fee")
        self.assertTrue(logs)
        self.assertNotIn("Draft this RFP-required section", fee.content or "")
        self.assertIn("$", fee.content or "")


class WorkersCompNotBudgetTests(unittest.TestCase):
    def test_workers_comp_certificate_score_zero(self) -> None:
        self.assertEqual(budget_section_score("WORKER'S COMPENSATION CERTIFICATE"), 0)
        self.assertEqual(budget_section_score("Workers Compensation Insurance"), 0)

    def test_improve_workers_comp_does_not_collapse_fee_schedule(self) -> None:
        from app.services.proposal_budget_content import collapse_duplicate_cost_proposal_tabs
        from app.services.proposal_budget_playbook import should_apply_budget_playbook

        wc = _sec(
            "wc",
            "WORKER'S COMPENSATION CERTIFICATE",
            "[MANUAL FILL: Draft this RFP-required section — WORKER'S COMPENSATION CERTIFICATE]",
        )
        fee = _sec(
            "fee",
            "PROPOSAL RATE/FEE SCHEDULE",
            "## Fees\n\n| Phase | Amount |\n|---|---|\n| A | $10,000 |\n" * 3,
        )
        self.assertFalse(
            should_apply_budget_playbook(wc, "Improve this section for the RFP.")
        )
        collapsed, logs = collapse_duplicate_cost_proposal_tabs([wc, fee])
        self.assertFalse(logs)
        self.assertEqual(len(collapsed), 2)
        self.assertEqual(find_budget_section_index([wc, fee]), 1)
