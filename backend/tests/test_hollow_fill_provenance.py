"""07_FIN finalist bids must never reach the writer as delivered work.

06_WON is work zö won and delivered; 07_FIN is a bid zö LOST. Citing a finalist
bid as past performance is a fabricated claim in a submitted proposal, so the
label is applied at merge time from the retrieved FILENAMES — not left to a
"prefer 06_WON" line in a prompt that a model may ignore.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services import proposal_hollow_kb_fill as M

_FIN_MARK = "[FINALIST BID — NOT DELIVERED WORK"


def _patches(hits: dict[str, tuple[str, list[str]]]):
    async def _search(query, limit=4, max_chars=0):
        return hits.get(query, ("", []))

    # Both are imported INSIDE _retrieve_queries, so they must be patched at
    # their source modules, not as attributes of proposal_hollow_kb_fill.
    return (
        patch("app.services.supermemory.is_configured", return_value=True),
        patch(
            "app.services.proposal_knowledge_base_tools.search_knowledge_base",
            new=AsyncMock(side_effect=_search),
        ),
    )


class FinalistProvenanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_finalist_sourced_block_is_labelled(self):
        p1, p2 = _patches(
            {"06_WON refs": ("We delivered the Northglenn rebrand.", ["07_FIN_Northglenn.pdf"])}
        )
        with p1, p2:
            evidence, sources = await M._retrieve_queries(["06_WON refs"])
        self.assertIn(_FIN_MARK, evidence)
        self.assertIn("07_FIN_Northglenn.pdf", evidence)
        self.assertIn("07_FIN_Northglenn.pdf", sources)

    async def test_won_sourced_block_is_not_labelled(self):
        p1, p2 = _patches(
            {"06_WON refs": ("Maricopa County campaign delivered.", ["06_WON_Maricopa.pdf"])}
        )
        with p1, p2:
            evidence, _ = await M._retrieve_queries(["06_WON refs"])
        self.assertNotIn(_FIN_MARK, evidence)
        self.assertIn("Maricopa County campaign delivered.", evidence)

    async def test_mixed_batch_labels_only_the_finalist_block(self):
        p1, p2 = _patches(
            {
                "06_WON a": ("Won work text.", ["06_WON_A.pdf"]),
                "06_WON b": ("Finalist work text.", ["07_FIN_B.pdf"]),
            }
        )
        with p1, p2:
            evidence, _ = await M._retrieve_queries(["06_WON a", "06_WON b"])
        won_block, fin_block = evidence.split("### KB query: 06_WON b")
        self.assertNotIn(_FIN_MARK, won_block)
        self.assertIn(_FIN_MARK, fin_block)

    def test_no_query_pulls_finalist_bids_as_delivery_evidence(self):
        # The retrieval queries must ask for 06_WON only — pairing 07_FIN into
        # the same query made lost bids look interchangeable with won work.
        src = (M.__file__ or "").replace(".pyc", ".py")
        with open(src, encoding="utf-8") as fh:
            body = fh.read()
        self.assertNotIn("06_WON 07_FIN", body)


if __name__ == "__main__":
    unittest.main()
