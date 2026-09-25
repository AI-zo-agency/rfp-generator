"""Pricing plan engine: KB parse, tier, checks, render (no LLM)."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from app.services.pricing_plan_engine import (
    decide_tier,
    parse_guide,
    parse_labor,
    render,
    verify_asks,
    verify_plan,
)

FIX = Path(__file__).parent / "fixtures" / "pricing_plan"


def _json(name: str) -> dict:
    return json.loads((FIX / name).read_text())


GUIDE = parse_guide((FIX / "guide.md").read_text())
LABOR = parse_labor((FIX / "labor.md").read_text())
ALL_VERBATIM = " ".join(
    "{{VERBATIM:%s}}" % k for k in ("investment_framing", "scope_protection", "reimbursables", "revisions")
)


def _mini_plan(**task_overrides) -> dict:
    task = {
        "task_id": "A1", "group": "Social", "deliverable": "Social package x12", "track": "P1",
        "billing": "one_time", "guide_id": "4.5", "quantity": 12, "unit_price": None, "scope_ids": ["S1"],
        "staffing": [{"role": "Copywriter", "hours": 80}, {"role": "Art Director", "hours": 60}],
    }
    task.update(task_overrides)
    return {
        "tasks": [task],
        "form_fills": [],
        "sections": [{"heading": "1. Cost", "body_md": "{{TASK_TABLE}}\n" + ALL_VERBATIM}],
        "internal_notes": [],
    }


MINI_ASKS = {
    "ceilings": [{"label": "P1", "amount": 50000, "scope": "annual", "track": "P1"}],
    "priced_scope": [{"id": "S1", "item": "social"}],
    "tier_facts": {"client_scale": "city"},
}


class KbParseTests(unittest.TestCase):
    def test_every_menu_line_has_three_bands_except_media(self) -> None:
        self.assertGreaterEqual(len(GUIDE["items"]), 40)
        missing = [k for k, v in GUIDE["items"].items() if len(v["tiers"]) != 3]
        self.assertEqual(missing, ["6.1"])
        self.assertEqual(GUIDE["items"]["4.5"]["tiers"]["Average"], (2900.0, 3800.0))

    def test_all_verbatim_blocks_found(self) -> None:
        self.assertEqual(
            set(GUIDE["verbatim"]),
            {"investment_framing", "scope_protection", "reimbursables", "revisions", "media"},
        )

    def test_labor_billable_rates(self) -> None:
        self.assertEqual(LABOR["Agency Director"], 400.0)
        self.assertEqual(LABOR["Account Manager"], 275.0)
        self.assertEqual(len(LABOR), 10)


class TierTests(unittest.TestCase):
    def test_cost_weight_25_forces_low(self) -> None:
        self.assertEqual(decide_tier({"cost_weight_pct": 30})[0], "Low")

    def test_state_agency_with_two_complexity_signals_is_high(self) -> None:
        asks = {"tier_facts": {
            "client_scale": "state_agency",
            "multicultural_or_bilingual": {"value": True},
            "statewide_or_regional": {"value": True},
        }}
        self.assertEqual(decide_tier(asks)[0], "High")

    def test_default_is_average(self) -> None:
        self.assertEqual(decide_tier({"cost_weight_pct": 15, "tier_facts": {"client_scale": "large_county"}})[0], "Average")

    def test_small_county_straightforward_is_low(self) -> None:
        self.assertEqual(decide_tier({"tier_facts": {"client_scale": "small_county"}})[0], "Low")


class VerifyTests(unittest.TestCase):
    def test_988_golden_plan_passes(self) -> None:
        errs, _ = verify_plan(_json("988_plan.json"), _json("988_asks.json"), GUIDE, LABOR)
        self.assertEqual(errs, [])

    def test_988_asks_quotes_are_verbatim(self) -> None:
        self.assertEqual(verify_asks(_json("988_asks.json"), (FIX / "988_rfp.txt").read_text()), [])

    def test_invented_quote_rejected(self) -> None:
        asks = _json("988_asks.json")
        asks["asks"][0]["quote"] = "Contractor shall bill a flat monthly retainer of any amount"
        errs = verify_asks(asks, (FIX / "988_rfp.txt").read_text())
        self.assertTrue(any("quote not found" in e for e in errs))

    def test_dupage_form_must_tie_out_to_track_tasks(self) -> None:
        errs, _ = verify_plan(_json("dupage_plan.json"), _json("dupage_asks.json"), GUIDE, LABOR)
        self.assertTrue(any("must tie out" in e for e in errs), errs)

    def test_price_outside_band_rejected(self) -> None:
        plan = _mini_plan(staffing=[{"role": "Copywriter", "hours": 1}])
        errs, _ = verify_plan(plan, MINI_ASKS, GUIDE, LABOR)
        self.assertTrue(any("outside 4.5 Average band" in e for e in errs), errs)

    def test_off_card_role_rejected(self) -> None:
        plan = _mini_plan(staffing=[{"role": "Wizard", "hours": 100}])
        errs, _ = verify_plan(plan, MINI_ASKS, GUIDE, LABOR)
        self.assertTrue(any("not on Labor Cost card" in e for e in errs), errs)

    def test_literal_dollar_in_prose_rejected(self) -> None:
        plan = _mini_plan()
        plan["sections"][0]["body_md"] += "\nOnly $5,000 per month."
        errs, _ = verify_plan(plan, MINI_ASKS, GUIDE, LABOR)
        self.assertIn("prose contains a literal dollar figure — use tokens", errs)

    def test_all_inclusive_rfp_forbids_reimbursables(self) -> None:
        asks = {**MINI_ASKS, "all_inclusive_pricing": {"value": True}}
        errs, _ = verify_plan(_mini_plan(), asks, GUIDE, LABOR)
        self.assertTrue(any("all-inclusive" in e for e in errs), errs)

    def test_own_ceiling_must_be_65_to_85_percent_used(self) -> None:
        asks = {**MINI_ASKS, "ceilings": [{"label": "P1", "amount": 200000, "scope": "annual", "track": "P1"}]}
        errs, _ = verify_plan(_mini_plan(), asks, GUIDE, LABOR)
        self.assertTrue(any("must be 65-85%" in e for e in errs), errs)

    def test_target_budget_anchors_when_no_ceiling(self) -> None:
        asks = {**MINI_ASKS, "ceilings": []}
        errs, _ = verify_plan(_mini_plan(track=None), asks, GUIDE, LABOR, target_budget_usd=100000)
        self.assertTrue(any("target budget" in e for e in errs), errs)
        errs, _ = verify_plan(_mini_plan(track=None), asks, GUIDE, LABOR, target_budget_usd=38500)
        self.assertEqual(errs, [])

    def test_no_ceiling_no_target_warns_unanchored(self) -> None:
        _, warns = verify_plan(_json("newport_plan.json"), _json("newport_asks.json"), GUIDE, LABOR)
        self.assertTrue(any(w.startswith("unanchored") for w in warns), warns)


class RenderTests(unittest.TestCase):
    def test_988_render_has_totals_verbatim_and_no_tokens(self) -> None:
        md = render(_json("988_plan.json"), _json("988_asks.json"), GUIDE, LABOR)
        self.assertNotIn("{{", md)
        self.assertIn("$717,650", md)
        self.assertIn("**Total not-to-exceed** | **$950,000**", md)
        for key in ("investment_framing", "scope_protection", "revisions"):
            self.assertIn(GUIDE["verbatim"][key][:60], md)
        self.assertIn("**INTERNAL — DO NOT PLACE**", md)

    def test_form_row_ties_out(self) -> None:
        plan = _mini_plan()
        plan["form_fills"] = [
            {"row_id": "R1", "column": col, "kind": kind, "task_ids": ["A1"]}
            for col, kind in (("QTY", "hours"), ("PRICE", "rate"), ("EXTENDED PRICE", "extended"))
        ]
        plan["sections"][0]["body_md"] += "\n{{FORM}}"
        asks = {**MINI_ASKS, "buyer_form": {
            "name": "Form", "columns": ["QTY", "PRICE", "EXTENDED PRICE"],
            "rows": [{"row_id": "R1", "label": "Hourly Rate P1", "unit": "HR", "track": "P1"}],
        }}
        self.assertEqual(verify_plan(plan, asks, GUIDE, LABOR)[0], [])
        self.assertIn("| Hourly Rate P1 | HR | 140 | $275 | $38,500 |", render(plan, asks, GUIDE, LABOR))


if __name__ == "__main__":
    unittest.main()
