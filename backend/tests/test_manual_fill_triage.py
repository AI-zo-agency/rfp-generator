"""Manual-fill triage: criticality must be earned with a real RFP citation.

Replaces `_rfp_mandates_placeholder_ask`, which decided whether a placeholder mattered
using nine hardcoded `re.search` branches (FEIN, insurance, e-verify, affidavit, bond,
W-9, references, percent-time, NTE) and, for every other topic, "do two tokens of length
>= 4 appear in the RFP text". Here an agent reads the actual RFP, and its quote is
verified by string containment — the one mechanical check that cannot go stale.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ManualFillFlag
from app.services.proposal_manual_fill_triage import (
    quote_appears_in_rfp,
    triage_manual_fill_flags,
)

RFP_TEXT = """SECTION 4 — SUBMISSION REQUIREMENTS
4.1 Proposals must include three client references.
4.2 Bids submitted without a bid bond will be rejected as non-responsive.
4.3 Vendors are encouraged to include team photographs.
"""


def _flag(tag: str, *, section_id: str = "s1") -> ManualFillFlag:
    return ManualFillFlag(
        sectionId=section_id,
        sectionTitle="Submission Forms",
        kind="manual_fill",
        tag=tag,
    )


class QuoteContainmentTests(unittest.TestCase):
    def test_exact_quote_found(self):
        assert quote_appears_in_rfp(
            "Bids submitted without a bid bond will be rejected", RFP_TEXT
        )

    def test_whitespace_and_case_differences_still_match(self):
        """Models re-wrap and re-case quotes; that is not fabrication."""
        assert quote_appears_in_rfp(
            "bids submitted   without a bid bond\nwill be rejected", RFP_TEXT
        )

    def test_invented_quote_not_found(self):
        assert not quote_appears_in_rfp(
            "Bids without a performance bond are disqualified", RFP_TEXT
        )

    def test_empty_quote_is_not_a_citation(self):
        assert not quote_appears_in_rfp("", RFP_TEXT)
        assert not quote_appears_in_rfp("   ", RFP_TEXT)

    def test_no_rfp_text_cannot_verify_anything(self):
        assert not quote_appears_in_rfp("anything at all", "")


class TriageTests(unittest.IsolatedAsyncioTestCase):
    async def _triage(self, verdicts: list[dict], flags: list[ManualFillFlag]):
        with patch(
            "app.services.proposal_manual_fill_triage._classify_flags",
            new=AsyncMock(return_value=verdicts),
        ):
            return await triage_manual_fill_flags(
                flags=flags, rfp_text=RFP_TEXT, rfp_client="Acme", rfp_title="RFP 1"
            )

    async def test_disqualifying_with_real_citation_is_kept(self):
        out = await self._triage(
            [
                {
                    "tag": "bid bond",
                    "criticality": "disqualifying",
                    "rfpEvidence": "Bids submitted without a bid bond will be rejected",
                    "whyRequired": "The RFP rejects bids with no bond.",
                    "ifSkipped": "Bid rejected unopened.",
                }
            ],
            [_flag("bid bond")],
        )
        self.assertEqual(out[0].criticality, "disqualifying")
        self.assertIn("bid bond will be rejected", out[0].rfp_evidence)

    async def test_disqualifying_with_invented_citation_is_downgraded(self):
        """The core rule: urgency needs a source, same as any other claim."""
        out = await self._triage(
            [
                {
                    "tag": "performance bond",
                    "criticality": "disqualifying",
                    "rfpEvidence": "Bids without a performance bond are disqualified",
                    "whyRequired": "Invented.",
                    "ifSkipped": "Invented.",
                }
            ],
            [_flag("performance bond")],
        )
        self.assertEqual(out[0].criticality, "scored")

    async def test_disqualifying_with_no_citation_is_downgraded(self):
        out = await self._triage(
            [{"tag": "bid bond", "criticality": "disqualifying", "rfpEvidence": ""}],
            [_flag("bid bond")],
        )
        self.assertEqual(out[0].criticality, "scored")

    async def test_scored_does_not_require_a_citation(self):
        """Only disqualifying carries the citation requirement."""
        out = await self._triage(
            [{"tag": "references", "criticality": "scored", "rfpEvidence": ""}],
            [_flag("references")],
        )
        self.assertEqual(out[0].criticality, "scored")

    async def test_optional_is_marked_not_dropped_here(self):
        """Removal is existing step 13's job; triage only labels."""
        out = await self._triage(
            [{"tag": "team photographs", "criticality": "optional"}],
            [_flag("team photographs")],
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].criticality, "optional")

    async def test_flag_missing_from_verdicts_defaults_to_scored(self):
        """Never silently drop a flag the agent forgot to classify."""
        out = await self._triage([], [_flag("bid bond")])
        self.assertEqual(out[0].criticality, "scored")

    async def test_unknown_criticality_value_falls_back_to_scored(self):
        out = await self._triage(
            [{"tag": "bid bond", "criticality": "catastrophic"}], [_flag("bid bond")]
        )
        self.assertEqual(out[0].criticality, "scored")

    async def test_agent_failure_leaves_every_flag_scored(self):
        """Best-effort: a triage outage must not drop submission gaps."""
        with patch(
            "app.services.proposal_manual_fill_triage._classify_flags",
            new=AsyncMock(side_effect=RuntimeError("provider down")),
        ):
            out = await triage_manual_fill_flags(
                flags=[_flag("bid bond"), _flag("references")],
                rfp_text=RFP_TEXT,
                rfp_client="Acme",
                rfp_title="RFP 1",
            )
        self.assertEqual(len(out), 2)
        self.assertTrue(all(f.criticality == "scored" for f in out))

    async def test_no_rfp_text_cannot_produce_disqualifying(self):
        """With nothing to cite, no citation can be verified."""
        with patch(
            "app.services.proposal_manual_fill_triage._classify_flags",
            new=AsyncMock(
                return_value=[
                    {
                        "tag": "bid bond",
                        "criticality": "disqualifying",
                        "rfpEvidence": "Bids submitted without a bid bond will be rejected",
                    }
                ]
            ),
        ):
            out = await triage_manual_fill_flags(
                flags=[_flag("bid bond")], rfp_text="", rfp_client="", rfp_title=""
            )
        self.assertEqual(out[0].criticality, "scored")

    async def test_empty_flag_list_short_circuits(self):
        classify = AsyncMock(return_value=[])
        with patch(
            "app.services.proposal_manual_fill_triage._classify_flags", new=classify
        ):
            out = await triage_manual_fill_flags(
                flags=[], rfp_text=RFP_TEXT, rfp_client="", rfp_title=""
            )
        self.assertEqual(out, [])
        classify.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
