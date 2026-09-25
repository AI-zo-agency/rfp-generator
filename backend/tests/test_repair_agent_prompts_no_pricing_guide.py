"""Repair/revise agent prompts must not send agents to rebuild pricing from the guide.

v2's pricing plan owns the Cost section (rendered on persist); SURGICAL_FIX and
USER_REVISE patch section prose, they never rebuild fee dollars from
00_Guide_Pricing / search_pricing_guide.
"""

from __future__ import annotations

import unittest

from app.services.proposal_langchain_agents import (
    SURGICAL_FIX_SYSTEM,
    USER_REVISE_SYSTEM,
    QUERY_PLANNER_SYSTEM,
)
from app.services.proposal_budget_playbook import BUDGET_TOOL_ROUTING


class NoPricingGuideInRepairPromptsTests(unittest.TestCase):
    def test_surgical_fix_prompt_drops_pricing_guide(self) -> None:
        self.assertNotIn("search_pricing_guide", SURGICAL_FIX_SYSTEM)

    def test_user_revise_prompt_drops_pricing_guide(self) -> None:
        self.assertNotIn("search_pricing_guide", USER_REVISE_SYSTEM)
        self.assertNotIn("00_Guide_Pricing", USER_REVISE_SYSTEM)

    def test_query_planner_prompt_drops_tier_rebuild_guidance(self) -> None:
        self.assertNotIn("rebuilding rates from the guide", QUERY_PLANNER_SYSTEM)

    def test_budget_tool_routing_drops_tier_picking(self) -> None:
        self.assertNotIn("search_pricing_guide", BUDGET_TOOL_ROUTING)
        self.assertNotIn("Pick ONE tier", BUDGET_TOOL_ROUTING)

    def test_repair_json_agents_never_register_pricing_guide_tool(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        import app.services.proposal_langchain_agents as agents_mod

        captured: dict = {}

        def fake_build_proposal_tools(*args, **kwargs):
            captured.update(kwargs)
            return []

        with patch.object(
            agents_mod, "build_proposal_tools", side_effect=fake_build_proposal_tools
        ), patch.object(
            agents_mod,
            "run_tool_agent_loop",
            new=AsyncMock(return_value=('{"content":"x"}', "openrouter", [])),
        ):
            asyncio.run(
                agents_mod.run_tool_json_agent(
                    role=agents_mod.AgentRole.USER_REVISE,
                    rfp_id="r1",
                    title="RFP",
                    client="Client",
                    user_content="change the budget pricing tier",
                    section_title="Budget & Pricing",
                    user_message="rebuild fees from the pricing guide",
                )
            )
        self.assertFalse(captured.get("include_pricing_guide"))


class BudgetRepairContextDropsPricingGuideTests(unittest.TestCase):
    """build_budget_repair_context must not fetch/inject 00_Guide_Pricing text —
    v2's pricing plan owns Cost section fees; nothing prices from the guide."""

    def test_repair_context_never_calls_fetch_pricing_guide_context(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from app.models.rfp import RfpRecord
        from app.services.proposal_budget_playbook import build_budget_repair_context

        rfp = RfpRecord(
            id="r1",
            title="T",
            client="C",
            dueDate="2026-09-01",
            receivedDate="2026-08-01",
            lastActivity="2026-08-05",
            lastActivityNote="n",
        )

        import app.services.proposal_pricing_service as pricing_service

        fail_if_called = AsyncMock(
            side_effect=AssertionError("fetch_pricing_guide_context must not be called")
        )
        with patch.object(
            pricing_service, "fetch_pricing_guide_context", new=fail_if_called
        ):
            context = asyncio.run(
                build_budget_repair_context(
                    rfp=rfp,
                    rfp_text="Cost proposal required. Submit itemized budget.",
                    research=None,
                    user_message="rebuild fees from the pricing guide",
                )
            )

        fail_if_called.assert_not_awaited()
        # The instructional playbook block may still mention "00_Guide_Pricing"
        # by name (e.g. "labor rows when present") — what must be gone is the
        # fetched guide document itself, injected under this header.
        self.assertNotIn("=== 00_Guide_Pricing ===", context)


if __name__ == "__main__":
    unittest.main()
