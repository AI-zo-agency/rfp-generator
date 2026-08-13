"""Evidence agent owns Supermemory query choice — no regex/person anchors."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.go_no_go_evidence_agent import (
    attribute_hits,
    build_evidence_digest,
    merge_queries_onto_requirements,
    parse_follow_ups,
    run_evidence_agent,
    select_initial_queries,
)
from app.services.go_no_go_requirements import RfpRequirement


def _req(name: str, *queries: str, core: bool = True) -> RfpRequirement:
    return RfpRequirement(requirement=name, isCore=core, kbQueries=list(queries))


class SelectInitialQueriesTests(unittest.TestCase):
    def test_round_robin_covers_late_requirements(self) -> None:
        reqs = [
            _req("Early", "early q"),
            _req("Mid", "mid q"),
            _req("Late", "late q"),
        ]
        selected = select_initial_queries(reqs, max_queries=3)
        blob = " | ".join(selected)
        self.assertIn("early q", blob)
        self.assertIn("mid q", blob)
        self.assertIn("late q", blob)


class FollowUpParseTests(unittest.TestCase):
    def test_parse_follow_ups_maps_to_known_requirements(self) -> None:
        follow = parse_follow_ups(
            {
                "followUps": [
                    {
                        "requirement": "CMS implementation",
                        "queries": ["zö agency CMS developer 04_Bio"],
                        "why": "no hits",
                    }
                ]
            },
            known_requirements={"CMS implementation", "Hosting"},
        )
        self.assertEqual(len(follow), 1)
        self.assertEqual(follow[0][0], "CMS implementation")
        self.assertIn("04_Bio", follow[0][1][0])

    def test_merge_appends_follow_up_queries(self) -> None:
        reqs = [_req("CMS implementation", "first q")]
        out = merge_queries_onto_requirements(
            reqs, [("CMS implementation", ["follow q bio"])]
        )
        self.assertEqual(out[0].kb_queries, ["first q", "follow q bio"])


class EvidenceAgentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_runs_follow_up_when_initial_hits_empty(self) -> None:
        reqs = [_req("WordPress CMS", "initial cms query")]

        async def search(query: str):
            if "follow" in query.casefold():
                return [
                    {
                        "title": "04_Bio_WebDev.pdf",
                        "content": "Specializes in WordPress. Built hundreds of sites.",
                    }
                ]
            return []

        with patch(
            "app.services.go_no_go_evidence_agent.llm.chat_json",
            new=AsyncMock(
                return_value=(
                    {
                        "followUps": [
                            {
                                "requirement": "WordPress CMS",
                                "queries": ["zö agency WordPress developer follow bio"],
                                "why": "empty",
                            }
                        ]
                    },
                    "test",
                )
            ),
        ):
            working, hits_by_req, all_hits, queries = await run_evidence_agent(
                rfp_id="rfp-1",
                rfp_title="Website RFP",
                rfp_excerpt="Need WordPress CMS",
                requirements=reqs,
                search=search,
            )

        self.assertTrue(any("follow" in q.casefold() for q in queries))
        self.assertEqual(len(hits_by_req["WordPress CMS"]), 1)
        self.assertIn("WordPress", all_hits[0]["content"])
        self.assertTrue(
            any("follow" in q.casefold() for q in working[0].kb_queries)
        )

    async def test_digest_and_attribution(self) -> None:
        reqs = [_req("UX design", "ux q")]
        by_query = {
            "ux q": [{"title": "03_CS_Site.pdf", "content": "Improved user flow"}]
        }
        hits = attribute_hits(reqs, by_query)
        digest = build_evidence_digest(reqs, hits)
        self.assertIn("UX design", digest)
        self.assertIn("03_CS_Site.pdf", digest)
        self.assertIn("user flow", digest)


if __name__ == "__main__":
    unittest.main()
