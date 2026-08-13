"""Fair KB query budget — every requirement gets a search, not just the first N.

Live failure mode: 24 planned queries filled a 16-query cap, so discipline /
platform searches never ran and Technical Capability scored as if evidence
were absent. Selection must work for ANY RFP shape — no named clients/people.
"""

from __future__ import annotations

import unittest

from app.services.go_no_go_requirements import RfpRequirement
from app.services.go_no_go_service import (
    _enrich_requirements_with_role_queries,
    _select_kb_queries,
)


class QuerySelectionTests(unittest.TestCase):
    def test_role_queries_are_reserved_before_planner_fill(self) -> None:
        reqs = [
            RfpRequirement(
                requirement=f"Capability item {i}",
                isCore=True,
                kbQueries=[f"planner filler query number {i} aaa"],
            )
            for i in range(30)
        ]
        selected = _select_kb_queries(
            requirements=reqs,
            rfp_sample=(
                "Website redesign with WordPress CMS, UX design, content migration, "
                "hosting SLA, and WCAG accessibility."
            ),
            extras=[f"extra filler {i}" for i in range(20)],
            max_queries=20,
            reserved_role=6,
        )
        blob = " | ".join(selected).casefold()
        self.assertTrue(
            any(token in blob for token in ("wordpress", "web developer", "cms")),
            selected,
        )
        self.assertLessEqual(len(selected), 20)

    def test_round_robin_covers_late_requirements(self) -> None:
        reqs = [
            RfpRequirement(
                requirement="Early scope item",
                isCore=True,
                kbQueries=["early unique query alpha"],
            ),
            RfpRequirement(
                requirement="Mid scope item",
                isCore=True,
                kbQueries=["mid unique query beta"],
            ),
            RfpRequirement(
                requirement="Late GIS mapping integration",
                isCore=True,
                kbQueries=["late unique gis query omega"],
            ),
        ]
        selected = _select_kb_queries(
            requirements=reqs,
            rfp_sample="branding only",
            extras=[],
            max_queries=3,
            reserved_role=0,
        )
        blob = " | ".join(selected).casefold()
        self.assertIn("early unique", blob)
        self.assertIn("mid unique", blob)
        self.assertIn("late unique", blob)

    def test_enrich_adds_discipline_search_without_named_people(self) -> None:
        reqs = [
            RfpRequirement(
                requirement="WordPress CMS implementation",
                isCore=True,
                kbQueries=["model wrote something vague"],
            )
        ]
        enriched = _enrich_requirements_with_role_queries(reqs)
        blob = " | ".join(enriched[0].kb_queries).casefold()
        self.assertIn("wordpress", blob)
        self.assertIn("04_bio", blob)
        self.assertNotIn("shawn", blob)
        self.assertNotIn("torrent", blob)


if __name__ == "__main__":
    unittest.main()
