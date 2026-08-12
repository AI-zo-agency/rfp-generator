"""Fast proposal generation settings."""

from __future__ import annotations

import unittest
from unittest import mock

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_manuscript_digest import manuscript_content_hash


class ManuscriptDigestTests(unittest.TestCase):
    def test_hash_changes_when_content_changes(self) -> None:
        a = ProposalDraft(
            rfpId="r1",
            updatedAt="2026-01-01T00:00:00Z",
            sections=[
                ProposalSection(id="s1", title="A", content="hello"),
            ],
        )
        b = ProposalDraft(
            rfpId="r1",
            updatedAt="2026-01-01T00:00:00Z",
            sections=[
                ProposalSection(id="s1", title="A", content="hello world"),
            ],
        )
        self.assertNotEqual(manuscript_content_hash(a), manuscript_content_hash(b))

    def test_phase3_concurrency_sequential_by_default(self) -> None:
        from app.services.proposal_drafting_graph import _phase3_concurrency

        with mock.patch("app.core.config.settings") as settings:
            settings.fast_proposal_generation = False
            self.assertEqual(_phase3_concurrency(), 1)
            settings.fast_proposal_generation = True
            settings.phase3_llm_concurrency = 3
            self.assertEqual(_phase3_concurrency(), 3)


if __name__ == "__main__":
    unittest.main()
