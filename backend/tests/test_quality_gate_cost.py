"""Cost controls that must not cost accuracy.

Act 1 asked one LLM call per section — 24 calls on a 24-section draft to answer one
question. Batching cuts the call count without narrowing coverage: every section and its
evidence still reaches the model. Rounds 2+ only re-examine sections that changed, so
extra rounds stay cheap and MAX_ROUNDS can stay at 3.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import GateTicket, ProposalDraft, ProposalSection
from app.services import proposal_quality_gate as gate


def _draft(n: int, *, chars: int = 200) -> ProposalDraft:
    return ProposalDraft(
        rfpId="r1",
        sections=[
            ProposalSection(id=f"s{i}", title=f"Section {i}", content="x" * chars)
            for i in range(n)
        ],
        updatedAt="2026-08-13T00:00:00Z",
    )


class BatchingTests(unittest.TestCase):
    def test_small_sections_share_one_batch(self):
        batches = gate.batch_sections(_draft(6, chars=100).sections, limit=24_000)
        self.assertEqual(len(batches), 1)

    def test_batches_split_at_the_char_limit(self):
        batches = gate.batch_sections(_draft(6, chars=10_000).sections, limit=24_000)
        self.assertGreater(len(batches), 1)

    def test_every_section_appears_exactly_once(self):
        """Batching is a packing change, not a sampling change — no coverage is lost."""
        sections = _draft(25, chars=3_000).sections
        batched = [s.id for b in gate.batch_sections(sections, limit=24_000) for s in b]
        self.assertEqual(sorted(batched), sorted(s.id for s in sections))
        self.assertEqual(len(batched), len(set(batched)))

    def test_a_section_larger_than_the_limit_still_gets_its_own_batch(self):
        """Never silently dropped for being too big."""
        sections = _draft(1, chars=80_000).sections
        batches = gate.batch_sections(sections, limit=24_000)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 1)

    def test_empty_sections_are_skipped(self):
        sections = _draft(3, chars=0).sections
        self.assertEqual(gate.batch_sections(sections, limit=24_000), [])

    def test_no_sections_means_no_batches(self):
        self.assertEqual(gate.batch_sections([], limit=24_000), [])


class Act1CallCountTests(unittest.IsolatedAsyncioTestCase):
    async def test_twenty_four_sections_do_not_cost_twenty_four_calls(self):
        draft = _draft(24, chars=500)
        verifier = AsyncMock(return_value=({"claims": []}, "p"))
        with (
            patch(
                "app.services.proposal_section_kb_evidence.fetch_packed_section_kb_evidence",
                new=AsyncMock(return_value=("evidence", ["kb.md"])),
            ),
            patch(
                "app.services.proposal_langchain_agents.run_json_agent", new=verifier
            ),
        ):
            await gate.verify_fact_bound_claims(rfp_id="r1", draft=draft, research=None)
        self.assertLess(verifier.await_count, 24)
        self.assertGreater(verifier.await_count, 0)

    async def test_claims_are_attributed_to_the_right_section(self):
        """Batching must not blur which section a claim came from."""
        draft = _draft(2, chars=100)
        with (
            patch(
                "app.services.proposal_section_kb_evidence.fetch_packed_section_kb_evidence",
                new=AsyncMock(return_value=("evidence", ["kb.md"])),
            ),
            patch(
                "app.services.proposal_langchain_agents.run_json_agent",
                new=AsyncMock(
                    return_value=(
                        {"claims": [{"sectionId": "s1", "claim": "c", "status": "verified"}]},
                        "p",
                    )
                ),
            ),
        ):
            out = await gate.verify_fact_bound_claims(
                rfp_id="r1", draft=draft, research=None
            )
        self.assertTrue(out)
        self.assertEqual(out[0].section_id, "s1")

    async def test_retrieval_failure_still_yields_unresolved_not_silence(self):
        draft = _draft(1, chars=100)
        with (
            patch(
                "app.services.proposal_section_kb_evidence.fetch_packed_section_kb_evidence",
                new=AsyncMock(side_effect=RuntimeError("down")),
            ),
            patch(
                "app.services.proposal_langchain_agents.run_json_agent",
                new=AsyncMock(
                    return_value=(
                        {"claims": [{"sectionId": "s0", "claim": "c", "status": "contradicted"}]},
                        "p",
                    )
                ),
            ),
        ):
            out = await gate.verify_fact_bound_claims(
                rfp_id="r1", draft=draft, research=None
            )
        # No evidence was retrieved, so nothing can be contradicted by it.
        self.assertEqual(out[0].status, "unresolved")


class IncrementalDetectionTests(unittest.TestCase):
    def test_first_round_examines_everything(self):
        draft = _draft(4, chars=100)
        out = gate.sections_to_examine(draft, changed=None)
        self.assertEqual(len(out), 4)

    def test_later_rounds_only_examine_changed_sections(self):
        draft = _draft(4, chars=100)
        out = gate.sections_to_examine(draft, changed={"s1", "s2"})
        self.assertEqual({s.id for s in out}, {"s1", "s2"})

    def test_no_changes_means_nothing_to_re_examine(self):
        draft = _draft(4, chars=100)
        self.assertEqual(gate.sections_to_examine(draft, changed=set()), [])


class RoundBudgetTests(unittest.TestCase):
    def test_three_rounds_retained_for_accuracy(self):
        """Rounds stay cheap via incremental detection, so accuracy is not traded away."""
        self.assertEqual(gate.MAX_ROUNDS, 3)


if __name__ == "__main__":
    unittest.main()


class CrossSectionAccuracyTests(unittest.IsolatedAsyncioTestCase):
    """Scoping later rounds must not blind the cross-section detectors.

    Repetition and consistency compare sections against each other. If round 2 only
    shows them the sections that changed, a new duplicate between an edited section and
    an untouched one is invisible — a real accuracy loss traded for tokens. Only the
    per-section detector (slop) may be scoped.
    """

    async def _capture(self, only_sections):
        draft = _draft(3, chars=100)
        draft.sections[0].content = "UNIQUE_EDITED_MARKER"
        draft.sections[2].content = "UNIQUE_UNTOUCHED_MARKER"
        seen: dict[str, str] = {}

        async def _fake(role, user_content, key):
            seen[key] = user_content
            return []

        with patch.object(gate, "_run_detector", new=AsyncMock(side_effect=_fake)):
            await gate.detect_quality_tickets(
                draft=draft, scorecard=[], only_sections=only_sections
            )
        return seen

    async def test_repetition_sees_untouched_sections_in_later_rounds(self):
        seen = await self._capture({"s0"})
        self.assertIn("UNIQUE_UNTOUCHED_MARKER", seen["repeats"])

    async def test_consistency_sees_untouched_sections_in_later_rounds(self):
        seen = await self._capture({"s0"})
        self.assertIn("UNIQUE_UNTOUCHED_MARKER", seen["conflicts"])

    async def test_slop_is_scoped_to_changed_sections(self):
        """Slop is per-section, so scoping it costs nothing in accuracy."""
        seen = await self._capture({"s0"})
        self.assertIn("UNIQUE_EDITED_MARKER", seen["findings"])
        self.assertNotIn("UNIQUE_UNTOUCHED_MARKER", seen["findings"])

    async def test_round_one_sends_everything_to_every_detector(self):
        seen = await self._capture(None)
        for key in ("repeats", "conflicts", "findings"):
            self.assertIn("UNIQUE_UNTOUCHED_MARKER", seen[key])
