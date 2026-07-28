"""Lean outline hygiene — enrich titles; keep important + closing tabs."""

from __future__ import annotations

import unittest

from app.services.proposal_outline_dedup import (
    filter_lean_outline_sections,
    is_generic_filler_outline_title,
    is_important_or_closing_outline_title,
    is_pricing_outline_title,
    merge_closing_components_into_outline,
    outline_titles_near_duplicate,
)


class OutlineDedupTests(unittest.TestCase):
    def test_near_duplicate_approach_methodology(self) -> None:
        self.assertTrue(
            outline_titles_near_duplicate(
                "Technical Approach",
                "Technical Approach & Methodology",
            )
        )

    def test_agency_requirements_siblings_deferred_to_collapse(self) -> None:
        # Near-dup returns False so collapse can see every G.# title.
        self.assertFalse(
            outline_titles_near_duplicate(
                "Agency Requirements — Collateral Materials (G.6)",
                "Agency Requirements — Formative Research (G.1)",
            )
        )

    def test_generic_filler_titles(self) -> None:
        self.assertTrue(is_generic_filler_outline_title("Executive Summary"))
        self.assertTrue(is_generic_filler_outline_title("Understanding of the Project"))
        self.assertFalse(is_generic_filler_outline_title("References"))
        self.assertFalse(
            is_generic_filler_outline_title("Sample Work Portfolio — Public Awareness")
        )
        self.assertTrue(is_important_or_closing_outline_title("Offeror Commitment & Closing Statement"))

    def test_filter_drops_static_and_near_dups(self) -> None:
        sections = [
            {"id": "a", "title": "Company History, Core Services, and Client Roster", "required": True},
            {"id": "b", "title": "Public Awareness Campaign Approach", "required": True},
            {"id": "c", "title": "Public Awareness Campaign Approach & Methodology", "required": True},
            {"id": "d", "title": "Fee Schedule", "required": True},
        ]
        kept, dropped = filter_lean_outline_sections(
            sections,
            rfp_context="public awareness campaign approach fee schedule",
        )
        titles = [s["title"] for s in kept]
        self.assertNotIn("Company History, Core Services, and Client Roster", titles)
        self.assertEqual(len([t for t in titles if "Approach" in t]), 1)
        self.assertTrue(any("Fee Schedule" in t for t in titles))
        self.assertTrue(any("Company History" in d or "near-duplicate" in d for d in dropped))

    def test_generic_filler_kept_when_named_in_rfp(self) -> None:
        sections = [
            {"id": "a", "title": "Executive Summary", "required": True},
            {"id": "b", "title": "Pricing Proposal Form", "required": True},
        ]
        rfp = "Submit an Executive Summary and the Pricing Proposal Form."
        kept, _dropped = filter_lean_outline_sections(sections, rfp_context=rfp)
        titles = [s["title"] for s in kept]
        self.assertIn("Executive Summary", titles)
        self.assertIn("Pricing Proposal Form", titles)

    def test_generic_filler_dropped_without_rfp_mention(self) -> None:
        sections = [
            {"id": "a", "title": "Executive Summary", "required": True},
            {"id": "b", "title": "Scope of Work — Media Buy", "required": True},
        ]
        kept, dropped = filter_lean_outline_sections(
            sections,
            rfp_context="scope of work media buy deliverables only",
        )
        titles = [s["title"] for s in kept]
        self.assertNotIn("Executive Summary", titles)
        self.assertIn("Scope of Work — Media Buy", titles)
        self.assertTrue(any("generic filler" in d for d in dropped))

    def test_assembler_mode_keeps_rfp_named_generics(self) -> None:
        sections = [
            {"id": "a", "title": "Executive Summary", "required": True},
            {"id": "b", "title": "Executive Summary (duplicate)", "required": False},
        ]
        kept, dropped = filter_lean_outline_sections(
            sections,
            rfp_context="",
            drop_generic_filler=False,
        )
        self.assertEqual(len(kept), 1)
        self.assertTrue(any("near-duplicate" in d for d in dropped))

    def test_price_enriched_from_rfp_heading(self) -> None:
        sections = [
            {"id": "a", "title": "Price", "required": True},
            {"id": "b", "title": "Sample Work Submission (Portfolio)", "required": True},
        ]
        rfp = (
            "Proposal Contents\n"
            "4.2 Cost Proposal / Fee Schedule — Labor Category Rates\n"
            "4.3 Sample Work Submission (Portfolio)\n"
        )
        kept, dropped = filter_lean_outline_sections(sections, rfp_context=rfp)
        titles = [s["title"] for s in kept]
        self.assertTrue(
            any("Cost Proposal" in t or "Fee Schedule" in t or "Labor Category" in t for t in titles),
            msg=titles,
        )
        self.assertFalse(any(d.startswith("Price") and "generic" in d for d in dropped))

    def test_qualifications_kept_and_enriched_not_regex_dropped(self) -> None:
        """Do not kill Qualifications via static regex — enrich to the RFP heading."""
        sections = [
            {"id": "a", "title": "Qualifications and Experience", "required": True},
            {"id": "b", "title": "Agency Requirements — Media Planning", "required": True},
        ]
        rfp = (
            "4.1 Qualifications and Experience of the Firm and Key Personnel\n"
            "4.4 Agency Requirements — Media Planning\n"
            "References\n"
            "Offeror Closing Statement\n"
        )
        kept, _dropped = filter_lean_outline_sections(sections, rfp_context=rfp)
        titles = [s["title"] for s in kept]
        self.assertTrue(
            any("Qualifications" in t and "Key Personnel" in t for t in titles),
            msg=titles,
        )
        self.assertTrue(any("Agency Requirements" in t for t in titles))

    def test_collapses_many_agency_requirements_g_tabs(self) -> None:
        sections = [
            {"id": f"g{i}", "title": f"Agency Requirements — Service (G.{i})", "required": True}
            for i in range(1, 17)
        ] + [
            {"id": "price", "title": "Price", "required": True},
            {
                "id": "pricing",
                "title": "Proposal Pricing — Hourly Rates by Labor Category (Line Items Tab)",
                "required": True,
            },
            {
                "id": "sample",
                "title": "Sample Work Submission (Portfolio) — Minimum Two Recent Campaigns",
                "required": True,
            },
        ]
        kept, dropped = filter_lean_outline_sections(
            sections,
            rfp_context="agency requirements G.1 formative research through G.16 innovation proposal pricing hourly rates",
        )
        titles = [s["title"] for s in kept]
        agency_tabs = [t for t in titles if "Agency Requirements" in t]
        self.assertEqual(len(agency_tabs), 1, msg=titles)
        self.assertIn("Capability Matrix", agency_tabs[0])
        self.assertIn("G.1", agency_tabs[0])
        self.assertIn("G.16", agency_tabs[0])
        pricing_tabs = [
            t for t in titles if is_pricing_outline_title(t) or "Price" in t or "Pricing" in t
        ]
        self.assertEqual(len(pricing_tabs), 1, msg=titles)
        self.assertIn("Hourly Rates", pricing_tabs[0])
        self.assertTrue(any("merged into Agency Requirements" in d for d in dropped))
        self.assertTrue(any("Sample Work" in t for t in titles))

    def test_merge_closing_adds_commitment_and_references(self) -> None:
        sections = [
            {"id": "rfp-sec-1", "title": "Technical Approach & Scope of Work", "required": True},
        ]
        rfp = (
            "Provide three customer references with phone and email.\n"
            "Proposer must acknowledge all addenda.\n"
        )
        merged, added = merge_closing_components_into_outline(
            sections,
            rfp_context=rfp,
        )
        titles = [
            (s.title if hasattr(s, "title") else s.get("title")) for s in merged
        ]
        self.assertTrue(any("Reference" in (t or "") for t in titles), msg=titles)
        self.assertTrue(
            any("Closing" in (t or "") or "Commitment" in (t or "") for t in titles),
            msg=titles,
        )
        self.assertTrue(len(added) >= 1)


if __name__ == "__main__":
    unittest.main()
