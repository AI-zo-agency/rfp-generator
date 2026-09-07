"""Lean Review & Fix: rewrite only when needed, compact voice, low tokens."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalSection, RfpSectionMap
from app.services.proposal_brand_voice import format_brand_voice_block
from app.services.proposal_kb_fact_checker import _should_run_requirement_agent


class LeanReviewGateTests(unittest.TestCase):
    def test_grounded_section_skips_sonnet_rewrite(self) -> None:
        section = ProposalSection(
            id="rfp-structure-executive-summary",
            title="Executive Summary",
            content=(
                "We lock one production calendar before layout starts. "
                "Curt Schultz sets visual direction for issue one. "
                "We hand the City print-ready files on the agreed proof date. "
                "Our McMinnville library work used the same proof rhythm. "
            )
            * 3,
            status="generated",
            mode="write",
        )
        mapped = RfpSectionMap(
            id="rfp-structure-executive-summary",
            title="Executive Summary",
            requirements=["Summarize approach"],
            zoMode="write",
        )
        self.assertFalse(
            _should_run_requirement_agent(section, mapped, section.content or "")
        )

    def test_hollow_hourly_claim_triggers_rewrite(self) -> None:
        section = ProposalSection(
            id="rfp-structure-fee-proposal",
            title="Fee Proposal",
            content=(
                "This table answers the RFP's scored Cost / hourly-rate instrument. "
                "Total proposed investment: $89,400."
            ),
            status="generated",
            mode="write",
        )
        self.assertTrue(
            _should_run_requirement_agent(section, None, section.content or "")
        )

    def test_verify_tag_triggers_rewrite(self) -> None:
        section = ProposalSection(
            id="rfp-structure-fiscal-stability",
            title="Fiscal Stability",
            content="We are stable. [VERIFY: attach D&B report — Sonja]",
            status="generated",
            mode="write",
        )
        self.assertTrue(
            _should_run_requirement_agent(section, None, section.content or "")
        )

    def test_compact_voice_is_much_shorter_than_full(self) -> None:
        full = format_brand_voice_block({"tone": "direct"}, register="narrative")
        compact = format_brand_voice_block(
            {"tone": "direct"}, register="narrative", compact=True
        )
        self.assertIn("REV 6", compact)
        self.assertLess(len(compact), len(full) // 2)
        self.assertNotIn("INTERESTING PROPOSAL ANSWER", compact)

    def test_toc_never_triggers_requirement_rewrite(self) -> None:
        section = ProposalSection(
            id="rfp-structure-toc",
            title="2. Table of Contents",
            content="| Section | Page |\n| --- | --- |\n| Cover | 1 |",
            status="generated",
            mode="write",
        )
        mapped = RfpSectionMap(
            id="rfp-structure-toc",
            title="2. Table of Contents",
            requirements=["Include a table of contents"],
            uncovered_requirements=["Address all RFP requirements"],
            zoMode="write",
        )
        self.assertFalse(
            _should_run_requirement_agent(section, mapped, section.content or "")
        )


class PerSectionBlockerNoLlmTests(unittest.TestCase):
    def test_targeted_fix_defers_contradiction_to_end_only(self) -> None:
        """Review sections run in parallel; contradiction is one whole-proposal pass."""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "app/services/proposal_fulfill_rfp_gaps.py"
        ).read_text(encoding="utf-8")
        self.assertIn("Scanning the whole proposal for fact / RFP / budget", src)
        self.assertIn("batch_done_ids", src)
        # Must not reintroduce per-section LLM contradiction in the review loop.
        self.assertNotIn(
            "use_llm_contradiction=False",
            src.split("if targeted_fix_contradiction_is_done")[0],
        )


if __name__ == "__main__":
    unittest.main()
