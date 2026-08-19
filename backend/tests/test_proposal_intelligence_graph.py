import unittest
from unittest.mock import AsyncMock, patch

from app.services.proposal_intelligence.graph import _build_graph


class IntelligenceGraphTests(unittest.TestCase):
    def test_graph_builds(self) -> None:
        g = _build_graph()
        self.assertIsNotNone(g)

    def test_graph_has_expected_node_names(self) -> None:
        # Compile and inspect via get_graph if available
        compiled = _build_graph()
        graph = compiled.get_graph()
        node_ids = set(graph.nodes.keys())
        for expected in (
            "opportunity_extract",
            "strategy_delivery",
            "execution_plan",
            "dynamic_section",
            "writing_briefs",
            "validate",
            "derive_legacy",
        ):
            self.assertIn(expected, node_ids)
        for retired in (
            "rfp_understanding",
            "delivery_parallel",
            "winning_pattern",
            "section_strategy",
            "retrieval_planner",
        ):
            self.assertNotIn(retired, node_ids)


if __name__ == "__main__":
    unittest.main()
