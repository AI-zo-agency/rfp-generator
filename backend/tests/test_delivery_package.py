"""Delivery Package roles — LLM-stamped on outline/briefs, not title regex."""

from __future__ import annotations

import unittest

from app.services.proposal_delivery_package import (
    DeliveryRole,
    build_delivery_package,
    format_delivery_package_block,
    methodology_retrieval_query,
    normalize_delivery_roles,
    roles_from_execution_plan,
)


class NormalizeRolesTests(unittest.TestCase):
    def test_normalizes_planner_lists(self) -> None:
        self.assertEqual(
            normalize_delivery_roles(["substance", "calendar"]),
            frozenset({DeliveryRole.SUBSTANCE, DeliveryRole.CALENDAR}),
        )
        self.assertEqual(
            normalize_delivery_roles("price"),
            frozenset({DeliveryRole.PRICE}),
        )
        self.assertEqual(normalize_delivery_roles(["nope", "substance"]), frozenset({DeliveryRole.SUBSTANCE}))
        self.assertEqual(normalize_delivery_roles(None), frozenset())


class RolesFromPlanTests(unittest.TestCase):
    def test_reads_roles_from_section_brief_not_title(self) -> None:
        plan = {
            "writing": {
                "sectionPlans": {
                    "plans": [
                        {
                            "sectionId": "rfp-growth",
                            "title": "Weird Buyer Heading XYZ",
                            "deliveryRoles": ["substance", "calendar"],
                        }
                    ]
                },
                "proposalOutline": {"sections": []},
            }
        }
        roles = roles_from_execution_plan(
            plan, section_id="rfp-growth", section_title="Weird Buyer Heading XYZ"
        )
        self.assertEqual(
            roles, frozenset({DeliveryRole.SUBSTANCE, DeliveryRole.CALENDAR})
        )

    def test_reads_roles_from_outline_when_brief_missing(self) -> None:
        plan = {
            "writing": {
                "sectionPlans": {"plans": []},
                "proposalOutline": {
                    "sections": [
                        {
                            "id": "rfp-fee",
                            "title": "Quotation Form Packet 7B",
                            "deliveryRoles": ["price"],
                        }
                    ]
                },
            }
        }
        roles = roles_from_execution_plan(
            plan, section_id="rfp-fee", section_title="Quotation Form Packet 7B"
        )
        self.assertEqual(roles, frozenset({DeliveryRole.PRICE}))

    def test_unknown_title_without_plan_roles_is_empty(self) -> None:
        """No synonym guessing — empty roles is safer than a wrong match."""
        roles = roles_from_execution_plan(
            {"writing": {"sectionPlans": {"plans": []}, "proposalOutline": {"sections": []}}},
            section_id="x",
            section_title="Strategic Growth Approach",
        )
        self.assertEqual(roles, frozenset())


class BuildAndFormatPackageTests(unittest.TestCase):
    def test_build_from_methodology_and_timeline(self) -> None:
        plan = {
            "delivery": {
                "methodology": {
                    "phases": [
                        {
                            "name": "Website Maintenance",
                            "activities": ["security patching"],
                            "governance": "client sign-off",
                        },
                        {
                            "name": "Sponsor Development",
                            "activities": ["tier packet"],
                            "governance": "pricing approval",
                        },
                    ],
                    "confidence": 0.8,
                },
                "timeline": {
                    "milestones": [
                        {"name": "Onboarding & audit", "offset": "Week 1-6", "dependsOn": []},
                    ],
                    "goLive": "Aug 2027",
                    "reviewCycles": "monthly",
                    "confidence": 0.7,
                },
            }
        }
        package = build_delivery_package(plan)
        self.assertEqual(
            [w["name"] for w in package["workstreams"]],
            ["Website Maintenance", "Sponsor Development"],
        )
        self.assertEqual(package["goLive"], "Aug 2027")

    def test_build_emits_generic_horizon_fields_from_timeline_intel(self) -> None:
        plan = {
            "opportunity": {
                "understanding": {
                    "timelineIntel": {
                        "contractHorizon": "12-month base + 2 option years",
                        "performanceEnd": "June 30, 2030",
                        "scheduleAuthority": "TBD after award — contractor proposes",
                    }
                }
            },
            "delivery": {
                "methodology": {"phases": [], "confidence": 0.5},
                "timeline": {
                    "milestones": [],
                    "goLive": "",
                    "reviewCycles": "",
                    "confidence": 0.5,
                },
            },
        }
        package = build_delivery_package(plan)
        self.assertEqual(package["contractHorizon"], "12-month base + 2 option years")
        self.assertEqual(package["performanceEnd"], "June 30, 2030")
        self.assertEqual(
            package["scheduleAuthority"],
            "TBD after award — contractor proposes",
        )
        block = format_delivery_package_block(
            frozenset({DeliveryRole.CALENDAR}),
            package,
            section_title="Project Schedule",
        )
        self.assertIn("12-month base + 2 option years", block)
        self.assertIn("June 30, 2030", block)
        self.assertIn("PROPOSED schedule", block)
        self.assertIn("Month-N", block)
        self.assertNotIn("Montana", block)
        self.assertNotIn("May 31", block)

    def test_substance_block_from_roles(self) -> None:
        package = {
            "workstreams": [
                {
                    "name": "Social Media",
                    "activities": ["archive calendar"],
                    "governance": "client review",
                }
            ],
            "milestones": [],
            "goLive": "",
            "reviewCycles": "",
        }
        block = format_delivery_package_block(
            frozenset({DeliveryRole.SUBSTANCE}),
            package,
            section_title="Weird Buyer Heading XYZ",
        )
        self.assertIn("DELIVERY PACKAGE", block)
        self.assertIn("Social Media", block)
        self.assertIn("complete defendable plan", block.lower())
        self.assertIn("not by title keywords", block.lower())

    def test_empty_roles_yield_empty_block(self) -> None:
        self.assertEqual(
            format_delivery_package_block(
                frozenset(),
                {"workstreams": [{"name": "X", "activities": [], "governance": ""}]},
                section_title="Anything",
            ),
            "",
        )


class MethodologyQueryTests(unittest.TestCase):
    def test_query_is_sector_agnostic_not_website_hardcoded(self) -> None:
        q = methodology_retrieval_query(
            project_type="festival marketing sponsorship",
            industry="nonprofit",
            org_type="association",
            sector="events",
        )
        self.assertNotIn("website methodology", q.lower())
        self.assertIn("festival", q.lower())
        self.assertIn("delivery", q.lower())


if __name__ == "__main__":
    unittest.main()
