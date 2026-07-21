"""Tests for KB fact checker (no live Supermemory)."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalResearchCache, ProposalSection, RfpSectionMap
from app.services.proposal_kb_fact_checker import (
    _dedupe_section3_case_studies,
    _eval_percent_claimed_without_rfp,
    _kb_query_for_section,
    _reject_destructive_fact_check_rewrite,
    _requirements_for_section,
    _resolve_mapped_section,
    _rfp_excerpt_for_section,
    _section3_client_key,
    _should_run_requirement_agent,
    _split_bio_subsections,
)
from types import SimpleNamespace


class EvalPercentTests(unittest.TestCase):
    def test_flags_percent_in_eval_context_not_in_rfp(self) -> None:
        text = (
            "The evaluation framework tells us DEQ values most: "
            "Technical Approach (30%), Vendor Experience (25%), and Cost is 15%."
        )
        rfp = "Best Value ranking: Vendor Experience, Marketing Plan, Pricing listed last."
        bad = _eval_percent_claimed_without_rfp(text, rfp)
        self.assertIn("30%", bad)
        self.assertIn("25%", bad)

    def test_allows_percent_present_in_rfp(self) -> None:
        text = "Cost factor is 15% of the total score."
        rfp = "Cost factor (15%) shall be evaluated as follows."
        self.assertEqual(_eval_percent_claimed_without_rfp(text, rfp), [])


class Section3DedupeTests(unittest.TestCase):
    def test_client_key_from_title(self) -> None:
        self.assertIn(
            "umatilla",
            _section3_client_key(
                ProposalSection(id="x", title="3.2 — City of Umatilla Digital Campaign 2006", content="")
            ),
        )

    def test_dedupe_keeps_longer_case_study(self) -> None:
        short = ProposalSection(
            id="section-3-work-a",
            title="3.1 — Umatilla Rock the Locks",
            content="VIP tickets sold out.",
        )
        long = ProposalSection(
            id="section-3-work-b",
            title="3.2 — City of Umatilla",
            content="VIP tickets sold out in days. " * 20,
        )
        other = ProposalSection(id="section-1-1", title="Who We Are", content="We are zö.")
        merged, removed = _dedupe_section3_case_studies([other, short, long])
        self.assertEqual(removed, 1)
        umatilla = [
            s for s in merged if s.id.startswith("section-3-work-")
        ]
        self.assertEqual(len(umatilla), 1)
        self.assertGreater(len(umatilla[0].content or ""), len(short.content or ""))


class RequirementAgentHelpersTests(unittest.TestCase):
    def test_rfp_excerpt_prefers_matching_paragraphs(self) -> None:
        rfp = (
            "Unrelated intro about parking.\n\n"
            "Section 5.2.1 Specifications checklist Item 1 bilingual materials.\n\n"
            "Another unrelated footer."
        )
        excerpt = _rfp_excerpt_for_section(
            rfp,
            section_title="Specifications Compliance 5.2.1",
            requirements=["Answer checklist Item 1 bilingual"],
            max_chars=5000,
        )
        self.assertIn("5.2.1", excerpt)
        self.assertNotIn("parking", excerpt.casefold())

    def test_resolve_mapped_by_section_id(self) -> None:
        research = ProposalResearchCache(
            rfpId="x",
            rfpSections=[
                RfpSectionMap(
                    id="rfp-marketing-plan",
                    title="Marketing Plan",
                    requirements=["Include phased approach"],
                )
            ],
            updatedAt="2020-01-01",
        )
        section = ProposalSection(id="rfp-marketing-plan", title="Marketing Plan", content="")
        mapped = _resolve_mapped_section(section, research)
        self.assertIsNotNone(mapped)
        assert mapped is not None
        self.assertIn("phased", mapped.requirements[0])

    def test_should_run_agent_on_verify_and_specs(self) -> None:
        sec = ProposalSection(
            id="rfp-spec",
            title="Specifications Compliance — 5.2.1",
            content="ok",
        )
        self.assertTrue(_should_run_requirement_agent(sec, None, sec.content))
        self.assertTrue(
            _should_run_requirement_agent(
                ProposalSection(id="a", title="T", content="[VERIFY: insurance]"),
                None,
                "[VERIFY: insurance]",
            )
        )

    def test_skips_agent_on_substantive_cover_letter(self) -> None:
        letter = (
            "Dear Selection Committee,\n\n"
            "We are pleased to submit our proposal for the District's website redesign. "
            "Our team brings thirteen years of public-sector digital work and a dedicated "
            "account lead for day-to-day communication.\n\n"
            "Respectfully,\nRon Comer"
        )
        sec = ProposalSection(
            id="rfp-cover",
            title="Cover Letter",
            content=letter,
        )
        self.assertFalse(_should_run_requirement_agent(sec, None, letter))

    def test_rejects_stub_downgrade(self) -> None:
        prior = "Dear Committee,\n\n" + ("We are zö agency. " * 30)
        stub = (
            "[VERIFY: Draft content for Cover Letter — insufficient evidence in corpus. "
            "Requirements: Address Cover Letter per RFP]"
        )
        self.assertTrue(_reject_destructive_fact_check_rewrite(prior, stub))
        self.assertFalse(_reject_destructive_fact_check_rewrite(prior, prior))


class KbQueryBuilderTests(unittest.TestCase):
    def test_case_study_query_uses_03_cs_not_full_title(self) -> None:
        rfp = SimpleNamespace(client="NC Environmental Quality")
        q = _kb_query_for_section(
            ProposalSection(
                id="section-3-work-1",
                title="3.2 — City of Umatilla Digital Campaign 2006",
                content="long draft text that should not appear in query",
            ),
            rfp,
        )
        self.assertIn("03_CS", q)
        self.assertIn("Umatilla", q)
        self.assertNotIn("NC Environmental", q)


class BioSubsectionTests(unittest.TestCase):
    def test_split_bio_subsections(self) -> None:
        text = (
            "### Curt — Title\n\n"
            "**Work History**\n"
            "- [VERIFY: Work History]\n\n"
            "**Key Accounts**\n"
            "- Oregon Employment Department\n"
        )
        preamble, blocks = _split_bio_subsections(text)
        self.assertIn("Curt", preamble)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0][0], "Work History")
        self.assertIn("VERIFY", blocks[0][1])
        self.assertEqual(blocks[1][0], "Key Accounts")

    def test_bio_verify_uses_subsection_agent_not_full_section(self) -> None:
        sec = ProposalSection(
            id="section-2-bio-curt",
            title="2.1 — Curt Schultz",
            content="**Work History**\n- [VERIFY: Work History]",
        )
        self.assertFalse(_should_run_requirement_agent(sec, None, sec.content))


if __name__ == "__main__":
    unittest.main()
