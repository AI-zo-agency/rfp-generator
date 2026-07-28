"""Tests for LLM cost instrumentation (Part 1 — observability only)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.llm_call_context import llm_call_context
from app.services.llm_call_log import (
    format_cost_breakdown_log,
    get_run_cost_breakdown,
    record_llm_call,
)
from app.services.llm_pricing import estimate_cost_usd, resolve_model_price


class LlmPricingTests(unittest.TestCase):
    def test_sonnet_price_match(self) -> None:
        price = resolve_model_price("anthropic/claude-sonnet-4")
        self.assertEqual(price.input_per_mtok, 3.0)
        self.assertEqual(price.output_per_mtok, 15.0)

    def test_haiku_cheaper_than_sonnet(self) -> None:
        sonnet = estimate_cost_usd(
            model="anthropic/claude-sonnet-4",
            input_tokens=100_000,
            output_tokens=10_000,
        )
        haiku = estimate_cost_usd(
            model="anthropic/claude-haiku-4.5",
            input_tokens=100_000,
            output_tokens=10_000,
        )
        self.assertGreater(sonnet, haiku)


class LlmCallLogTests(unittest.TestCase):
    def test_record_and_breakdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            with patch("app.services.llm_call_log._use_supabase", return_value=False), patch(
                "app.services.rfp_repository._db_path", return_value=db_path
            ), patch(
                "app.services.rfp_repository._use_supabase", return_value=False
            ):
                run_id = "run-test-1"
                record_llm_call(
                    run_id=run_id,
                    rfp_id="rfp-1",
                    node_name="retrieval_planner",
                    model="anthropic/claude-sonnet-4",
                    tier="heavy",
                    provider="openrouter",
                    input_tokens=1000,
                    output_tokens=200,
                    cost_usd=0.006,
                    latency_ms=1200,
                )
                record_llm_call(
                    run_id=run_id,
                    rfp_id="rfp-1",
                    node_name="draft_sections:s1",
                    model="anthropic/claude-sonnet-4",
                    tier="heavy",
                    provider="openrouter",
                    input_tokens=5000,
                    output_tokens=2000,
                    cost_usd=0.045,
                    latency_ms=8000,
                )
                breakdown = get_run_cost_breakdown(run_id)
                self.assertEqual(breakdown["call_count"], 2)
                self.assertAlmostEqual(breakdown["total_cost_usd"], 0.051, places=5)
                self.assertEqual(breakdown["by_node"][0]["node_name"], "draft_sections:s1")
                text = format_cost_breakdown_log(breakdown)
                self.assertIn("total_usd=", text)
                self.assertIn("retrieval_planner", text)

    def test_context_defaults(self) -> None:
        with llm_call_context(rfp_id="r1", run_id="run-x", node_name="timeline"):
            from app.services.llm_call_context import (
                get_llm_node_name,
                get_llm_rfp_id,
                get_llm_run_id,
            )

            self.assertEqual(get_llm_rfp_id(), "r1")
            self.assertEqual(get_llm_run_id(), "run-x")
            self.assertEqual(get_llm_node_name(), "timeline")


if __name__ == "__main__":
    unittest.main()
