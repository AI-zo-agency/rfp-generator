"""Pricing plan engine (methodology v2): cost build-up, rule-based prices, guardrails, client-safe render."""

from __future__ import annotations

import copy
import json
import unittest
from datetime import date
from pathlib import Path

from app.services.pricing_kb import PricingBook, build_book
from app.services.pricing_plan_engine import (
    budgets_from_asks,
    billing_terms_name,
    blended_rate,
    compute,
    internal_summary,
    margins,
    media_fee,
    price_plan,
    render,
    term_value,
    verify_asks,
    verify_plan,
    wording_text,
)

FIX = Path(__file__).parent / "fixtures" / "pricing_v2"
BOOK = build_book(*[(FIX / n).read_text() for n in ("01_Pricing_Book.md", "02_Rules_and_Wording.md", "03_Pricing_Internal.md")])
TODAY = date(2026, 10, 1)
ASKS = {"priced_scope": [{"id": "S1"}, {"id": "S2"}], "ceilings": [], "asks": []}


def good_plan() -> dict:
    return {
        "engagement_type": "fixed_quote", "client_kind": "private", "client_name": "Acme Remodeling", "term_months": 12,
        "tasks": [
            {"task_id": "A1", "group": "Brand", "deliverable": "Silver brand book", "scope_ids": ["S1"],
             "billing": "one_time", "catalog_code": "1b", "catalog_item": "Silver Brand Book", "quantity": 1},
            {"task_id": "A2", "group": "Web", "deliverable": "Two landing pages", "scope_ids": ["S2"],
             "billing": "one_time", "quantity": 1,
             "build": {"hours": {"DS": 20, "WD": 30}, "pos": {"Creative Director": 150, "Copywriter": 400},
                       "hard_cost": 0, "basis": "task library: website, two pages"}},
        ],
        "overhead": {"hours": {"PM": 8, "AM": 8, "LD": 3}, "basis": "management for a 3-month project"},
        "fills": {"midpoint milestone": "design approval", "outside items": "ad spend, printing and new photo shoots"},
        "form_fills": [], "internal_notes": [],
        "sections": [{"heading": "Investment", "body_md": (
            "{{TASK_TABLE}}\n\nThe project total is {{TOTAL}}.\n\n{{VERBATIM:billing}}\n\n"
            "{{VERBATIM:outside}}\n\n{{VERBATIM:changes}}")}],
    }


def priced(plan: dict, budgets: dict | None = None) -> tuple[dict, dict]:
    report = price_plan(plan, BOOK, budgets or {})
    return plan, report


def check(plan: dict, asks: dict = ASKS, budgets: dict | None = None, report: dict | None = None, **kw):
    return verify_plan(plan, asks, BOOK, budgets=budgets or {}, report=report, today=kw.pop("today", TODAY), **kw)


class Parity(unittest.TestCase):
    """The client's own workbook script on its Gilroy example is the oracle for cost, hours and floor."""

    @classmethod
    def setUpClass(cls) -> None:
        g = json.loads((FIX / "gilroy_plan.json").read_text())
        cls.client = json.loads((FIX / "gilroy_client_summary.json").read_text())
        tasks = []
        for i, t in enumerate(g["tasks"]):
            n = len(t["periods"])  # hours and POs in the example are per active period
            tasks.append({
                "task_id": f"G{i}", "group": t["pillar"], "deliverable": t["task"], "billing": "one_time",
                "scope_ids": ["S1"],
                "build": {"hours": {k: v * n for k, v in t["hours"].items()},
                          "pos": {k: v * n for k, v in t["po"].items()}, "hard_cost": t["hard"] * n, "basis": "example"},
            })
        cls.plan = {"engagement_type": "monthly_retainer", "client_kind": "nonprofit", "term_months": 10, "tasks": tasks}
        price_plan(cls.plan, BOOK, {})
        cls.c = compute(cls.plan, BOOK)
        cls.m = margins(cls.c)

    def test_total_cost_and_hours_match_the_client(self) -> None:
        self.assertAlmostEqual(self.m["total"]["cost"], self.client["total_cost"], places=2)
        self.assertAlmostEqual(self.m["total"]["hours"], self.client["in_house_hours"], places=2)

    def test_cost_by_pillar_matches_the_client(self) -> None:
        for p in self.client["pillars"]:
            self.assertAlmostEqual(self.m["groups"][p["name"]]["cost"], p["cost"], places=2, msg=p["name"])

    def test_floor_price_matches_the_client(self) -> None:
        for p in self.client["pillars"]:
            ours = self.m["groups"][p["name"]]["cost"] / BOOK.settings.cost_ratio
            self.assertAlmostEqual(ours, p["floor_price_input_unit"] * 10, delta=0.5, msg=p["name"])

    def test_clients_own_prices_hold_the_floor_in_our_margin_math(self) -> None:
        total_margin = 1 - self.client["total_cost"] / self.client["total_price"]
        self.assertAlmostEqual(total_margin, self.client["margin_pct"], places=3)


