"""Anti-duplication digests for Phase 3 drafting prompts."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalSection
from app.services.proposal_section_dedup import (
    ANTI_DUPLICATION_RULES,
    collapse_title_near_duplicate_sections,
    dedupe_manuscript_for_scan,
    format_prior_sections_block,
    prune_near_duplicate_sections,
    remove_aggregate_restatement_sections,
)


def _sec(sid: str, title: str, content: str, *, weight: float | None = None) -> ProposalSection:
    kwargs: dict = {
        "id": sid,
        "title": title,
        "content": content,
        "wordTarget": 400,
        "status": "generated",
    }
    if weight is not None:
        kwargs["evaluationWeight"] = weight
    return ProposalSection(**kwargs)


_UMATILLA_BODY = (
    "We managed comprehensive social media promotion for the City of Umatilla "
    "Rock the Lock Music Festival delivering coordinated campaigns across Facebook "
    "Instagram email and paid digital advertising with tight timeline constraints. "
    "Our approach focused on driving VIP sales and general admission purchases through "
    "targeted content and strategic platform optimization for overnight visitation. "
    "Digital campaigns outperformed prior benchmarks establishing the festival as a "
    "regional destination draw and accelerating ticket sales through tourism marketing."
)


class SectionDedupTests(unittest.TestCase):
    def test_anti_duplication_rules_forbid_restating_owned_facts(self) -> None:
        self.assertIn("ZERO repetition", ANTI_DUPLICATION_RULES)
        self.assertIn("Do NOT re-explain", ANTI_DUPLICATION_RULES)

    def test_prior_block_prefers_later_sections_when_over_cap(self) -> None:
        prior = [
            {
                "id": f"s-{i}",
                "title": f"Section {i}",
                "content": f"Unique content for section {i} about topic {i}.",
            }
            for i in range(30)
        ]
        block = format_prior_sections_block(prior, max_sections=5, max_chars_each=200)
        self.assertIn("ALREADY COVERED", block)
        self.assertIn("Section 29", block)
        self.assertIn("Section 25", block)
        self.assertNotIn("Section 0", block)
        self.assertNotIn("Section 10", block)

    def test_prior_block_excludes_batch_ids(self) -> None:
        prior = [
            {"id": "a", "title": "Approach", "content": "Our approach uses discovery."},
            {"id": "b", "title": "Timeline", "content": "Phase 1 starts in Q1."},
        ]
        block = format_prior_sections_block(prior, exclude_ids={"a"})
        self.assertIn("Timeline", block)
        self.assertNotIn("Approach", block)

    def test_prune_deletes_content_overlap_campaign_tabs(self) -> None:
        sections = [
            _sec("section-1-who-we-are", "1.1 — Who We Are", "We are zö agency brand essence."),
            _sec(
                "section-3-work-01-umatilla",
                "3.1 — City of Umatilla Digital Campaign",
                _UMATILLA_BODY,
            ),
            _sec(
                "rfp-tourism-accounts",
                "Examples of Tourism or Destination Marketing Social Media Accounts Managed",
                _UMATILLA_BODY
                + " Meta Business Suite expertise supports destination marketing conversion.",
            ),
            _sec(
                "rfp-successful-campaigns",
                "Examples of Successful Campaigns",
                _UMATILLA_BODY
                + " Seasonal destination marketing campaigns extend visitor engagement.",
            ),
            _sec(
                "rfp-references",
                "Client References",
                "San Francisco Travel Contact phone email for tourism reference verification "
                "and municipal collaboration outcomes across multi year renewals.",
            ),
        ]
        kept, dropped = prune_near_duplicate_sections(sections)
        kept_ids = {s.id for s in kept}
        self.assertIn("section-3-work-01-umatilla", kept_ids)
        self.assertIn("rfp-references", kept_ids)
        # One of the overlapping campaign tabs must go.
        campaign_kept = sum(
            1 for sid in kept_ids if sid in {"rfp-tourism-accounts", "rfp-successful-campaigns"}
        )
        self.assertEqual(campaign_kept, 1)
        self.assertTrue(dropped)

    def test_prune_deletes_overlapping_collab_tabs(self) -> None:
        body = (
            "We coordinate with the Tourism Program Manager weekly for content calendar "
            "approval monthly strategic planning and quarterly performance reviews with "
            "multi-contractor collaboration protocols for government tourism programs. "
            "Shared editorial calendars cross-contractor review and unified messaging "
            "guidelines keep brand consistency across photographers PR and digital teams "
            "while Ron Comer remains the dedicated primary liaison for day-to-day decisions."
        )
        sections = [
            _sec("section-1-who-we-are", "1.1 — Who We Are", "Brand promise and essence paragraphs."),
            _sec("rfp-collab-a", "Description of Communication and Collaboration Processes", body),
            _sec(
                "rfp-collab-b",
                "Workflow for Coordinating with Multiple Contractors While Maintaining Consistent Publishing",
                body + " Additional contractor integration detail for board reporting cycles.",
            ),
            _sec(
                "rfp-comp",
                "Proposed Compensation Structure (Monthly Retainer)",
                "Fixed monthly retainer of four thousand dollars all inclusive with travel "
                "software subscriptions reporting and coordination covered under the fee.",
            ),
        ]
        kept, dropped = prune_near_duplicate_sections(sections)
        kept_ids = {s.id for s in kept}
        self.assertIn("section-1-who-we-are", kept_ids)
        self.assertIn("rfp-comp", kept_ids)
        self.assertEqual(sum(1 for sid in kept_ids if sid.startswith("rfp-collab")), 1)
        self.assertTrue(dropped)

    def test_prune_report_content_twins(self) -> None:
        body = (
            "Our monthly performance dashboard tracks website traffic newsletter "
            "subscriptions itinerary downloads and conversion paths from social "
            "platforms with strategic recommendations for tourism ROI optimization "
            "across seasonal campaigns and geographic visitor dispersion metrics. "
            "We report cost per qualified visitor audience targeting effectiveness "
            "and budget optimization recommendations tied to overnight visitation goals."
        )
        sections = [
            _sec("rfp-reports-a", "Monthly Performance Reports with Strategic Recommendations", body),
            _sec("rfp-reports-b", "Sample Performance Reports or Dashboards", body + " Extra."),
        ]
        kept, dropped = prune_near_duplicate_sections(sections)
        self.assertEqual(len(kept), 1)
        self.assertTrue(dropped)

    def test_remove_mega_section_that_restates_siblings(self) -> None:
        filler = " ".join(["detail"] * 80)
        approach = (
            "Approach to Services / Deliverables\n\n"
            "Monthly Social Media Posting Schedules Developed from the Tourism Program's "
            f"Approved Editorial Calendar\n{filler}\n\n"
            f"Platform-Specific Publishing Strategies\n{filler}\n\n"
            f"Community Engagement and Social Listening Approach\n{filler}\n\n"
            f"Paid Advertising Management and Optimization\n{filler}\n\n"
            f"Crisis Communication and Issue Escalation Procedures\n{filler}\n"
        )
        sections = [
            _sec(
                "rfp-posting",
                "Monthly Social Media Posting Schedules Developed from the Tourism Program's Approved Editorial Calendar",
                "Short posting plan with seasonal calendar mapping.",
            ),
            _sec("rfp-platform", "Platform-Specific Publishing Strategies", "Short platform plan."),
            _sec("rfp-engage", "Community Engagement and Social Listening Approach", "Short engage plan."),
            _sec("rfp-paid", "Paid Advertising Management and Optimization", "Short paid plan."),
            _sec("rfp-crisis", "Crisis Communication and Issue Escalation Procedures", "Short crisis plan."),
            _sec("rfp-approach", "Approach to Services / Deliverables", approach),
        ]
        kept, logs = remove_aggregate_restatement_sections(sections)
        kept_ids = {s.id for s in kept}
        self.assertNotIn("rfp-approach", kept_ids)
        self.assertIn("rfp-posting", kept_ids)
        self.assertTrue(any("removed" in log for log in logs))

    def test_dedupe_never_drops_budget_pricing_tab(self) -> None:
        budget_body = (
            "## Proposed Investment\n\n**Professional fees: $151,000**\n\n"
            "Total proposed investment: $151,000.\n\n"
            "| Phase | Deliverable | Amount |\n| --- | --- | ---: |\n"
            "| Discovery | Kickoff | $30,000 |\n| **Total** | | **$151,000** |\n"
        )
        twin = budget_body + "\nExtra closing sentence about fees and transparency.\n"
        sections = [
            _sec("section-budget-pricing", "Budget & Pricing", budget_body),
            _sec("rfp-fees-alt", "Fee Schedule & Cost Proposal", twin),
            _sec("rfp-other", "Understanding of Scope", "Client goals and audiences only. " * 20),
        ]
        kept, logs = dedupe_manuscript_for_scan(sections)
        kept_ids = {s.id for s in kept}
        self.assertIn("section-budget-pricing", kept_ids)
        self.assertTrue(any(s.title == "Budget & Pricing" for s in kept))

    def test_dedupe_protects_pricing_title_with_score_under_4(self) -> None:
        """Bare 'Pricing' scores 3 — must still be protected (threshold was wrongly >=4)."""
        pricing_body = (
            "## Fee table\n\nTotal: $90,000\n\n"
            "| Line | Amount |\n| --- | ---: |\n| Media | $40,000 |\n"
            "| Creative | $50,000 |\n"
        )
        twin = pricing_body + "\nAdditional fee narrative for evaluators.\n"
        sections = [
            _sec("rfp-pricing", "Pricing", pricing_body),
            _sec("rfp-fees-clone", "Cost Proposal Narrative", twin),
            _sec("rfp-other", "Understanding of Scope", "Client goals and audiences only. " * 20),
        ]
        kept, _logs = dedupe_manuscript_for_scan(sections)
        self.assertIn("rfp-pricing", {s.id for s in kept})

    def test_dedupe_never_drops_required_forms_or_experience_tabs(self) -> None:
        sections = [
            _sec(
                "rfp-req-forms",
                "Required Forms & Attachments",
                "W-9, COI, and signed addenda checklist. " * 15,
            ),
            _sec(
                "ledger-comp-1",
                "Public Sector/Transportation Industry Experience",
                "Transit and government campaign experience narrative. " * 20,
            ),
            _sec(
                "rfp-mega",
                "Qualifications Summary",
                "Required Forms & Attachments\n"
                "Public Sector/Transportation Industry Experience\n"
                + ("Restated sibling content. " * 80),
            ),
        ]
        kept, _logs = dedupe_manuscript_for_scan(sections)
        kept_ids = {s.id for s in kept}
        self.assertIn("rfp-req-forms", kept_ids)
        self.assertIn("ledger-comp-1", kept_ids)

    def test_collapse_references_and_past_performance_twin(self) -> None:
        body = (
            "Oregon Recovery Network multicultural campaign and McMinnville Library "
            "bilingual programming as comparable public-sector communications work. "
        ) * 8
        sections = [
            _sec(
                "s20",
                "References — At least three references for similar engagements",
                body,
            ),
            _sec("s27", "References & Past Performance", body + " Extra sentence."),
            _sec("s19", "Cost Proposal — Pricing models", "Fee table and narrative. " * 20),
        ]
        kept, dropped = collapse_title_near_duplicate_sections(sections)
        titles = [s.title for s in kept]
        self.assertEqual(len([t for t in titles if "Reference" in t]), 1, msg=titles)
        self.assertTrue(any("near-duplicate" in d for d in dropped))
        self.assertTrue(any("Cost Proposal" in t for t in titles))


if __name__ == "__main__":
    unittest.main()
