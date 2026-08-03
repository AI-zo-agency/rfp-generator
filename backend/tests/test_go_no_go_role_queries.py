"""The KB must be searched for the disciplines an RFP names.

Real symptom: for a municipal website RFP the tool searched sector/client/title
only. It never searched "web developer", so the one relevant bio in the KB
(a Web Developer, 10+ yrs WordPress) was never retrieved, while narrative-heavy
bios surfaced on topical similarity and were cited as technical evidence —
including a Creative Director whose own bio reads "Web Design/Development
(Not Programming)".
"""

from __future__ import annotations

import unittest

from app.services.go_no_go_role_queries import (
    required_disciplines,
    role_evidence_queries,
)

# Condensed from the municipal website RFP that produced the bad analysis.
WEBSITE_RFP = """
The County seeks a vendor to redesign its public website. Scope includes
discovery and stakeholder engagement, information architecture and navigation
design, user experience (UX) design, visual design, CMS implementation,
content migration, hosting and ongoing maintenance, WCAG 2.1 AA accessibility
conformance, GA4 and Google Tag Manager configuration, Slate CRM integration,
AI-assisted site search, GIS map integration, and an employee intranet.
Vendors must document cybersecurity practices.
"""

BRAND_RFP = """
The City seeks a branding and public relations partner for a tourism campaign.
Scope includes visual identity, logo refresh, style guide, media relations and
press outreach, plus Spanish-language translation of campaign materials.
"""


class RoleQueryTests(unittest.TestCase):
    def test_website_rfp_triggers_a_web_developer_search(self) -> None:
        blob = " | ".join(role_evidence_queries(WEBSITE_RFP)).casefold()
        self.assertIn("web developer", blob)
        self.assertIn("wordpress", blob)

    def test_website_rfp_covers_its_core_disciplines(self) -> None:
        found = required_disciplines(WEBSITE_RFP)
        for discipline in (
            "web development",
            "CMS",
            "hosting and infrastructure",
            "content migration",
            "UX design",
            "accessibility",
            "cybersecurity",
            "GIS and mapping",
            "analytics",
            "CRM integration",
            "AI search",
            "intranet",
        ):
            self.assertIn(discipline, found)

    def test_queries_target_bio_documents(self) -> None:
        queries = role_evidence_queries(WEBSITE_RFP)
        self.assertTrue(
            any("04_Bio" in q for q in queries),
            "role searches must reach bio documents, not just case studies",
        )

    def test_brand_rfp_does_not_ask_for_web_developers(self) -> None:
        found = required_disciplines(BRAND_RFP)
        self.assertIn("branding", found)
        self.assertIn("public relations", found)
        self.assertIn("translation", found)
        self.assertNotIn("CMS", found)
        self.assertNotIn("hosting and infrastructure", found)

    def test_no_queries_for_empty_text(self) -> None:
        self.assertEqual(role_evidence_queries(""), [])
        self.assertEqual(required_disciplines(""), [])

    def test_query_count_is_bounded(self) -> None:
        queries = role_evidence_queries(WEBSITE_RFP, max_queries=5)
        self.assertLessEqual(len(queries), 5)

    def test_every_query_is_scoped_to_zo(self) -> None:
        for query in role_evidence_queries(WEBSITE_RFP):
            self.assertTrue(query.startswith("zö agency"), query)


if __name__ == "__main__":
    unittest.main()
