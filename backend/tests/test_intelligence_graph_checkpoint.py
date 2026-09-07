"""Unit tests for Phase 2 intelligence-graph per-node checkpointing.

Pure unit tests — no network/LLM calls. `aget_research_cache` / `asave_research_cache`
are patched at the module path the checkpoint helpers import them from
(`app.services.proposal_repository`), matching the deferred-import pattern the
helpers use to avoid an import cycle.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalResearchCache
from app.services.proposal_intelligence import graph as graph_mod
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan


def _make_cache(rfp_id: str, checkpoint: dict | None) -> ProposalResearchCache:
    return ProposalResearchCache(
        rfp_id=rfp_id,
        updated_at="2026-01-01T00:00:00+00:00",
        intelligence_checkpoint=checkpoint,
    )


class ContextFingerprintTests(unittest.TestCase):
    def test_differs_across_different_text(self) -> None:
        a = graph_mod._context_fingerprint("rfp text one")
        b = graph_mod._context_fingerprint("rfp text two")
        self.assertNotEqual(a, b)

    def test_stable_for_same_text(self) -> None:
        a = graph_mod._context_fingerprint("same rfp text")
        b = graph_mod._context_fingerprint("same rfp text")
        self.assertEqual(a, b)


class LoadIntelligenceCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_fingerprint_mismatch_returns_empty(self) -> None:
        cache = _make_cache(
            "rfp-1",
            {
                "fingerprint": "old-fingerprint",
                "completedNodes": ["opportunity_extract"],
                "plan": {"rfpId": "rfp-1"},
                "updatedAt": "2026-01-01T00:00:00+00:00",
            },
        )
        with patch(
            "app.services.proposal_repository.aget_research_cache",
            new=AsyncMock(return_value=cache),
        ):
            plan, completed = await graph_mod._load_intelligence_checkpoint(
                "rfp-1", "new-fingerprint"
            )
        self.assertIsNone(plan)
        self.assertEqual(completed, [])

    async def test_fingerprint_match_returns_saved_state(self) -> None:
        saved_plan = {"rfpId": "rfp-1", "opportunity": {}}
        cache = _make_cache(
            "rfp-1",
            {
                "fingerprint": "fp-match",
                "completedNodes": ["opportunity_extract", "strategy_delivery"],
                "plan": saved_plan,
                "updatedAt": "2026-01-01T00:00:00+00:00",
            },
        )
        with patch(
            "app.services.proposal_repository.aget_research_cache",
            new=AsyncMock(return_value=cache),
        ):
            plan, completed = await graph_mod._load_intelligence_checkpoint(
                "rfp-1", "fp-match"
            )
        self.assertEqual(plan, saved_plan)
        self.assertEqual(completed, ["opportunity_extract", "strategy_delivery"])

    async def test_no_cache_returns_empty(self) -> None:
        with patch(
            "app.services.proposal_repository.aget_research_cache",
            new=AsyncMock(return_value=None),
        ):
            plan, completed = await graph_mod._load_intelligence_checkpoint(
                "rfp-1", "any-fingerprint"
            )
        self.assertIsNone(plan)
        self.assertEqual(completed, [])

    async def test_store_failure_degrades_to_no_checkpoint(self) -> None:
        with patch(
            "app.services.proposal_repository.aget_research_cache",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            plan, completed = await graph_mod._load_intelligence_checkpoint(
                "rfp-1", "fp"
            )
        self.assertIsNone(plan)
        self.assertEqual(completed, [])


class WrapCheckpointBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_node_skips_fn_call(self) -> None:
        fn = AsyncMock()
        node = graph_mod._wrap("opportunity_extract", fn)
        state: graph_mod.IntelligenceGraphState = {
            "rfp_id": "rfp-1",
            "rfp_context": "some rfp text",
            "plan": ProposalExecutionPlan(rfpId="rfp-1").model_dump(by_alias=True),
            "completed_nodes": ["opportunity_extract"],
        }
        result = await node(state)
        fn.assert_not_awaited()
        self.assertEqual(result, {})

    async def test_pending_node_calls_fn_and_records_completion(self) -> None:
        returned_plan = ProposalExecutionPlan(rfpId="rfp-1")
        fn = AsyncMock(return_value=returned_plan)
        node = graph_mod._wrap("opportunity_extract", fn)
        state: graph_mod.IntelligenceGraphState = {
            "rfp_id": "rfp-1",
            "rfp_context": "some rfp text",
            "plan": ProposalExecutionPlan(rfpId="rfp-1").model_dump(by_alias=True),
            "completed_nodes": [],
        }
        with patch.object(
            graph_mod, "_save_intelligence_checkpoint", new=AsyncMock()
        ) as save_mock:
            result = await node(state)
        fn.assert_awaited_once()
        self.assertEqual(result["completed_nodes"], ["opportunity_extract"])
        save_mock.assert_awaited_once()

    async def test_fn_raises_normal_exception_not_marked_completed(self) -> None:
        fn = AsyncMock(side_effect=RuntimeError("boom"))
        node = graph_mod._wrap("strategy_delivery", fn)
        state: graph_mod.IntelligenceGraphState = {
            "rfp_id": "rfp-1",
            "rfp_context": "some rfp text",
            "plan": ProposalExecutionPlan(rfpId="rfp-1").model_dump(by_alias=True),
            "completed_nodes": [],
        }
        with patch.object(
            graph_mod, "_save_intelligence_checkpoint", new=AsyncMock()
        ) as save_mock:
            result = await node(state)
        fn.assert_awaited_once()
        self.assertNotIn("completed_nodes", result)
        save_mock.assert_not_awaited()

    async def test_save_failure_does_not_propagate(self) -> None:
        returned_plan = ProposalExecutionPlan(rfpId="rfp-1")
        fn = AsyncMock(return_value=returned_plan)
        node = graph_mod._wrap("opportunity_extract", fn)
        state: graph_mod.IntelligenceGraphState = {
            "rfp_id": "rfp-1",
            "rfp_context": "some rfp text",
            "plan": ProposalExecutionPlan(rfpId="rfp-1").model_dump(by_alias=True),
            "completed_nodes": [],
        }
        with patch(
            "app.services.proposal_repository.aget_research_cache",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.services.proposal_repository.asave_research_cache",
            new=AsyncMock(side_effect=RuntimeError("write failed")),
        ):
            result = await node(state)
        # Node itself must not raise; the plan/completion is still returned.
        self.assertEqual(result["completed_nodes"], ["opportunity_extract"])


class WrapProgressActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_node_emits_activity_with_label_and_step(self) -> None:
        returned_plan = ProposalExecutionPlan(rfpId="rfp-1")
        fn = AsyncMock(return_value=returned_plan)
        node = graph_mod._wrap("strategy_delivery", fn)
        state: graph_mod.IntelligenceGraphState = {
            "rfp_id": "rfp-1",
            "rfp_context": "some rfp text",
            "plan": ProposalExecutionPlan(rfpId="rfp-1").model_dump(by_alias=True),
            "completed_nodes": [],
        }
        with patch.object(
            graph_mod, "_save_intelligence_checkpoint", new=AsyncMock()
        ), patch(
            "app.services.proposal_pipeline_checkpoint.record_pipeline_activity",
            new=AsyncMock(),
        ) as activity_mock:
            await node(state)
        activity_mock.assert_awaited_once_with(
            "rfp-1",
            label="Shaping strategy & delivery",
            detail=None,
            step_index=2,
            step_total=len(graph_mod.INTELLIGENCE_NODE_LABELS),
            in_progress_phase="phase-2",
        )

    async def test_skipped_node_emits_no_activity(self) -> None:
        fn = AsyncMock()
        node = graph_mod._wrap("opportunity_extract", fn)
        state: graph_mod.IntelligenceGraphState = {
            "rfp_id": "rfp-1",
            "rfp_context": "some rfp text",
            "plan": ProposalExecutionPlan(rfpId="rfp-1").model_dump(by_alias=True),
            "completed_nodes": ["opportunity_extract"],
        }
        with patch(
            "app.services.proposal_pipeline_checkpoint.record_pipeline_activity",
            new=AsyncMock(),
        ) as activity_mock:
            await node(state)
        activity_mock.assert_not_awaited()
        fn.assert_not_awaited()

    async def test_activity_failure_does_not_break_node(self) -> None:
        returned_plan = ProposalExecutionPlan(rfpId="rfp-1")
        fn = AsyncMock(return_value=returned_plan)
        node = graph_mod._wrap("opportunity_extract", fn)
        state: graph_mod.IntelligenceGraphState = {
            "rfp_id": "rfp-1",
            "rfp_context": "some rfp text",
            "plan": ProposalExecutionPlan(rfpId="rfp-1").model_dump(by_alias=True),
            "completed_nodes": [],
        }
        with patch.object(
            graph_mod, "_save_intelligence_checkpoint", new=AsyncMock()
        ), patch(
            "app.services.proposal_pipeline_checkpoint.record_pipeline_activity",
            new=AsyncMock(side_effect=RuntimeError("progress store down")),
        ):
            result = await node(state)
        fn.assert_awaited_once()
        self.assertEqual(result["completed_nodes"], ["opportunity_extract"])


if __name__ == "__main__":
    unittest.main()
