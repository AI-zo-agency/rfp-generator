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
            "rfp_understanding",
            "opportunity_strategy",
            "delivery_pattern",
            "delivery_parallel",
            "work_breakdown",
            "dynamic_section",
            "winning_pattern",
            "section_strategy",
            "retrieval_planner",
            "validate",
            "derive_legacy",
        ):
            self.assertIn(expected, node_ids)


if __name__ == "__main__":
    unittest.main()