class CatalogAndCustomPricing(unittest.TestCase):
    def test_catalog_item_sells_at_its_book_price(self) -> None:
        plan, _ = priced(good_plan())
        c = compute(plan, BOOK)
        self.assertEqual(c["amounts"]["A1"], 4800)
        self.assertEqual(c["cost_term"]["A1"], 1520)  # all-in cost from the catalog costs table

    def test_custom_price_without_a_budget_starts_near_cost_over_0_40(self) -> None:
        plan, _ = priced(good_plan())
        c = compute(plan, BOOK)
        cost = c["cost_term"]["A2"]  # includes the management overhead share
        self.assertAlmostEqual(c["multiples"]["__total__"], 2.5)
        self.assertAlmostEqual(c["amounts"]["A2"], cost * 2.5, delta=100)
        self.assertGreaterEqual(c["amounts"]["A2"], cost / 0.47)

    def test_budget_with_room_prices_at_the_target(self) -> None:
        plan, report = priced(good_plan(), {"__total__": 100_000})
        self.assertEqual(report["no_fit"], {})
        self.assertAlmostEqual(compute(plan, BOOK)["multiples"]["__total__"], 3.7)

    def test_over_budget_comes_down_to_about_90_percent(self) -> None:
        plan, report = priced(good_plan(), {"__total__": 24_000})
        c = compute(plan, BOOK)
        self.assertEqual(report["no_fit"], {})
        self.assertAlmostEqual(term_value(c), 21_600, delta=200)
        self.assertLess(compute(plan, BOOK)["multiples"]["__total__"], 3.7)

    def test_the_floor_stops_the_price_coming_down(self) -> None:
        plan, report = priced(good_plan(), {"__total__": 20_000})
        c = compute(plan, BOOK)
        self.assertEqual(report["no_fit"], {})  # the floor total still fits under the budget
        self.assertLessEqual(term_value(c), 20_000)
        self.assertAlmostEqual(c["multiples"]["__total__"], 1 / 0.47, places=3)
        self.assertGreaterEqual(margins(c)["groups"]["Web"]["margin"], 0.53 - 1e-9)

    def test_scope_that_cannot_fit_is_reported_with_the_floor(self) -> None:
        plan, report = priced(good_plan(), {"__total__": 9_000})
        info = report["no_fit"]["__total__"]
        self.assertGreater(info["floor_total"], 9_000)
        errs, _ = check(plan, budgets={"__total__": 9_000}, report=report)
        self.assertTrue(any("does not fit the budget" in e for e in errs))

    def test_management_hours_are_recovered_in_custom_prices(self) -> None:
        without = good_plan()
        without["overhead"]["hours"] = {"PM": 0, "AM": 0, "LD": 0}
        a, _ = priced(without)
        b, _ = priced(good_plan())
        self.assertGreater(compute(b, BOOK)["amounts"]["A2"], compute(a, BOOK)["amounts"]["A2"])

    def test_monthly_catalog_item_counts_twelve_months(self) -> None:
        plan = good_plan()
        plan["tasks"] = [{"task_id": "A1", "group": "SEO", "deliverable": "SEO", "scope_ids": ["S1", "S2"], "billing": "monthly",
                          "catalog_code": "7c", "catalog_item": "SEO Basic", "quantity": 1}]
        plan["overhead"] = {"hours": {"PM": 1, "AM": 1, "LD": 1}, "basis": "x"}
        priced(plan)
        c = compute(plan, BOOK)
        self.assertEqual(c["amounts"]["A1"], 2100)
        self.assertEqual(term_value(c), 25_200)

    def test_per_event_fees_are_rates_never_summed(self) -> None:
        plan = good_plan()
        plan["tasks"][1]["billing"] = "per_event"
        priced(plan)
        c = compute(plan, BOOK)
        self.assertNotIn("A2", c["cost_term"])
        self.assertEqual(term_value(c), 4800)

    def test_blended_rate_and_negotiated_client(self) -> None:
        self.assertEqual(blended_rate({"client_name": "Acme"}, BOOK), 275)
        self.assertEqual(blended_rate({"client_name": "City of Bend, Oregon"}, BOOK), 250)

    def test_media_fees(self) -> None:
        self.assertEqual(media_fee(BOOK, 5_000), 2_000)  # 20% is $1,000, the $2,000 minimum applies
        self.assertEqual(media_fee(BOOK, 12_000), 2_160)  # 18% in the $10,000 to $15,000 band
        self.assertEqual(media_fee(BOOK, 250_000), 25_000)  # the top band, 10%

    def test_traditional_media_counts_the_whole_budget_but_earns_the_commission(self) -> None:
        plan = good_plan()
        plan["tasks"].append({"task_id": "A3", "group": "Media", "deliverable": "Radio and print", "scope_ids": ["S2"],
                              "billing": "one_time", "quantity": 1, "media_kind": "traditional", "media_spend": 100_000,
                              "build": {"hours": {"AM": 10}, "pos": {}, "hard_cost": 0, "basis": "placement"}})
        priced(plan)
        c = compute(plan, BOOK)
        self.assertEqual(c["amounts"]["A3"], 100_000)
        self.assertEqual(c["revenue"]["A3"], 15_000)
        self.assertEqual(term_value(c, None, True), 100_000)

    def test_snapshot_reprices_identically_without_the_kb(self) -> None:
        plan, _ = priced(good_plan(), {"__total__": 20_000})
        snap = BOOK.snapshot({"1b"})
        again = PricingBook.from_snapshot(json.loads(json.dumps(snap)))
        a, b = compute(plan, BOOK), compute(plan, again)
        self.assertEqual(a["amounts"], b["amounts"])
        self.assertEqual(render(plan, ASKS, BOOK), render(plan, ASKS, again))


