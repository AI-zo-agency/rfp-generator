"""Pricing plan service: repair loop + budget build (LLM mocked)."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services import pricing_plan_service as svc
from app.services.pricing_plan_engine import parse_guide, parse_labor
from tests.test_pricing_plan_engine import plan_988_within_rules

FIX = Path(__file__).parent / "fixtures" / "pricing_plan"
GUIDE_MD = (FIX / "guide.md").read_text()
LABOR_MD = (FIX / "labor.md").read_text()
KB = svc.PricingKb(GUIDE_MD, parse_guide(GUIDE_MD), LABOR_MD, parse_labor(LABOR_MD))
RFP = (FIX / "988_rfp.txt").read_text()


def _json(name: str) -> dict:
    return json.loads((FIX / name).read_text())


class RepairLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_plan_is_sent_back_with_errors_then_accepted(self) -> None:
        good = plan_988_within_rules()
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
        bad = plan_988_within_rules()
        bad["sections"][0]["body_md"] += " Costs $12,000."
        with patch.object(svc.llm, "chat_json", AsyncMock(return_value=(bad, "p"))):
            plan, rounds = await svc.author_pricing_plan(RFP, _json("988_asks.json"), KB)
        self.assertEqual(len(rounds), svc.MAX_REPAIRS + 1)
        self.assertTrue(any("Unresolved check" in n["issue"] for n in plan["internal_notes"]))


class BudgetBuildTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_builds_budget_with_legacy_line_items(self) -> None:
        calls = AsyncMock(side_effect=[(_json("988_asks.json"), "p"), (plan_988_within_rules(), "p")])
        with patch.object(svc.llm, "chat_json", calls), patch.object(svc, "load_pricing_kb", AsyncMock(return_value=KB)):
            budget = await svc.generate_pricing_plan_budget("rfp-988", RFP)
        self.assertEqual(budget.budget_format, "pricing_plan")
        self.assertEqual(budget.pricing_tier, "High")
        self.assertEqual(budget.rfp_budget_cap, 950000.0)
        priced = sum(li.extended or 0 for li in budget.line_items if li.unit == "project")
        self.assertEqual(priced, 771275)
        md = svc.render_pricing_plan_budget(budget)
        self.assertIn("$771,275", md)
        self.assertNotIn("{{", md)


class GenerateBranchTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_routes_to_v2_and_passes_target(self) -> None:
        from app.models.proposal import ProposalBudget, ProposalResearchCache
        from app.services import proposal_pricing_service as pps

        research = ProposalResearchCache(rfpId="r1", updatedAt="t", targetBudgetUsd=250000)
        v2_budget = ProposalBudget(rfpId="r1", updatedAt="t", budgetFormat="pricing_plan", pricingPlan={"tasks": []})
        content = type("C", (), {"description": "desc", "pdf_text": "pdf"})()
        gen = AsyncMock(return_value=v2_budget)
        with patch.object(pps.llm, "is_configured", return_value=True), \
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
        calls = AsyncMock(side_effect=[(_json("988_asks.json"), "p"), (plan_988_within_rules(), "p")])
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
    async def test_target_travels_to_the_job_not_saved_by_the_request(self) -> None:
        from app.api.v1 import proposals as api

        enqueue = AsyncMock(return_value="queued")
        run = AsyncMock()
        with patch.object(api, "asave_research_cache", AsyncMock()) as save, \
             patch.object(api, "_enqueue_pipeline_phase", enqueue), \
             patch.object(api, "run_phase3_5_budget", run):
            await api.phase3_5_budget_endpoint(
                "r1", api.Phase35BudgetRequest(chainNext=False, targetBudgetUsd=180000)
            )
            save.assert_not_awaited()
            self.assertEqual(enqueue.await_args.kwargs["job_kwargs"], {"chain_next": False, "target_budget_usd": 180000})
            await enqueue.await_args.args[2]()  # the in-process work coroutine
        run.assert_awaited_once_with("r1", target_budget_usd=180000)

    async def test_job_saves_target_only_when_changed(self) -> None:
        from app.models.proposal import ProposalResearchCache
        from app.services import proposal_generator as gen

        research = ProposalResearchCache(rfpId="r1", updatedAt="t", targetBudgetUsd=180000)
        with patch.object(gen, "aget_research_cache", AsyncMock(return_value=research)), \
             patch.object(gen, "asave_research_cache", AsyncMock()) as save:
            await gen._sync_target_budget("r1", 180000)
            save.assert_not_awaited()
            await gen._sync_target_budget("r1", 0)  # cleared field -> None
            self.assertIsNone(save.await_args.args[0].target_budget_usd)


class ChatEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_edit_goes_through_checks(self) -> None:
        budget_calls = AsyncMock(side_effect=[(_json("988_asks.json"), "p"), (plan_988_within_rules(), "p")])
        with patch.object(svc.llm, "chat_json", budget_calls), patch.object(svc, "load_pricing_kb", AsyncMock(return_value=KB)):
            budget = await svc.generate_pricing_plan_budget("rfp-988", RFP)
        edited = plan_988_within_rules()
        edited["tasks"] = [t for t in edited["tasks"] if t["task_id"] != "A12c"]  # drop media
        edited["sections"] = [
            {**s, "body_md": s["body_md"].replace("{{VERBATIM:media}}", "").replace("{{MEDIA_SPLIT}}", "")}
            for s in edited["sections"]
        ]
        edited["reply"] = "Removed the media placement task."
        with patch.object(svc.llm, "chat_json", AsyncMock(return_value=(edited, "p"))), \
             patch.object(svc, "load_pricing_kb", AsyncMock(return_value=KB)):
            new_budget, reply = await svc.edit_pricing_plan_from_chat(budget, instruction="remove media", rfp_text=RFP)
        self.assertEqual(reply, "Removed the media placement task.")
        self.assertNotIn("A12c", [li.id for li in new_budget.line_items])
        self.assertNotIn("reply", new_budget.pricing_plan)


class PlanFollowUpTests(unittest.IsolatedAsyncioTestCase):
    async def _budget(self, plan=None):
        calls = AsyncMock(side_effect=[(_json("988_asks.json"), "p"), (plan or plan_988_within_rules(), "p")])
        with patch.object(svc.llm, "chat_json", calls), patch.object(svc, "load_pricing_kb", AsyncMock(return_value=KB)):
            return await svc.generate_pricing_plan_budget("rfp-988", RFP)

    async def test_internal_notes_and_tier_go_to_pricing_flags_not_render(self) -> None:
        budget = await self._budget()
        self.assertTrue(any(f.startswith("[PRICING NOTE — Sonja: ") for f in budget.pricing_flags), budget.pricing_flags)
        self.assertIn("[PRICING NOTE — tier: High — " , " ".join(budget.pricing_flags))
        self.assertNotIn("DO NOT PLACE", svc.render_pricing_plan_budget(budget))

    async def test_code_adds_media_assumption_note_once(self) -> None:
        budget = await self._budget()
        media = [n for n in budget.pricing_plan["internal_notes"] if n["issue"].startswith("Media budget of")]
        self.assertEqual(media, [{"issue": "Media budget of $430,000 is an assumption — confirm against the "
                                  "RFP / client media plan", "owner": "Sonja"}])
        edited = {**copy.deepcopy(budget.pricing_plan), "reply": "ok"}
        with patch.object(svc.llm, "chat_json", AsyncMock(return_value=(edited, "p"))), \
             patch.object(svc, "load_pricing_kb", AsyncMock(return_value=KB)):
            new_budget, _ = await svc.edit_pricing_plan_from_chat(budget, instruction="tighten", rfp_text=RFP)
        again = [n for n in new_budget.pricing_plan["internal_notes"] if n["issue"].startswith("Media budget of")]
        self.assertEqual(len(again), 1)

    async def test_tier_basis_separate_and_stripped_from_edit_prompt(self) -> None:
        budget = await self._budget()
        llm_line = plan_988_within_rules()["tier_rationale"]
        self.assertEqual(budget.pricing_plan["tier_rationale"], llm_line)
        self.assertTrue(budget.pricing_plan["tier_basis"])
        edited = {**copy.deepcopy(budget.pricing_plan), "reply": "ok"}
        call = AsyncMock(return_value=(edited, "p"))
        with patch.object(svc.llm, "chat_json", call), patch.object(svc, "load_pricing_kb", AsyncMock(return_value=KB)):
            new_budget, _ = await svc.edit_pricing_plan_from_chat(budget, instruction="tighten", rfp_text=RFP)
        sent = call.await_args_list[0].args[0][1]["content"].split("=== CURRENT PLAN ===")[1]
        for key in ('"tier"', '"tier_basis"', '"kb_snapshot"'):
            self.assertNotIn(key, sent)
        self.assertEqual(new_budget.pricing_plan["tier_rationale"], llm_line)

    async def test_llm_error_mid_repair_keeps_last_plan(self) -> None:
        bad = plan_988_within_rules()
        bad["sections"][0]["body_md"] += " Costs $12,000."
        calls = AsyncMock(side_effect=[(bad, "p"), svc.llm.LlmError("cap reached")])
        with patch.object(svc.llm, "chat_json", calls):
            plan, rounds = await svc.author_pricing_plan(RFP, _json("988_asks.json"), KB)
        self.assertIn("Costs $12,000.", plan["sections"][0]["body_md"])
        self.assertTrue(any("Unresolved check: prose contains a literal dollar" in n["issue"] for n in plan["internal_notes"]))
        self.assertTrue(rounds[-1]["errors"])

    async def test_first_call_non_dict_is_502(self) -> None:
        with patch.object(svc.llm, "chat_json", AsyncMock(return_value=(["not", "a", "plan"], "p"))):
            with self.assertRaises(svc.ProposalError) as ctx:
                await svc.author_pricing_plan(RFP, _json("988_asks.json"), KB)
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("returned no plan", str(ctx.exception))


class LineItemAdapterTests(unittest.TestCase):
    def test_line_items_sum_to_term_value(self) -> None:
        from app.services.pricing_plan_engine import compute, term_value

        for plan in (_json("newport_plan.json"), plan_988_within_rules()):
            items = svc.plan_to_line_items(plan, KB.labor)
            total = sum(li.extended for li in items if li.extended is not None)
            self.assertEqual(total, term_value(compute(plan, KB.labor)))

    def test_billing_shapes_and_types(self) -> None:
        items = {li.id: li for li in svc.plan_to_line_items(_json("newport_plan.json"), KB.labor)}
        self.assertEqual((items["A1"].unit, items["A1"].quantity), ("month", 12))
        self.assertEqual(items["A1"].extended, 12 * items["A1"].rate)
        self.assertEqual((items["A4"].unit, items["A4"].extended, items["A4"].notes), ("event", None, "per event"))
        self.assertEqual(items["A1"].line_item_type, "agency_fee")
        media = {li.id: li for li in svc.plan_to_line_items(plan_988_within_rules(), KB.labor)}["A12c"]
        self.assertEqual(media.line_item_type, "client_passthrough")


def _newport_budget():
    from app.services.pricing_plan_engine import decide_tier

    asks = _json("newport_asks.json")
    plan = svc._stamp(_json("newport_plan.json"), KB, *decide_tier(asks))
    return svc._budget_from_plan("r-newport", asks, plan, [])


def _fail(name):
    from unittest.mock import MagicMock

    return MagicMock(side_effect=AssertionError(f"{name} must not run on a pricing-plan budget"))


class LegacyGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_budget_passes_leave_v2_budget_alone(self) -> None:
        from app.services.proposal_budget_editor import run_budget_editor_pass
        from app.services.proposal_budget_validation import reconcile_proposal_budget
        from app.services.rfp_cost_demands import ensure_rfp_cost_demands_in_budget_markdown

        budget = _newport_budget()
        self.assertIs(run_budget_editor_pass(budget, rfp_context="x"), budget)
        self.assertIs(reconcile_proposal_budget(budget, rfp_context="x"), budget)
        body = svc.render_pricing_plan_budget(budget)
        out, demands, _logs = await ensure_rfp_cost_demands_in_budget_markdown(
            body, rfp_text="x", budget=budget, rewrite=True
        )
        self.assertEqual((out, demands), (body, []))


class Phase35PricingPlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_phase35_places_plan_and_skips_legacy_chain(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
        from app.services import proposal_budget_content as pbc
        from app.services import proposal_generator as gen

        budget = _newport_budget()
        research = ProposalResearchCache(rfpId="r-newport", updatedAt="t", budget=budget)
        draft = ProposalDraft(
            rfpId="r-newport", updatedAt="t",
            sections=[
                ProposalSection(id="s1", title="Approach", content="We will plan.", status="generated"),
                ProposalSection(id="s2", title="Budget & Pricing", content="old legacy fee table", status="generated"),
            ],
        )
        saved = AsyncMock()
        with patch.object(gen, "aget_research_cache", AsyncMock(return_value=research)), \
             patch.object(gen, "generate_proposal_budget", AsyncMock(return_value=(budget, research))), \
             patch.object(gen, "_assert_proposal_not_reset", AsyncMock()), \
             patch.object(gen, "load_rfp_for_proposal", return_value=(None, None, "Newport RFP text")), \
             patch.object(gen, "asave_proposal_draft", saved), \
             patch.object(pbc, "aget_proposal_draft", AsyncMock(return_value=draft)), \
             patch.object(pbc, "asave_proposal_draft", AsyncMock()), \
             patch.object(gen, "run_budget_editor_pass", _fail("run_budget_editor_pass")), \
             patch("app.services.proposal_budget_validation.reconcile_proposal_budget", _fail("reconcile")), \
             patch("app.services.proposal_budget_format_judge.judge_rfp_budget_format", _fail("format judge")), \
             patch.object(gen, "align_fee_narrative_with_budget", _fail("align_fee_narrative")), \
             patch.object(gen, "run_budget_grounding_check", _fail("grounding check")), \
             patch.object(pbc, "sync_phase_budget_tables_across_draft", _fail("phase table sync")):
            out_draft, out_research, out_budget = await gen._run_phase3_5_budget_inner(
                "r-newport", app_settings=object(), has_manuscript=True
            )
        self.assertIs(out_budget, budget)
        self.assertIs(out_research, research)
        final = saved.await_args.args[0]
        self.assertIs(final, out_draft)
        cost = next(s for s in final.sections if s.id == "s2")
        self.assertEqual(cost.content.strip(), svc.render_pricing_plan_budget(budget).strip())


class ReconcileCachedBudgetPricingPlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconcile_cached_budget_returns_v2_unchanged(self) -> None:
        from app.models.proposal import ProposalResearchCache
        from app.services import proposal_pricing_service as ps

        budget = _newport_budget()
        research = ProposalResearchCache(rfpId="r-newport", updatedAt="t", budget=budget)
        saved = AsyncMock()
        with patch.object(ps, "aget_research_cache", AsyncMock(return_value=research)), \
             patch.object(ps, "load_rfp_for_proposal", return_value=(None, None, "Newport RFP text")), \
             patch.object(ps, "asave_research_cache", saved):
            out_budget, out_research = await ps.reconcile_cached_budget("r-newport")
        self.assertIs(out_budget, budget)
        self.assertIs(out_research, research)
        saved.assert_not_awaited()


class Phase35ReconcilePricingPlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconcile_places_plan_and_skips_legacy_chain(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
        from app.services import proposal_budget_content as pbc
        from app.services import proposal_generator as gen
        from app.services import proposal_pricing_service as ps

        budget = _newport_budget()
        research = ProposalResearchCache(rfpId="r-newport", updatedAt="t", budget=budget)
        draft = ProposalDraft(
            rfpId="r-newport", updatedAt="t",
            sections=[
                ProposalSection(id="s1", title="Approach", content="We will plan.", status="generated"),
                ProposalSection(id="s2", title="Budget & Pricing", content="old legacy fee table", status="generated"),
            ],
        )
        saved = AsyncMock()
        with patch.object(ps, "reconcile_cached_budget", AsyncMock(return_value=(budget, research))), \
             patch.object(gen, "_assert_proposal_not_reset", AsyncMock()), \
             patch.object(gen, "load_rfp_for_proposal", return_value=(None, None, "Newport RFP text")), \
             patch.object(gen, "aget_proposal_draft", AsyncMock(return_value=draft)), \
             patch.object(gen, "asave_proposal_draft", saved), \
             patch.object(pbc, "aget_proposal_draft", AsyncMock(return_value=draft)), \
             patch.object(pbc, "asave_proposal_draft", AsyncMock()), \
             patch.object(gen, "run_budget_editor_pass", _fail("run_budget_editor_pass")), \
             patch("app.services.proposal_budget_validation.reconcile_proposal_budget", _fail("reconcile")), \
             patch("app.services.proposal_budget_format_judge.judge_rfp_budget_format", _fail("format judge")), \
             patch.object(gen, "align_fee_narrative_with_budget", _fail("align_fee_narrative")), \
             patch.object(gen, "run_budget_grounding_check", _fail("grounding check")), \
             patch.object(pbc, "sync_phase_budget_tables_across_draft", _fail("phase table sync")), \
             patch.object(ps, "coerce_budget_to_phased_from_guide", _fail("coerce")), \
             patch.object(pbc, "apply_rfp_required_budget_instrument", _fail("instrument reshape")):
            out_draft, out_research, out_budget = await gen.run_phase3_5_budget_reconcile("r-newport")
        self.assertIs(out_budget, budget)
        self.assertEqual(out_budget.budget_format, "pricing_plan")
        self.assertEqual(out_budget.line_items, budget.line_items)
        self.assertEqual(out_budget.pricing_plan, budget.pricing_plan)
        self.assertIs(out_research, research)
        final = saved.await_args.args[0]
        self.assertIs(final, out_draft)
        cost = next(s for s in final.sections if s.id == "s2")
        self.assertEqual(cost.content.strip(), svc.render_pricing_plan_budget(budget).strip())


if __name__ == "__main__":
    unittest.main()
