"""Slim outline helper for demo section-list panel."""

from __future__ import annotations

import unittest

import server


class SlimOutlineTests(unittest.TestCase):
    def test_nests_children_by_id(self) -> None:
        plan = {
            "writing": {
                "proposalOutline": {
                    "sections": [
                        {
                            "id": "a",
                            "title": "Tab A",
                            "order": 1,
                            "evaluationWeight": 10,
                            "children": ["a1"],
                        },
                        {
                            "id": "a1",
                            "title": "Sub",
                            "order": 2,
                            "parentId": "a",
                            "children": [],
                        },
                        {"id": "b", "title": "Tab B", "order": 3, "children": []},
                    ]
                }
            }
        }
        out = server._slim_outline_sections(plan)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["title"], "Tab A")
        self.assertEqual(out[0]["children"][0]["title"], "Sub")
        self.assertEqual(out[0]["evaluationWeight"], 10)
        self.assertEqual(server._count_outline_nodes(out), 3)


if __name__ == "__main__":
    unittest.main()
