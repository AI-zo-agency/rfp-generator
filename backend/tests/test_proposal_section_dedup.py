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

    def test_senior_editor_compact_keeps_overlapping_campaign_tabs(self) -> None:
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
        kept, _logs = dedupe_manuscript_for_scan(sections, drop_clone_tabs=False)
        kept_ids = {s.id for s in kept}
        self.assertIn("rfp-tourism-accounts", kept_ids)
        self.assertIn("rfp-successful-campaigns", kept_ids)
        self.assertIn("rfp-references", kept_ids)

    def test_trim_overlap_keeps_tabs_and_strips_copied_paragraphs(self) -> None:
        shared = (
            "We staff a dedicated account team with weekly status meetings, "
            "monthly reporting, and a named project manager for day-to-day "
            "decisions across the full engagement window for this contract."
        )
        sections = [
            _sec(
                "rfp-capacity",
                "Capacity",
                shared + "\n\nUnique capacity table lists 40 creative hours per week.",
            ),
            _sec(
                "rfp-staffing",
                "Staffing Plan",
                shared + "\n\nUnique staffing roster names roles without repeating the team essay.",
            ),
        ]
        kept, logs = dedupe_manuscript_for_scan(sections, drop_clone_tabs=False)
        self.assertEqual({s.id for s in kept}, {"rfp-capacity", "rfp-staffing"})
        staffing = next(s for s in kept if s.id == "rfp-staffing")
        capacity = next(s for s in kept if s.id == "rfp-capacity")
        self.assertIn("Unique capacity table", capacity.content or "")
        self.assertTrue(
            any("trimmed" in line for line in logs)
            or "See **" in (staffing.content or ""),
        )
        self.assertIn("See **", staffing.content or "")
        self.assertNotIn(shared, staffing.content or "")

    def test_trim_static_restates_in_rfp_tab(self) -> None:
        who = (
            "zö is a full-service independent creative agency founded in 2013 "
            "with offices in Portland serving public-sector communications work."
        )
        sections = [
            _sec("section-1-who-we-are", "1.1 Who We Are", who),
            _sec(
                "rfp-approach",
                "Approach",
                who + "\n\nWeekly sprints with a named PM and a shared content calendar.",
            ),
        ]
        kept, logs = dedupe_manuscript_for_scan(sections, drop_clone_tabs=False)
        approach = next(s for s in kept if s.id == "rfp-approach")
        self.assertEqual(len(kept), 2)
        self.assertIn("Weekly sprints", approach.content or "")
        self.assertIn("See **", approach.content or "")
        self.assertNotIn("founded in 2013", approach.content or "")
        self.assertTrue(any("trimmed" in line for line in logs))

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

    def test_scan_coverage_gate_keeps_mega_with_unique_content(self) -> None:
        """Complete Scan (require_full_coverage): a mega-tab that restates
        siblings but carries its OWN unique content must be KEPT, not deleted —
        only its duplicated prose is trimmed. Never lose already-made content."""
        approach = "Our strategic approach anchors enrollment goals with planning and execution. " * 8
        dash = "Our dashboards track application starts completed applications cost per application. " * 8
        innov = "Our innovation process runs quarterly audits monthly mining and annual planning. " * 8
        unique = "Our proprietary neuromarketing biometric sentiment methodology delivers differentiated advantage. " * 20
        mega = (
            "Strategic Approach to Portfolio Planning. " + approach
            + "Sample Reporting Dashboards. " + dash
            + "Innovation Process and Examples. " + innov
            + unique
        )
        sections = [
            _sec("mega", "Brand Marketing Plan", mega),
            _sec("d1", "Strategic Approach to Portfolio Planning", approach),
            _sec("e1", "Sample Reporting Dashboards", dash),
            _sec("f1", "Innovation Process and Examples", innov),
        ]
        kept, logs = remove_aggregate_restatement_sections(
            sections, require_full_coverage=True
        )
        kept_ids = {s.id for s in kept}
        self.assertIn("mega", kept_ids, msg="mega with unique content must be kept")
        self.assertTrue(any("kept" in log and "covered" in log for log in logs), msg=logs)

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

    def test_scan_drop_clone_false_still_collapses_identical_title_twins(self) -> None:
        """Complete Scan used drop_clone_tabs=False and left two §5.2 / §5.9 tabs."""
        body_a = (
            "North Miami Beach municipal brand development with comparable recreation "
            "center and community outreach case studies for evaluator review. "
        ) * 6
        body_b = (
            "OpenGov portal vendor registration fields completed with legal name FEIN "
            "and contact details synced from Business Information Section 1.3. "
        ) * 6
        sections = [
            _sec(
                "rfp-structure-5-9",
                "Section 5.9 — OpenGov Portal Vendor Registration",
                body_b,
            ),
            _sec(
                "rfp-structure-5-2",
                "Section 5.2 — Sample Work Submission (Portfolio of Comparable Projects)",
                body_a,
            ),
            _sec(
                "rfp-structure-5-2-2",
                "Section 5.2 — Sample Work Submission (Portfolio of Comparable Projects)",
                body_a + " Extra twin paragraph.",
            ),
            _sec(
                "rfp-structure-5-9-2",
                "Section 5.9 — OpenGov Portal Vendor Registration",
                body_b + " Extra twin paragraph.",
            ),
            _sec(
                "rfp-addenda",
                "Acknowledgment of Addenda",
                "We acknowledge receipt of all addenda issued for this solicitation.",
            ),
        ]
        kept, logs = dedupe_manuscript_for_scan(sections, drop_clone_tabs=False)
        titles = [s.title or "" for s in kept]
        self.assertEqual(
            len([t for t in titles if "Sample Work" in t]),
            1,
            msg=titles,
        )
        self.assertEqual(
            len([t for t in titles if "OpenGov" in t]),
            1,
            msg=titles,
        )
        self.assertTrue(any("near-duplicate" in line for line in logs))
        self.assertTrue(any("Addenda" in t for t in titles))

    def test_scan_drop_clone_false_collapses_lettered_submittal_item_twins(self) -> None:
        """A lettered RFP submittal item ('F. Innovation Process...') drafted a
        second time without its letter prefix ('Innovation Process...') is the
        same ask twice — must collapse via the exact-match path, same as a
        digit-prefixed twin already does, without touching the disabled soft
        near-dup matcher."""
        body = (
            "We evaluate emerging channels, audience strategies, and AI-enabled "
            "tools on a continuous cycle, bringing proactive recommendations "
            "with business impact projections before the client asks. "
        ) * 6
        sections = [
            _sec(
                "rfp-innovation-unlettered",
                "Innovation Process and Examples of Strategic Recommendations "
                "Provided to Clients",
                body,
            ),
            _sec(
                "rfp-innovation-lettered",
                "F. Innovation Process and Examples of Strategic Recommendations "
                "Provided to Clients",
                body + " Extra lettered-pass paragraph.",
            ),
            _sec(
                "rfp-approach",
                "D. Strategic Approach to Portfolio Paid Media Planning and "
                "Optimization",
                "Annual planning cycle, quarterly strategy sessions, and "
                "campaign-level execution framework. " * 8,
            ),
        ]
        kept, logs = dedupe_manuscript_for_scan(sections, drop_clone_tabs=False)
        titles = [s.title or "" for s in kept]
        self.assertEqual(
            len([t for t in titles if "Innovation Process" in t]), 1, msg=titles
        )
        self.assertTrue(any("near-duplicate" in line for line in logs), msg=logs)
        # The lone lettered section with no unlettered twin must survive untouched.
        self.assertTrue(any("Strategic Approach" in t for t in titles), msg=titles)

    def test_scan_drop_clone_false_keeps_soft_near_dup_toc_siblings(self) -> None:
        """Complete Scan must not merge distinct TOC tabs that merely share words."""
        body = (
            "We manage destination marketing social accounts and successful campaign "
            "examples with weekly reporting for municipal tourism clients. "
        ) * 8
        sections = [
            _sec(
                "rfp-tourism-accounts",
                "Examples of Tourism or Destination Marketing Social Media Accounts Managed",
                body,
            ),
            _sec(
                "rfp-successful-campaigns",
                "Examples of Successful Campaigns",
                body + " Seasonal destination marketing campaigns extend visitor engagement.",
            ),
            _sec(
                "rfp-refs-short",
                "References",
                "Contact phone email for tourism reference verification across renewals. " * 4,
            ),
            _sec(
                "rfp-refs-past",
                "References & Past Performance",
                "Past performance contacts and outcomes across multi year renewals. " * 4,
            ),
        ]
        kept, _logs = dedupe_manuscript_for_scan(sections, drop_clone_tabs=False)
        kept_ids = {s.id for s in kept}
        self.assertEqual(
            kept_ids,
            {
                "rfp-tourism-accounts",
                "rfp-successful-campaigns",
                "rfp-refs-short",
                "rfp-refs-past",
            },
        )


if __name__ == "__main__":
    unittest.main()
