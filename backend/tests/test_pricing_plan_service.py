"""Pricing plan service: repair loop, budget build, render switch, chat edit (LLM mocked)."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import AsyncMock, patch

from app.services import pricing_plan_service as svc
from app.services.pricing_plan_engine import compute, term_value
from tests.test_pricing_plan_engine import BOOK, good_plan

RFP = "Acme Remodeling requests proposals. Scope: S1 a brand book. S2 two landing pages."
ASKS = {
    "priced_scope": [{"id": "S1", "item": "brand book"}, {"id": "S2", "item": "landing pages"}],
    "ceilings": [], "asks": [], "client": {"name": "Acme Remodeling", "kind": "private", "quote": None},
}
RFP_CAP = RFP + " The total fee shall not exceed $50,000."
ASKS_CAP = {**ASKS, "ceilings": [{"label": "Total", "amount": 50000, "scope": "total", "quote": "shall not exceed $50,000"}]}


def full_plan() -> dict:
    """good_plan plus a monthly fee, a per-event rate and a traditional media task."""
    plan = good_plan()
    plan["tasks"] += [
        {"task_id": "A3", "group": "SEO", "deliverable": "Monthly SEO", "scope_ids": ["S2"], "billing": "monthly",
         "quantity": 1, "catalog_code": "7c", "catalog_item": "SEO Basic"},
        {"task_id": "A4", "group": "Photo", "deliverable": "Photo day", "scope_ids": ["S2"], "billing": "per_event",
         "quantity": 1, "catalog_code": "5a", "catalog_item": "Photography: Half Day"},
        {"task_id": "A5", "group": "Media", "deliverable": "Radio and print", "scope_ids": ["S2"], "billing": "one_time",
         "quantity": 1, "media_kind": "traditional", "media_spend": 40_000,
         "build": {"hours": {"AM": 6}, "pos": {}, "hard_cost": 0, "basis": "placement and trafficking"}},
    ]
    plan["sections"][0]["body_md"] += "\n\n{{MEDIA_SPLIT}}"
    plan["internal_notes"] = [{"issue": "Media budget of $40,000 is the RFP's own figure", "owner": "Writer"}]
    return plan


def llm_returns(*plans: dict) -> AsyncMock:
    return AsyncMock(side_effect=[(p, "p") for p in plans])


async def build_budget(plan: dict | None = None, asks: dict = ASKS, rfp: str = RFP):
    calls = llm_returns(asks, plan or good_plan())
    with patch.object(svc.llm, "chat_json", calls), patch.object(svc, "load_pricing_kb", AsyncMock(return_value=BOOK)):
        return await svc.generate_pricing_plan_budget("rfp-1", rfp)


def new_budget():
    from app.services.pricing_plan_engine import price_plan

    plan = good_plan()
    price_plan(plan, BOOK, {})
    return svc._budget_from_plan("r-acme", ASKS, svc._stamp(plan, BOOK), [], BOOK)


class RepairLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_plan_is_sent_back_with_errors_then_accepted(self) -> None:
        bad = good_plan()
        bad["sections"][0]["body_md"] += " Costs $12,000."
        calls = llm_returns(bad, good_plan())
        with patch.object(svc.llm, "chat_json", calls):
            plan, rounds = await svc.author_pricing_plan(RFP, ASKS, BOOK)
        self.assertEqual(calls.await_count, 2)
        repair_prompt = calls.await_args_list[1].args[0][1]["content"]
        self.assertIn("prose contains a literal dollar figure", repair_prompt)
        self.assertEqual(rounds[-1]["errors"], [])
        self.assertEqual(plan["pricing_version"], "v2")
        self.assertEqual(plan["kb_snapshot"]["roles"]["PM"]["loaded"], 85.0)

    async def test_unresolved_errors_become_internal_notes(self) -> None:
        bad = good_plan()
        bad["sections"][0]["body_md"] += " Costs $12,000."
        with patch.object(svc.llm, "chat_json", AsyncMock(return_value=(bad, "p"))):
            plan, rounds = await svc.author_pricing_plan(RFP, ASKS, BOOK)
        self.assertEqual(len(rounds), svc.MAX_REPAIRS + 1)
        self.assertTrue(any("Unresolved check" in n["issue"] for n in plan["internal_notes"]))

    async def test_malformed_plan_is_an_error_for_the_loop_not_a_crash(self) -> None:
        broken = good_plan()
        broken["tasks"] = "not a list"
        calls = llm_returns(broken, good_plan())
        with patch.object(svc.llm, "chat_json", calls):
            plan, rounds = await svc.author_pricing_plan(RFP, ASKS, BOOK)
        self.assertIn("malformed", rounds[0]["errors"][0])
        self.assertEqual(rounds[-1]["errors"], [])

    async def test_llm_error_mid_repair_keeps_last_plan(self) -> None:
        bad = good_plan()
        bad["sections"][0]["body_md"] += " Costs $12,000."
        calls = AsyncMock(side_effect=[(bad, "p"), svc.llm.LlmError("cap reached")])
        with patch.object(svc.llm, "chat_json", calls):
            plan, rounds = await svc.author_pricing_plan(RFP, ASKS, BOOK)
        self.assertIn("Costs $12,000.", plan["sections"][0]["body_md"])
        self.assertTrue(any("Unresolved check: prose contains a literal dollar" in n["issue"] for n in plan["internal_notes"]))
        self.assertTrue(rounds[-1]["errors"])

    async def test_first_call_non_dict_is_502(self) -> None:
        with patch.object(svc.llm, "chat_json", AsyncMock(return_value=(["not", "a", "plan"], "p"))):
            with self.assertRaises(svc.ProposalError) as ctx:
                await svc.author_pricing_plan(RFP, ASKS, BOOK)
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("returned no plan", str(ctx.exception))

    async def test_the_ai_sees_the_internal_doc_and_the_budget_to_fit(self) -> None:
        calls = llm_returns(good_plan())
        with patch.object(svc.llm, "chat_json", calls):
            await svc.author_pricing_plan(RFP_CAP, ASKS_CAP, BOOK)
        prompt = calls.await_args_list[0].args[0][1]["content"]
        self.assertIn("PRICING INTERNAL", prompt)
        self.assertIn("Margin floor", prompt)
        self.assertIn('"__total__": 50000', prompt)

    async def test_floor_over_budget_names_the_cuts(self) -> None:
        asks = {**ASKS, "ceilings": [{"label": "Total", "amount": 9000, "scope": "total", "quote": "shall not exceed $9,000"}]}
        with patch.object(svc.llm, "chat_json", AsyncMock(return_value=(good_plan(), "p"))):
            plan, rounds = await svc.author_pricing_plan(RFP + " The total fee shall not exceed $9,000.", asks, BOOK)
        notes = " ".join(n["issue"] for n in plan["internal_notes"])
        self.assertIn("Cuts that would fit", notes)
        self.assertIn("A2 Two landing pages saves", notes)


class BudgetBuildTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_builds_budget_with_line_items(self) -> None:
        budget = await build_budget(asks=ASKS_CAP, rfp=RFP_CAP)
        self.assertEqual(budget.budget_format, "pricing_plan")
        self.assertIsNone(budget.pricing_tier)
        self.assertEqual(budget.rfp_budget_cap, 50000.0)
        total = sum(li.extended or 0 for li in budget.line_items)
        c = compute(budget.pricing_plan, BOOK)
        self.assertEqual(total, term_value(c))
        self.assertLessEqual(total, 50000)
        md = svc.render_pricing_plan_budget(budget)
        self.assertIn(f"${total:,.0f}", md)
        self.assertNotIn("{{", md)

    async def test_internal_notes_and_summary_go_to_flags_not_render(self) -> None:
        budget = await build_budget()
        self.assertTrue(any("priced with Pricing v2" in f for f in budget.pricing_flags))
        # flags reach every user of the app: never margins, costs or hours
        flags = " ".join(budget.pricing_flags).lower()
        for leak in ("gross profit", "margin", "in-house", "all-in", "hours"):
            self.assertNotIn(leak, flags)
        self.assertNotIn("DO NOT PLACE", svc.render_pricing_plan_budget(budget))
        self.assertIn("INTERNAL — DO NOT PLACE", budget.pricing_plan["internal_summary"])

    async def test_client_text_has_no_internal_numbers(self) -> None:
        md = svc.render_pricing_plan_budget(await build_budget())
        for leak in ("margin", "in-house", "Creative Director", "PM", "loaded"):
            self.assertNotIn(leak, md)

    async def test_code_adds_media_assumption_note_once(self) -> None:
        plan = full_plan()
        plan["internal_notes"] = []
        budget = await build_budget(plan)
        media = [n for n in budget.pricing_plan["internal_notes"] if n["issue"].startswith("Media budget of")]
        self.assertEqual(len(media), 1)
        self.assertIn("$40,000", media[0]["issue"])

    async def test_code_skips_media_note_when_the_ai_already_flagged_the_amount(self) -> None:
        budget = await build_budget(full_plan())
        media = [n for n in budget.pricing_plan["internal_notes"] if n["issue"].startswith("Media budget of")]
        self.assertEqual(len(media), 1)  # the AI's own note; code added none


class GenerateBranchTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_routes_to_plan_and_passes_target(self) -> None:
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


def legacy_budget():
    """A budget saved by the old band-based engine."""
    from app.models.proposal import ProposalBudget

    plan = {
        "tasks": [{"task_id": "A1", "group": "Social", "deliverable": "Social package", "billing": "one_time",
                   "guide_id": "4.5", "quantity": 1,
                   "staffing": [{"role": "Copywriter", "hours": 10}]}],
        "sections": [{"heading": "Cost", "body_md": "{{TASK_TABLE}}\n\n{{STAFFING_TABLE}}\n\n{{VERBATIM:revisions}}"}],
        "kb_snapshot": {"verbatim": {"revisions": "Two rounds."}, "labor": {"Copywriter": 150.0}},
    }
    return ProposalBudget(rfpId="r-old", updatedAt="t", budgetFormat="pricing_plan", pricingPlan=plan,
                          pricingAsks={"ceilings": []})


class LegacyPlanTests(unittest.TestCase):
    """Budgets priced by the old band guide keep rendering exactly as before."""

    def test_legacy_plan_renders_with_the_frozen_renderer(self) -> None:
        md = svc.render_pricing_plan_budget(legacy_budget())
        self.assertIn("$1,500", md)
        self.assertIn("| A1 | Copywriter | 10 | $150 | $1,500 |", md)
        self.assertIn("Two rounds.", md)

    def test_term_value_works_for_both_shapes(self) -> None:
        self.assertEqual(svc.plan_term_value(legacy_budget().pricing_plan), 1500)
        self.assertEqual(svc.plan_term_value(new_budget().pricing_plan), 22_300)


class LegacyEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_editing_a_legacy_budget_is_refused_with_a_clear_reason(self) -> None:
        with self.assertRaises(svc.ProposalError) as ctx:
            await svc.edit_pricing_plan_from_chat(legacy_budget(), instruction="x", rfp_text=RFP)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("old pricing guide", str(ctx.exception))


class RenderSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def test_render_budget_markdown_uses_plan(self) -> None:
        from app.services.proposal_budget_content import render_budget_markdown

        budget = await build_budget()
        self.assertEqual(render_budget_markdown(budget, rfp_text=RFP), svc.render_pricing_plan_budget(budget))

    async def test_persist_guard_restores_budget_section_from_plan(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalSection
        from app.services.proposal_zero_fabrication import apply_zero_fabrication_guards

        budget = await build_budget()
        draft = ProposalDraft(
            rfpId="rfp-1", updatedAt="t",
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
        budget = await build_budget(full_plan())
        edited = full_plan()
        edited["tasks"] = [t for t in edited["tasks"] if t["task_id"] != "A5"]  # drop media
        edited["sections"][0]["body_md"] = edited["sections"][0]["body_md"].replace("{{MEDIA_SPLIT}}", "")
        edited["internal_notes"] = []
        edited["reply"] = "Removed the media placement task."
        with patch.object(svc.llm, "chat_json", AsyncMock(return_value=(edited, "p"))), \
             patch.object(svc, "load_pricing_kb", AsyncMock(return_value=BOOK)):
            new_budget, reply = await svc.edit_pricing_plan_from_chat(budget, instruction="remove media", rfp_text=RFP)
        self.assertEqual(reply, "Removed the media placement task.")
        self.assertNotIn("A5", [li.id for li in new_budget.line_items])
        self.assertNotIn("reply", new_budget.pricing_plan)

    async def test_snapshot_and_summary_are_stripped_from_the_edit_prompt(self) -> None:
        budget = await build_budget()
        edited = {**copy.deepcopy(budget.pricing_plan), "reply": "ok"}
        call = AsyncMock(return_value=(edited, "p"))
        with patch.object(svc.llm, "chat_json", call), patch.object(svc, "load_pricing_kb", AsyncMock(return_value=BOOK)):
            await svc.edit_pricing_plan_from_chat(budget, instruction="tighten", rfp_text=RFP)
        sent = call.await_args_list[0].args[0][1]["content"].split("=== CURRENT PLAN ===")[1]
        for key in ('"kb_snapshot"', '"internal_summary"', '"pricing"', '"pricing_version"'):
            self.assertNotIn(key, sent)

    async def test_editing_after_a_new_pricing_version_goes_live_is_refused(self) -> None:
        budget = await build_budget()
        newer = copy.copy(BOOK)
        object.__setattr__(newer, "version", "v3")
        with patch.object(svc, "load_pricing_kb", AsyncMock(return_value=newer)):
            with self.assertRaises(svc.ProposalError) as ctx:
                await svc.edit_pricing_plan_from_chat(budget, instruction="x", rfp_text=RFP)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("priced with Pricing v2", str(ctx.exception))


class LineItemAdapterTests(unittest.TestCase):
    def _items(self):
        from app.services.pricing_plan_engine import price_plan

        plan = full_plan()
        price_plan(plan, BOOK, {})
        return plan, {li.id: li for li in svc.plan_to_line_items(plan, BOOK)}

    def test_line_items_sum_to_term_value(self) -> None:
        plan, items = self._items()
        total = sum(li.extended for li in items.values() if li.extended is not None)
        self.assertEqual(total, term_value(compute(plan, BOOK)))

    def test_billing_shapes_and_types(self) -> None:
        _plan, items = self._items()
        self.assertEqual((items["A3"].unit, items["A3"].quantity), ("month", 12))
        self.assertEqual(items["A3"].extended, 12 * items["A3"].rate)
        self.assertEqual((items["A4"].unit, items["A4"].extended, items["A4"].notes), ("event", None, "per event"))
        self.assertEqual(items["A1"].line_item_type, "agency_fee")
        self.assertEqual(items["A1"].rate_source, "Catalog 1b")
        self.assertEqual(items["A5"].line_item_type, "client_passthrough")
        self.assertEqual(items["A5"].extended, 40_000)


class MoneyIntelligenceFactsTests(unittest.TestCase):
    def test_plan_budget_facts_come_from_the_plan(self) -> None:
        from app.models.proposal import ProposalBudget
        from app.services.proposal_money_intelligence import _canonical_budget_facts

        budget = new_budget()
        facts = _canonical_budget_facts(budget)
        self.assertIn("pricingVersion: v2", facts)
        self.assertIn("termValue (our bid: one-time fees + the term's monthly fees): 22,300.00", facts)
        self.assertNotIn("ZERO", facts)

        legacy = ProposalBudget(rfpId="r-old", updatedAt="t")
        self.assertIn("agencyRevenueEstimate is ZERO", _canonical_budget_facts(legacy))


class Phase35PricingPlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_phase35_places_plan_and_skips_legacy_chain(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
        from app.services import proposal_budget_content as pbc
        from app.services import proposal_generator as gen

        budget = new_budget()
        research = ProposalResearchCache(rfpId="r-acme", updatedAt="t", budget=budget)
        draft = ProposalDraft(
            rfpId="r-acme", updatedAt="t",
            sections=[
                ProposalSection(id="s1", title="Approach", content="We will plan.", status="generated"),
                ProposalSection(id="s2", title="Budget & Pricing", content="old legacy fee table", status="generated"),
            ],
        )
        saved = AsyncMock()
        with patch.object(gen, "aget_research_cache", AsyncMock(return_value=research)), \
             patch.object(gen, "generate_proposal_budget", AsyncMock(return_value=(budget, research))), \
             patch.object(gen, "_assert_proposal_not_reset", AsyncMock()), \
             patch.object(gen, "load_rfp_for_proposal", return_value=(None, None, "Acme RFP text")), \
             patch.object(gen, "asave_proposal_draft", saved), \
             patch.object(pbc, "aget_proposal_draft", AsyncMock(return_value=draft)), \
             patch.object(pbc, "asave_proposal_draft", AsyncMock()):
            out_draft, out_research, out_budget = await gen._run_phase3_5_budget_inner(
                "r-acme", app_settings=object(), has_manuscript=True
            )
        self.assertIs(out_budget, budget)
        self.assertIs(out_research, research)
        final = saved.await_args.args[0]
        self.assertIs(final, out_draft)
        cost = next(s for s in final.sections if s.id == "s2")
        self.assertEqual(cost.content.strip(), svc.render_pricing_plan_budget(budget).strip())


class ReconcileCachedBudgetPricingPlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconcile_cached_budget_returns_plan_unchanged(self) -> None:
        from app.models.proposal import ProposalResearchCache
        from app.services import proposal_pricing_service as ps

        budget = new_budget()
        research = ProposalResearchCache(rfpId="r-acme", updatedAt="t", budget=budget)
        saved = AsyncMock()
        with patch.object(ps, "aget_research_cache", AsyncMock(return_value=research)), \
             patch.object(ps, "load_rfp_for_proposal", return_value=(None, None, "Acme RFP text")), \
             patch.object(ps, "asave_research_cache", saved):
            out_budget, out_research = await ps.reconcile_cached_budget("r-acme")
        self.assertIs(out_budget, budget)
        self.assertIs(out_research, research)
        saved.assert_not_awaited()


class Phase35ReconcilePricingPlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconcile_places_plan_and_skips_legacy_chain(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
        from app.services import proposal_budget_content as pbc
        from app.services import proposal_generator as gen
        from app.services import proposal_pricing_service as ps

        budget = new_budget()
        research = ProposalResearchCache(rfpId="r-acme", updatedAt="t", budget=budget)
        draft = ProposalDraft(
            rfpId="r-acme", updatedAt="t",
            sections=[
                ProposalSection(id="s1", title="Approach", content="We will plan.", status="generated"),
                ProposalSection(id="s2", title="Budget & Pricing", content="old legacy fee table", status="generated"),
            ],
        )
        saved = AsyncMock()
        with patch.object(ps, "reconcile_cached_budget", AsyncMock(return_value=(budget, research))), \
             patch.object(gen, "_assert_proposal_not_reset", AsyncMock()), \
             patch.object(gen, "load_rfp_for_proposal", return_value=(None, None, "Acme RFP text")), \
             patch.object(gen, "aget_proposal_draft", AsyncMock(return_value=draft)), \
             patch.object(gen, "asave_proposal_draft", saved), \
             patch.object(pbc, "aget_proposal_draft", AsyncMock(return_value=draft)), \
             patch.object(pbc, "asave_proposal_draft", AsyncMock()):
            out_draft, out_research, out_budget = await gen.run_phase3_5_budget_reconcile("r-acme")
        self.assertIs(out_budget, budget)
        self.assertEqual(out_budget.budget_format, "pricing_plan")
        self.assertEqual(out_budget.line_items, budget.line_items)
        self.assertEqual(out_budget.pricing_plan, budget.pricing_plan)
        self.assertIs(out_research, research)
        final = saved.await_args.args[0]
        self.assertIs(final, out_draft)
        cost = next(s for s in final.sections if s.id == "s2")
        self.assertEqual(cost.content.strip(), svc.render_pricing_plan_budget(budget).strip())


class SelectionModeRoutesToPlanEditTests(unittest.IsolatedAsyncioTestCase):
    """A selection edit on the Cost tab must hit the plan branch too (not the patch/redraft path)."""

    async def _run(self, *, selection_mode: bool):
        from types import SimpleNamespace

        from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
        from app.models.rfp import RfpRecord
        from app.services import proposal_section_editor as editor
        from app.services import proposal_pricing_service as pricing_service

        budget = new_budget()
        section = ProposalSection(
            id="s2",
            title="Budget & Pricing",
            content=svc.render_pricing_plan_budget(budget),
            mode="write",
        )
        draft = ProposalDraft(rfpId="r-acme", updatedAt="t", sections=[section])
        research = ProposalResearchCache(rfpId="r-acme", updatedAt="t", budget=budget)
        rfp = RfpRecord(
            id="r-acme",
            title="Acme",
            client="Acme",
            sector="government",
            source="manual",
            dueDate="2026-09-01",
            receivedDate="2026-08-01",
            lastActivity="2026-08-01",
            lastActivityNote="test",
        )

        edited_budget = new_budget()
        edit_mock = AsyncMock(return_value=(edited_budget, "Updated the plan."))

        content = section.content or ""
        selected = content.split("\n")[0] or content[:20]
        start = content.index(selected)
        sel_kwargs = (
            {
                "selection_start": start,
                "selection_end": start + len(selected),
                "selection_text": selected,
            }
            if selection_mode
            else {}
        )

        with (
            patch("app.services.llm.is_configured", return_value=True),
            patch.object(
                editor, "aload_rfp_for_proposal",
                new=AsyncMock(return_value=(rfp, SimpleNamespace(description="", pdf_text=""), "RFP text")),
            ),
            patch.object(editor, "aget_proposal_draft", new=AsyncMock(return_value=draft)),
            patch.object(editor, "aget_research_cache", new=AsyncMock(return_value=research)),
            patch.object(editor, "asave_research_cache", new=AsyncMock()),
            patch.object(
                pricing_service, "fetch_pricing_guide_context",
                new=AsyncMock(return_value=("", [])),
            ),
            patch.object(svc, "edit_pricing_plan_from_chat", edit_mock),
        ):
            result = await editor.improve_proposal_section(
                "r-acme",
                "s2",
                "In this passage change the fee to a flat rate",
                persist=False,
                **sel_kwargs,
            )
        return result, edit_mock, edited_budget, selected

    async def test_selection_edit_is_routed_to_the_plan_branch(self) -> None:
        (section, _draft, _research, _provider, reply, changed, _fix), edit_mock, edited, selected = (
            await self._run(selection_mode=True)
        )
        edit_mock.assert_awaited_once()
        instruction = edit_mock.await_args.kwargs["instruction"]
        self.assertIn(selected, instruction)
        self.assertIn("In this passage change the fee to a flat rate", instruction)
        self.assertTrue(changed)
        self.assertEqual(reply, "Updated the plan.")
        self.assertEqual(section.content, svc.render_pricing_plan_budget(edited))

    async def test_non_selection_edit_still_routes_to_the_plan_branch(self) -> None:
        (_section, _draft, _research, _provider, _reply, changed, _fix), edit_mock, _edited, _selected = (
            await self._run(selection_mode=False)
        )
        edit_mock.assert_awaited_once()
        self.assertEqual(
            edit_mock.await_args.kwargs["instruction"],
            "In this passage change the fee to a flat rate",
        )
        self.assertTrue(changed)


if __name__ == "__main__":
    unittest.main()
