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


class GenerateBranchTests(unittest.IsolatedAsyncioTestCase):
    async def test_flag_on_routes_generate_to_v2_and_passes_target(self) -> None:
        from app.models.proposal import ProposalBudget, ProposalResearchCache
        from app.services import proposal_pricing_service as pps

        research = ProposalResearchCache(rfpId="r1", updatedAt="t", targetBudgetUsd=250000)
        v2_budget = ProposalBudget(rfpId="r1", updatedAt="t", budgetFormat="pricing_plan", pricingPlan={"tasks": []})
        content = type("C", (), {"description": "desc", "pdf_text": "pdf"})()
        gen = AsyncMock(return_value=v2_budget)
        with patch.object(pps.settings, "use_pricing_plan_v2", True), \
             patch.object(pps.llm, "is_configured", return_value=True), \
             patch.object(pps, "load_rfp_for_proposal", return_value=(object(), content, "ctx")), \
             patch.object(pps, "aget_research_cache", AsyncMock(return_value=research)), \
             patch.object(pps, "asave_research_cache", AsyncMock()) as save, \
             patch("app.services.pricing_plan_service.generate_pricing_plan_budget", gen):
            budget, saved = await pps.generate_proposal_budget("r1")
        self.assertEqual(budget.budget_format, "pricing_plan")
        self.assertEqual(gen.await_args.kwargs["target_budget_usd"], 250000)
        self.assertIs(saved.budget, budget)
        save.assert_awaited_once()


class RenderSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def _v2_budget(self):
        calls = AsyncMock(side_effect=[(_json("988_asks.json"), "p"), (_json("988_plan.json"), "p")])
        with patch.object(svc.llm, "chat_json", calls), patch.object(svc, "load_pricing_kb", AsyncMock(return_value=KB)):
            return await svc.generate_pricing_plan_budget("rfp-988", RFP)

    async def test_render_budget_markdown_uses_plan(self) -> None:
        from app.services.proposal_budget_content import render_budget_markdown

        budget = await self._v2_budget()
        self.assertEqual(render_budget_markdown(budget, rfp_text=RFP), svc.render_pricing_plan_budget(budget))

    async def test_persist_guard_restores_budget_section_from_plan(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalSection
        from app.services.proposal_zero_fabrication import apply_zero_fabrication_guards

        budget = await self._v2_budget()
        draft = ProposalDraft(
            rfpId="rfp-988", updatedAt="t",
            sections=[
                ProposalSection(id="s1", title="Approach", content="We will plan.", status="generated"),
                ProposalSection(id="s2", title="Budget & Pricing", content="| scrubbed | [MANUAL FILL] |", status="generated"),
            ],
        )
        out, report = apply_zero_fabrication_guards(draft, budget=budget, rfp_text=RFP)
        self.assertEqual(out.sections[1].content.strip(), svc.render_pricing_plan_budget(budget).strip())
        self.assertTrue(any("pricing plan" in line for line in report.logs))


class TargetBudgetEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_target_saved_before_budget_job(self) -> None:
        from app.api.v1 import proposals as api
        from app.models.proposal import ProposalResearchCache

        research = ProposalResearchCache(rfpId="r1", updatedAt="t")
        with patch.object(api, "aget_research_cache", AsyncMock(return_value=research)), \
             patch.object(api, "asave_research_cache", AsyncMock()) as save, \
             patch.object(api, "_enqueue_pipeline_phase", AsyncMock(return_value="queued")):
            await api.phase3_5_budget_endpoint(
                "r1", api.Phase35BudgetRequest(chainNext=False, targetBudgetUsd=180000)
            )
        self.assertEqual(save.await_args.args[0].target_budget_usd, 180000)
