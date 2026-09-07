"""Pricing guide tool must not run for narrative / non-fee topics."""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from app.services.proposal_langchain import (
    _pricing_guide_topic_is_about_fees,
    build_proposal_tools,
)


class PricingGuideTopicGateTests(unittest.TestCase):
    def test_partnership_outreach_is_not_fees(self) -> None:
        self.assertFalse(
            _pricing_guide_topic_is_about_fees(
                "Regional Partnership Development outreach"
            )
        )

    def test_tier_ask_is_fees(self) -> None:
        self.assertTrue(
            _pricing_guide_topic_is_about_fees("Average tier PM floor discovery")
        )

    def test_tools_omit_pricing_when_disabled(self) -> None:
        tools = build_proposal_tools(
            "r1", "RFP", "Client", include_pricing_guide=False
        )
        names = {t.name for t in tools}
        self.assertNotIn("search_pricing_guide", names)
        self.assertIn("search_rfp_requirements", names)

    def test_pricing_tool_skips_narrative_topic(self) -> None:
        tools = build_proposal_tools(
            "r1", "RFP", "Client", include_pricing_guide=True
        )
        pricing = next(t for t in tools if t.name == "search_pricing_guide")
        with mock.patch(
            "app.services.proposal_knowledge_base_tools.search_knowledge_base",
            new_callable=mock.AsyncMock,
        ) as search:
            out = asyncio.run(
                pricing.coroutine(
                    topic="Regional Partnership Development outreach"
                )
            )
        self.assertIn("skipped", out.casefold())
        search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