class Guardrails(unittest.TestCase):
    def test_a_good_plan_passes(self) -> None:
        plan, report = priced(good_plan())
        errs, warns = check(plan, report=report)
        self.assertEqual(errs, [])
        self.assertTrue(any("unanchored" in w for w in warns))

    def test_catalog_code_must_match_the_item_name(self) -> None:
        plan = good_plan()
        plan["tasks"][0]["catalog_item"] = "Gold Brand Book"
        errs, _ = check(priced(plan)[0])
        self.assertTrue(any("1b" in e and "Silver Brand Book" in e for e in errs))

    def test_unknown_catalog_code(self) -> None:
        plan = good_plan()
        plan["tasks"][0]["catalog_code"] = "99z"
        errs, _ = check(plan)
        self.assertTrue(any("99z" in e for e in errs))

    def test_management_hours_are_required(self) -> None:
        plan = good_plan()
        plan["overhead"] = {"hours": {"PM": 5}, "basis": "x"}
        errs, _ = check(priced(plan)[0])
        self.assertTrue(any("account management" in e and "AM" in e for e in errs))

    def test_custom_build_needs_a_basis_and_known_roles(self) -> None:
        plan = good_plan()
        plan["tasks"][1]["build"]["basis"] = ""
        plan["tasks"][1]["build"]["hours"]["Designer"] = 5
        errs, _ = check(plan)
        self.assertTrue(any("basis" in e for e in errs))
        self.assertTrue(any("Designer" in e for e in errs))

    def test_price_per_in_house_hour_floor(self) -> None:
        plan = good_plan()
        plan["tasks"][1]["build"] = {"hours": {"DS": 200}, "pos": {}, "hard_cost": 0, "basis": "x"}
        plan, report = priced(plan, {"__total__": 12_000})
        errs, _ = check(plan, budgets={"__total__": 12_000}, report=report)
        self.assertTrue(any("per in-house hour" in e or "floor" in e for e in errs))

    def test_quantity_needs_a_basis(self) -> None:
        plan = good_plan()
        plan["tasks"][0]["quantity"] = 3
        errs, _ = check(plan)
        self.assertTrue(any("quantity_basis" in e for e in errs))
        plan["tasks"][0].update(quantity_basis="assumption")
        errs, warns = check(priced(plan)[0])
        self.assertEqual([e for e in errs if "quantity" in e], [])
        self.assertTrue(any("assumption" in w for w in warns))

    def test_quantity_quote_must_be_in_the_rfp(self) -> None:
        plan = good_plan()
        plan["tasks"][0].update(quantity=3, quantity_basis="rfp", quantity_quote="three brand books")
        errs, _ = check(plan, rfp_text="The city wants one brand book.")
        self.assertTrue(any("quantity_quote" in e for e in errs))
        errs, _ = check(priced(plan)[0], rfp_text="The city wants three brand books.")
        self.assertEqual([e for e in errs if "quantity" in e], [])

    def test_prose_never_carries_dollars_hours_or_internals(self) -> None:
        for bad in ("It costs $5,000.", "Our margin is healthy.", "Curt Schultz leads it.", "Billed at an hour rate."):
            plan = good_plan()
            plan["sections"][0]["body_md"] += "\n\n" + bad
            errs, _ = check(priced(plan)[0])
            self.assertTrue(errs, bad)

    def test_a_client_named_morgan_is_fine(self) -> None:
        plan = good_plan()
        plan["sections"][0]["body_md"] += "\n\nWe look forward to serving Morgan County."
        errs, _ = check(priced(plan)[0])
        self.assertEqual(errs, [])

    def test_staffing_table_token_is_refused(self) -> None:
        plan = good_plan()
        plan["sections"][0]["body_md"] += "\n\n{{STAFFING_TABLE}}"
        errs, _ = check(priced(plan)[0])
        self.assertTrue(any("STAFFING_TABLE" in e for e in errs))

    def test_required_wording_and_unfilled_slots(self) -> None:
        plan = good_plan()
        del plan["fills"]["midpoint milestone"]
        errs, _ = check(priced(plan)[0])
        self.assertTrue(any("midpoint milestone" in e for e in errs))
        plan = good_plan()
        plan["sections"][0]["body_md"] = "{{TASK_TABLE}}"
        errs, _ = check(priced(plan)[0])
        self.assertEqual(sum("missing {{VERBATIM" in e for e in errs), 3)

    def test_below_floor_catalog_item_is_flagged_not_blocked_during_the_hold(self) -> None:
        plan = good_plan()
        plan["tasks"][0].update(catalog_code="2a", catalog_item="Logo: Bronze")
        plan, report = priced(plan)
        errs, warns = check(plan, report=report)
        self.assertTrue(any("2a" in w and "flag for Sonja" in w for w in warns))
        self.assertEqual([e for e in errs if "2a" in e], [])
        errs, _ = check(plan, today=date(2027, 1, 2))
        self.assertTrue(any("2a" in e and "hold has ended" in e for e in errs))

    def test_over_a_printed_budget_is_an_error(self) -> None:
        plan, report = priced(good_plan(), {"__total__": 100_000})
        plan["tasks"][0]["catalog_code"] = "1d"
        plan["tasks"][0]["catalog_item"] = "Platinum Brand Book"
        errs, _ = check(plan, budgets={"__total__": 12_000}, report={"no_fit": {}})
        self.assertTrue(any("exceeds the budget" in e for e in errs))

    def test_asks_need_a_client_kind(self) -> None:
        errs = verify_asks({"priced_scope": [{"id": "S1"}], "ceilings": [], "asks": [], "client": {"kind": "city"}}, "rfp")
        self.assertTrue(any("client.kind" in e for e in errs))


