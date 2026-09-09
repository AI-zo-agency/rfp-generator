"""RFP Cost demands — LLM extract/audit; stubs; fee-table coverage."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_content import (
    ensure_fee_detail_table_in_budget_markdown,
    _rollup_phase_fee_rows,
)
from app.services.rfp_cost_demands import (
    RfpCostDemand,
    apply_grounded_demand_fills,
    apply_missing_demand_stubs,
    ensure_rfp_asks_in_fee_detail_table,
    ensure_rfp_cost_demands_in_budget_markdown,
    format_rfp_cost_demands_for_prompt,
    stub_markdown_for_demand,
    strip_rfp_cost_demand_stub_sections,
)


def _budget(*rows: tuple[str, str, float]) -> ProposalBudget:
    now = datetime.now(timezone.utc).isoformat()
    items = [
        BudgetLineItem(
            id=f"li-{i}",
            category=cat,
            description=desc,
            extended=amt,
            quantity=1,
            rate=amt,
        )
        for i, (cat, desc, amt) in enumerate(rows)
    ]
    return ProposalBudget(
        rfpId="rfp-test",
        updatedAt=now,
        lineItems=items,
        agencyRevenueEstimate=sum(r[2] for r in rows if r[2] > 0),
        lumpSumTotal=sum(r[2] for r in rows if r[2] > 0),
    )


class RfpCostDemandsTests(unittest.IsolatedAsyncioTestCase):
    def test_sync_header_to_fee_detail_total(self) -> None:
        from app.services.proposal_budget_content import (
            sync_proposed_investment_to_fee_detail_total,
        )

        body = (
            "## Proposed Investment\n\n"
            "**Professional fees: $69,000**\n"
            "**Total proposed investment: $69,000**\n\n"
            "Total proposed investment: $69,000 ($69,000 in professional fees).\n\n"
            "## Fee Detail by Phase\n\n"
            "| Phase | Scope | Fee |\n"
            "| --- | --- | ---: |\n"
            "| Discovery | Kickoff | $7,500 |\n"
            "| Strategy | Plan | $60,000 |\n"
            "| **Total** | | **$67,500** |\n"
        )
        out = sync_proposed_investment_to_fee_detail_total(body)
        self.assertIn("**Professional fees: $67,500**", out)
        self.assertIn("**Total proposed investment: $67,500**", out)
        self.assertIn("$67,500 in professional fees", out)
        self.assertNotIn("$69,000", out)

    def test_fee_rollup_drops_zero_reference_rows(self) -> None:
        budget = _budget(
            ("Discovery", "Kickoff", 7500),
            ("Project-Based Rate Card (Reference Only)", "ref", 0),
            ("Media Planning & Digital Advertising", "Media plan", 30000),
        )
        rows = _rollup_phase_fee_rows(budget)
        labels = [r[0] for r in rows]
        self.assertTrue(any("Discovery" in x for x in labels))
        self.assertTrue(any("Media" in x for x in labels))
        self.assertFalse(any("Reference" in x for x in labels))
        self.assertTrue(all(r[2] and r[2] > 0 for r in rows))

    def test_restore_fee_detail_table_when_missing(self) -> None:
        budget = _budget(("Discovery", "Kickoff", 7500), ("Strategy", "Plan", 7500))
        body = "## Proposed Investment\n\n**Professional fees: $15,000**\n\n"
        out = ensure_fee_detail_table_in_budget_markdown(body, budget)
        self.assertIn("## Fee Detail by Phase", out)
        self.assertIn("| Phase | Scope | Fee |", out)
        self.assertIn("Discovery", out)

    def test_strip_legacy_demand_dump_sections(self) -> None:
        body = (
            "## Proposed Investment\n\nTotal $1.\n\n"
            "## RFP Cost demand — Nte\n\n"
            "<!-- rfp-cost-demand:nte -->\n"
            "[MANUAL FILL: Sonja — invent nothing]\n\n"
            "## Fee Detail by Phase\n\n| Phase | Scope | Fee |\n"
        )
        out = strip_rfp_cost_demand_stub_sections(body)
        self.assertNotIn("RFP Cost demand", out)
        self.assertIn("Fee Detail", out)

    def test_grounded_nte_uses_ledger_total(self) -> None:
        budget = _budget(("Discovery", "Kickoff", 67300))
        demands = [
            RfpCostDemand(
                id="not_to_exceed_total",
                kind="disclosure",
                requirement="State a total not-to-exceed contract amount",
                rfpQuote="TOTAL CONTRACT AMOUNT is an amount not to exceed",
                satisfaction="missing",
            )
        ]
        out, audited, logs = apply_grounded_demand_fills(
            "## Proposed Investment\n\n**Professional fees: $67,300**\n",
            demands,
            budget=budget,
        )
        self.assertTrue(logs)
        self.assertIn("67,300", out)
        self.assertIn("Not-to-Exceed", out)
        self.assertEqual(audited[0].satisfaction, "grounded")
        self.assertNotIn("## RFP Cost demand", out)

    def test_workstream_ask_becomes_fee_table_row(self) -> None:
        body = (
            "## Fee Detail by Phase\n\n"
            "| Phase | Scope | Fee |\n"
            "| --- | --- | ---: |\n"
            "| Discovery | Kickoff | $7,500 |\n"
            "| **Total** | | **$7,500** |\n"
        )
        demands = [
            RfpCostDemand(
                id="media_planning_and_buying_fee_home",
                kind="workstream_funding",
                requirement="Visible fee home for media planning and buying",
                satisfaction="missing",
            )
        ]
        out, logs = ensure_rfp_asks_in_fee_detail_table(body, demands)
        self.assertTrue(logs)
        self.assertIn("Media Planning", out)
        self.assertIn("MANUAL FILL", out)
        self.assertIn("**Total**", out)
        # Row sits before Total
        self.assertLess(out.index("Media Planning"), out.index("**Total**"))

    async def test_ensure_grounded_fills_not_demand_dump(self) -> None:
        demands = [
            RfpCostDemand(
                id="oral_presentation_expense",
                kind="disclosure",
                rfpQuote="Oral presentations will be made at the offeror's expense.",
                requirement="Disclose oral presentation costs borne by offeror",
                satisfaction="missing",
            ),
        ]
        prior = (
            "## Proposed Investment\n\n**Professional fees: $67,300**\n\n"
            "## Fee Detail by Phase\n\n"
            "| Phase | Scope | Fee |\n| --- | --- | ---: |\n"
            "| Discovery | Kickoff | $67,300 |\n"
            "| **Total** | | **$67,300** |\n"
        )
        budget = _budget(("Discovery", "Kickoff", 67300))

        async def _fake_extract(**_kwargs):
            return demands

        async def _fake_audit(ds, _content, **_kwargs):
            return [d.model_copy(update={"satisfaction": "missing"}) for d in ds]

        with (
            patch(
                "app.services.rfp_cost_demands.extract_rfp_cost_demands",
                new=AsyncMock(side_effect=_fake_extract),
            ),
            patch(
                "app.services.rfp_cost_demands.audit_rfp_cost_demands",
                new=AsyncMock(side_effect=_fake_audit),
            ),
        ):
            out, audited, logs = await ensure_rfp_cost_demands_in_budget_markdown(
                prior,
                rfp_text="ignored",
                budget=budget,
                rewrite=False,
            )

        self.assertIn("Fee Detail by Phase", out)
        self.assertIn("| Phase |", out)
        self.assertNotIn("## RFP Cost demand", out)
        self.assertTrue(any(d.satisfaction == "grounded" for d in audited) or logs)
        self.assertIn("oral presentation", out.casefold())

    def test_prompt_format_lists_demands(self) -> None:
        text = format_rfp_cost_demands_for_prompt(
            [
                RfpCostDemand(
                    id="x",
                    kind="disclosure",
                    requirement="State treatment",
                    rfpQuote="commissions and markups",
                )
            ]
        )
        self.assertIn("RFP COST DEMANDS", text)
        self.assertIn("commissions and markups", text)

    def test_compact_stub_not_section_dump(self) -> None:
        demand = RfpCostDemand(
            id="option_year_pricing",
            kind="disclosure",
            requirement="Confirm Year-2 pricing",
            satisfaction="missing",
        )
        stub = stub_markdown_for_demand(demand)
        self.assertIn("MANUAL FILL", stub)
        self.assertNotIn("## RFP Cost demand", stub)
        out, logs = apply_missing_demand_stubs("## Terms\n\nHello.\n", [demand])
        self.assertTrue(logs)
        self.assertIn("Outstanding Cost confirmations", out)


if __name__ == "__main__":
    unittest.main()
