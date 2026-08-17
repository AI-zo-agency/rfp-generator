"""Lean outline hygiene — enrich titles; keep important + closing tabs."""

from __future__ import annotations

import unittest

from app.services.proposal_outline_dedup import (
    filter_lean_outline_sections,
    is_generic_filler_outline_title,
    is_pricing_outline_title,
    merge_closing_components_into_outline,
    outline_titles_near_duplicate,
    section_protect_from_cap,
)


class OutlineDedupTests(unittest.TestCase):
    def test_near_duplicate_approach_methodology(self) -> None:
        self.assertTrue(
            outline_titles_near_duplicate(
                "Technical Approach",
                "Technical Approach & Methodology",
            )
        )

    def test_hourly_cost_not_near_dup_of_phased_budget(self) -> None:
        self.assertFalse(
            outline_titles_near_duplicate(
                "Cost Proposal — Hourly Rates by Labor Category",
                "Budget & Pricing",
                instrument_a="cost",
                instrument_b="narrative",
            )
        )
        self.assertTrue(
            outline_titles_near_duplicate(
                "Cost Proposal — Hourly Rates by Labor Category",
                "Budget & Pricing",
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
        self.assertTrue(
            section_protect_from_cap(
                {
                    "title": "Offeror Commitment & Closing Statement",
                    "protectFromCap": True,
                    "submissionInstrument": "form",
                }
            )
        )

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

    def test_letter_of_interest_near_duplicate(self) -> None:
        self.assertTrue(
            outline_titles_near_duplicate(
                "Letter of Interest - brief overview of contractor's interest in the project",
                "Letter of Interest",
            )
        )
        kept, dropped = filter_lean_outline_sections(
            [
                {
                    "id": "10",
                    "title": (
                        "Letter of Interest - brief overview of contractor's "
                        "interest in the project"
                    ),
                    "required": True,
                },
                {"id": "16", "title": "Letter of Interest", "required": True},
                {
                    "id": "12",
                    "title": "Qualifications and Experience - detailed summary",
                    "required": True,
                },
                {"id": "18", "title": "Qualifications and Experience", "required": True},
            ],
            rfp_context="letter of interest qualifications experience",
        )
        titles = [s["title"] for s in kept]
        self.assertEqual(len([t for t in titles if "Letter of Interest" in t]), 1)
        self.assertEqual(len([t for t in titles if "Qualifications" in t]), 1)
        self.assertTrue(any("near-duplicate" in d for d in dropped))

    def test_merge_closing_does_not_readd_near_dup_letter(self) -> None:
        sections = [
            {
                "id": "rfp-sec-loi",
                "title": (
                    "Letter of Interest - brief overview of contractor's "
                    "interest in the project"
                ),
                "required": True,
            },
        ]
        # Force a closing component path via cover-letter-ish RFP language if any;
        # at minimum near-dup guard must not explode with existing titles.
        merged, _added = merge_closing_components_into_outline(
            sections,
            rfp_context="Submit a Letter of Interest with three references.",
        )
        titles = [
            (s.title if hasattr(s, "title") else s.get("title")) for s in merged
        ]
        self.assertEqual(
            len([t for t in titles if t and "letter of interest" in t.casefold()]),
            1,
            msg=titles,
        )

    def test_merge_closing_adds_only_what_the_rfp_requires(self) -> None:
        """Ledger authority: only fixture rows are added (no forced commitment)."""
        from app.services.proposal_closing_ledger import ledger_from_fixture

        sections = [
            {"id": "rfp-sec-1", "title": "Technical Approach & Scope of Work", "required": True},
        ]
        ledger = ledger_from_fixture(
            [
                {
                    "id": "references",
                    "title": "References",
                    "kind": "form",
                    "sectionId": "rfp-closing-references",
                },
                {
                    "id": "addenda_acknowledgement",
                    "title": "Acknowledgement of Addenda",
                    "kind": "form",
                    "sectionId": "rfp-closing-addenda",
                },
            ]
        )
        merged, added = merge_closing_components_into_outline(
            sections,
            rfp_context="Provide three customer references. Proposer must acknowledge all addenda.",
            ledger=ledger,
        )
        titles = [
            (s.title if hasattr(s, "title") else s.get("title")) for s in merged
        ]
        self.assertTrue(any("Reference" in (t or "") for t in titles), msg=titles)
        self.assertFalse(
            any("Closing" in (t or "") or "Commitment" in (t or "") for t in titles),
            msg=titles,
        )
        self.assertTrue(len(added) >= 1)


    def test_references_vs_references_and_past_performance_are_near_dups(self) -> None:
        self.assertTrue(
            outline_titles_near_duplicate(
                "References — At least three references for similar engagements",
                "References & Past Performance",
            )
        )
        kept, dropped = filter_lean_outline_sections(
            [
                {
                    "id": "20",
                    "title": "References — At least three references for similar engagements",
                    "required": True,
                },
                {
                    "id": "27",
                    "title": "References & Past Performance",
                    "required": True,
                },
            ],
            rfp_context="references past performance sample work",
        )
        titles = [s["title"] for s in kept]
        self.assertEqual(len([t for t in titles if "Reference" in t]), 1, msg=titles)
        self.assertTrue(any("near-duplicate" in d for d in dropped))

    def test_structural_head_label_merges_siblings(self) -> None:
        """Same head before — merges without topic-specific regex."""
        self.assertTrue(
            outline_titles_near_duplicate(
                "Specific Experience — Three Similar Projects",
                "Specific Experience — Public-Private Partnerships",
            )
        )
        self.assertTrue(
            outline_titles_near_duplicate(
                "Cover Letter (maximum one page)",
                "Cover Letter — Respondent Contact Information",
            )
        )
        self.assertFalse(
            outline_titles_near_duplicate(
                "General Experience — Team",
                "Specific Experience — Case Studies",
            )
        )
        kept, dropped = filter_lean_outline_sections(
            [
                {
                    "id": "a",
                    "title": "Budget — Detailed Narrative",
                    "required": True,
                },
                {
                    "id": "b",
                    "title": "Budget — Milestone Disbursement Schedule",
                    "required": True,
                },
                {
                    "id": "c",
                    "title": "References — Three Contacts",
                    "required": True,
                },
            ],
            rfp_context="budget references",
        )
        titles = [s["title"] for s in kept]
        self.assertEqual(len([t for t in titles if t.startswith("Budget")]), 1)
        self.assertTrue(any("References" in t for t in titles))
        self.assertTrue(any("near-duplicate" in d for d in dropped))

    def test_hard_cap_prefers_scored_and_closing(self) -> None:
        from app.services.proposal_outline_dedup import (
            enforce_outline_section_cap,
            max_rfp_outline_sections,
            stamp_outline_evaluation_weights,
        )
        from app.services.proposal_intelligence.schemas import (
            EvaluationCriterion,
            OutlineSection,
        )

        self.assertEqual(max_rfp_outline_sections(12), 8)
        self.assertEqual(max_rfp_outline_sections(40), 18)
        self.assertEqual(max_rfp_outline_sections(None), 9)

        sections = [
            OutlineSection(id=f"s{i}", title=f"Filler Narrative Tab {i}", order=i)
            for i in range(1, 21)
        ]
        sections[0] = OutlineSection(
            id="s1", title="References Form", order=1, required=True
        )
        sections[1] = OutlineSection(
            id="s2", title="Technical Approach & Methodology", order=2, required=True
        )
        stamp_outline_evaluation_weights(
            sections,
            [EvaluationCriterion(name="Technical Approach", weight=40)],
        )
        kept, dropped = enforce_outline_section_cap(sections, 8)
        self.assertEqual(len(kept), 8)
        self.assertEqual(len(dropped), 12)
        titles = {s.title for s in kept}
        self.assertIn("References Form", titles)
        self.assertIn("Technical Approach & Methodology", titles)

    def test_hard_cap_never_drops_cost_instrument(self) -> None:
        from app.services.proposal_outline_dedup import enforce_outline_section_cap
        from app.services.proposal_intelligence.schemas import OutlineSection

        sections = [
            OutlineSection(id=f"s{i}", title=f"Filler Narrative Tab {i}", order=i)
            for i in range(1, 12)
        ]
        sections[0] = OutlineSection(
            id="cost",
            title="Cost Proposal — Hourly Rate Schedule",
            order=1,
            required=True,
            protectFromCap=True,
            submissionInstrument="cost",
        )
        sections[1] = OutlineSection(
            id="ai",
            title="Generative AI Disclosure",
            order=2,
            required=True,
            protectFromCap=True,
            submissionInstrument="disclosure",
        )
        kept, dropped = enforce_outline_section_cap(sections, 8)
        titles = {s.title for s in kept}
        self.assertIn("Cost Proposal — Hourly Rate Schedule", titles)
        self.assertIn("Generative AI Disclosure", titles)
        self.assertTrue(any("Filler" in d for d in dropped))

    def test_stamp_ignores_single_token_overlap(self) -> None:
        from app.services.proposal_outline_dedup import stamp_outline_evaluation_weights
        from app.services.proposal_intelligence.schemas import (
            EvaluationCriterion,
            OutlineSection,
        )

        section = OutlineSection(
            id="x",
            title="Prior Experience with Similar Clients",
            order=1,
        )
        stamp_outline_evaluation_weights(
            [section],
            [EvaluationCriterion(name="Cost and Overall Value", weight=25)],
        )
        # Only shared token would be none / weak — must NOT stamp
        self.assertIsNone(section.evaluation_weight)


if __name__ == "__main__":
    unittest.main()
