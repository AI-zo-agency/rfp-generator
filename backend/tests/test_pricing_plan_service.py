"""Pricing plan service: repair loop + budget build (LLM mocked)."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services import pricing_plan_service as svc
from app.services.pricing_plan_engine import parse_guide, parse_labor

FIX = Path(__file__).parent / "fixtures" / "pricing_plan"
GUIDE_MD = (FIX / "guide.md").read_text()
LABOR_MD = (FIX / "labor.md").read_text()
KB = svc.PricingKb(GUIDE_MD, parse_guide(GUIDE_MD), LABOR_MD, parse_labor(LABOR_MD))
RFP = (FIX / "988_rfp.txt").read_text()


def _json(name: str) -> dict:
    return json.loads((FIX / name).read_text())


class RepairLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_plan_is_sent_back_with_errors_then_accepted(self) -> None:
        good = _json("988_plan.json")
        bad = copy.deepcopy(good)
        bad["sections"][0]["body_md"] += " Costs $12,000."
        calls = AsyncMock(side_effect=[(bad, "p"), (good, "p")])
        with patch.object(svc.llm, "chat_json", calls):
            plan, rounds = await svc.author_pricing_plan(RFP, _json("988_asks.json"), KB)
        self.assertEqual(calls.await_count, 2)
        repair_prompt = calls.await_args_list[1].args[0][1]["content"]
        self.assertIn("prose contains a literal dollar figure", repair_prompt)
        self.assertEqual(rounds[-1]["errors"], [])
        self.assertEqual(plan["tier"], "High")
        self.assertEqual(plan["kb_snapshot"]["labor"]["Agency Director"], 400.0)

    async def test_unresolved_errors_become_internal_notes(self) -> None:
        bad = _json("988_plan.json")
        bad["sections"][0]["body_md"] += " Costs $12,000."
        with patch.object(svc.llm, "chat_json", AsyncMock(return_value=(bad, "p"))):
            plan, rounds = await svc.author_pricing_plan(RFP, _json("988_asks.json"), KB)
        self.assertEqual(len(rounds), svc.MAX_REPAIRS + 1)
        self.assertTrue(any("Unresolved check" in n["issue"] for n in plan["internal_notes"]))


class BudgetBuildTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_builds_budget_with_legacy_line_items(self) -> None:
        calls = AsyncMock(side_effect=[(_json("988_asks.json"), "p"), (_json("988_plan.json"), "p")])
        with patch.object(svc.llm, "chat_json", calls), patch.object(svc, "load_pricing_kb", AsyncMock(return_value=KB)):
            budget = await svc.generate_pricing_plan_budget("rfp-988", RFP)
        self.assertEqual(budget.budget_format, "pricing_plan")
        self.assertEqual(budget.pricing_tier, "High")
        self.assertEqual(budget.rfp_budget_cap, 950000.0)
        priced = sum(li.extended or 0 for li in budget.line_items if li.unit == "project")
        self.assertEqual(priced, 717650)
        md = svc.render_pricing_plan_budget(budget)
        self.assertIn("$717,650", md)
        self.assertNotIn("{{", md)


if __name__ == "__main__":
    unittest.main()