class ClientSafeRender(unittest.TestCase):
    def setUp(self) -> None:
        self.plan, _ = priced(good_plan())
        self.out = render(self.plan, ASKS, BOOK)

    def test_shows_prices_and_a_total(self) -> None:
        self.assertIn("$4,800", self.out)
        self.assertIn("| | **Total** |", self.out)

    def test_never_shows_hours_costs_margins_or_roles(self) -> None:
        # the approved wording may say "$275 an hour" for change orders, so only internal terms are checked
        for leak in ("hours", "margin", "cost", "loaded", "Creative Director", "PM", "1,520", "Overhead", "in-house"):
            self.assertNotIn(leak, self.out, leak)

    def test_wording_comes_from_the_rules_doc_with_slots_filled(self) -> None:
        self.assertIn("25% at design approval", self.out)
        self.assertIn("Outside the quote: ad spend, printing and new photo shoots", self.out)

    def test_rates_asked_for_are_the_blended_rate_for_every_role(self) -> None:
        plan = good_plan()
        plan["hourly_roles"] = ["Designer", "Developer"]
        plan["sections"][0]["body_md"] += "\n\n{{RATE_TABLE}}"
        out = render(priced(plan)[0], ASKS, BOOK)
        self.assertIn("| Designer | $275 |", out)
        self.assertIn("| Developer | $275 |", out)

    def test_buyer_form_hours_cells_stay_blank_for_sonja(self) -> None:
        asks = {**ASKS, "buyer_form": {"name": "Cost Form", "columns": ["HOURS", "PRICE"], "rows": [
            {"row_id": "R1", "label": "Design", "unit": "EA"}]}}
        plan = good_plan()
        plan["form_fills"] = [{"row_id": "R1", "column": "HOURS", "kind": "hours", "task_ids": ["A2"]},
                              {"row_id": "R1", "column": "PRICE", "kind": "task_price", "task_ids": ["A2"]}]
        plan["sections"][0]["body_md"] += "\n\n{{FORM}}"
        plan, _ = priced(plan)
        out = render(plan, asks, BOOK)
        self.assertIn("[MANUAL FILL]", out)
        errs, warns = check(plan, asks)
        self.assertEqual(errs, [])
        self.assertTrue(any("publishes prices only" in w for w in warns))

    def test_government_billing_replaces_the_deposit_lines(self) -> None:
        plan = good_plan()
        plan["client_kind"] = "government"
        text = wording_text(plan, BOOK, "billing")
        self.assertIn("last day of each month", text)
        self.assertNotIn("due on signing", text)
        self.assertEqual(billing_terms_name(plan), "Government Monthly")

    def test_internal_summary_holds_the_numbers_the_client_never_sees(self) -> None:
        s = internal_summary(self.plan, BOOK)
        self.assertIn("INTERNAL — DO NOT PLACE", s)
        self.assertIn("All-in cost", s)
        self.assertIn("nonprofit option (12% off)", s)

    def test_budgets_from_asks(self) -> None:
        asks = {"ceilings": [{"label": "Total", "amount": 120_000}, {"label": "Pool", "amount": 5_000_000, "shared_pool": True}]}
        self.assertEqual(budgets_from_asks(asks), {"__total__": 120_000})
        self.assertEqual(budgets_from_asks({"ceilings": []}, 50_000), {"__total__": 50_000})
        self.assertEqual(budgets_from_asks({"ceilings": []}), {})


if __name__ == "__main__":
    unittest.main()
