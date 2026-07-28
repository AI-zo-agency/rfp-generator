import unittest

from app.services.company_qualification.agents.content_budget import DEFAULT_BUDGETS
from app.services.company_qualification.retrieval.company_queries import (
    filter_company_sources,
    is_company_source,
)
from app.services.company_qualification.agents.team_selection import (
    MIN_FIT_SCORE,
    PIC_ROLE_TITLE,
    build_roster_profiles,
    _ensure_owner_as_principal,
    _ensure_principal_role,
    normalize_selected_members,
)
from app.services.company_qualification.schemas import (
    CapabilityTierItem,
    PrioritizedCapabilities,
    ProposalContext,
    RequiredTeamRole,
    Section1ContentBudget,
    TeamMemberSelection,
)
from app.services.proposal_sections_graph import _build_graph


class CompanyQualificationTests(unittest.TestCase):
    def test_excludes_bio_and_case_study_sources(self) -> None:
        self.assertFalse(is_company_source("04_Bio_Sonja_Anderson.pdf"))
        self.assertFalse(is_company_source("03_CS_City_Website.pdf"))
        self.assertFalse(is_company_source("06_WON_Proposal.pdf"))
        self.assertTrue(is_company_source("01_companyfacts_verified.pdf"))

    def test_filter_company_sources(self) -> None:
        sources = [
            "01_companyfacts_verified.pdf",
            "04_Bio_Todd_Anderson.pdf",
            "03_CS_Portland.pdf",
        ]
        filtered = filter_company_sources(sources)
        self.assertEqual(filtered, ["01_companyfacts_verified.pdf"])

    def test_default_content_budget_has_five_subsections(self) -> None:
        self.assertEqual(len(DEFAULT_BUDGETS), 5)
        ids = {b.section_id for b in DEFAULT_BUDGETS}
        self.assertIn("section-1-who-we-are", ids)
        self.assertIn("section-1-org-structure", ids)

    def test_org_structure_budget_covers_full_roster(self) -> None:
        org = next(b for b in DEFAULT_BUDGETS if b.section_id == "section-1-org-structure")
        self.assertEqual(org.word_min, 400)
        self.assertEqual(org.word_max, 900)
        self.assertEqual(org.format, "list")

    def test_prioritized_capabilities_three_tiers(self) -> None:
        caps = PrioritizedCapabilities(
            primary=[CapabilityTierItem(capability="Website Development", rationale="Primary")],
            secondary=[CapabilityTierItem(capability="SEO", rationale="Supporting")],
            omit=[CapabilityTierItem(capability="Podcast Production", rationale="Out of scope")],
        )
        self.assertEqual(len(caps.primary), 1)
        self.assertEqual(len(caps.omit), 1)

    def test_proposal_context_parses(self) -> None:
        ctx = ProposalContext.model_validate(
            {
                "industry": "Municipality",
                "servicesRequested": ["Website"],
                "buyerType": "Government",
                "evaluationPriorities": ["Accessibility"],
                "projectComplexity": "medium",
                "proposalType": "website_redesign",
                "summary": "Municipal website redesign.",
            }
        )
        self.assertEqual(ctx.industry, "Municipality")
        self.assertEqual(ctx.project_complexity, "medium")

    def test_section1_content_budget_schema(self) -> None:
        budget = Section1ContentBudget(budgets=list(DEFAULT_BUDGETS))
        payload = budget.model_dump(by_alias=True)
        self.assertEqual(len(payload["budgets"]), 5)

    def test_section_tracks_run_in_strict_order(self) -> None:
        """Every generation step is sequential — no parallel fan-out."""
        graph = _build_graph().get_graph()
        edges = {(edge.source, edge.target) for edge in graph.edges}

        self.assertIn(("__start__", "fetch_proposal_context"), edges)
        self.assertIn(("fetch_proposal_context", "fetch_company_truth"), edges)
        self.assertIn(("fetch_company_truth", "prioritize_capabilities"), edges)
        self.assertNotIn(("__start__", "fetch_company_truth"), edges)
        self.assertIn(("plan_section_1", "build_section_1_cq"), edges)
        self.assertIn(("build_section_1_cq", "select_team"), edges)
        self.assertIn(("select_team", "build_bios"), edges)
        self.assertIn(("build_bios", "select_evidence"), edges)
        self.assertIn(("select_evidence", "build_case_studies"), edges)
        self.assertNotIn(("prioritize_capabilities", "select_team"), edges)
        self.assertNotIn(("prioritize_capabilities", "select_evidence"), edges)

    def test_roster_profiles_extract_people_not_employers(self) -> None:
        roster = """
## RON COMER
senior account manager
Ron leads media strategy.

## YEARS OF EXPERIENCE
| Traditional Media | 20 years | Account Management | 38 years |

### KSA Marketing
Client Services Director 2023 - 2025
"""
        profiles = build_roster_profiles(roster)
        names = [p["name"] for p in profiles]
        self.assertIn("Ron Comer", names)
        self.assertNotIn("Ksa Marketing", names)
        ron = next(p for p in profiles if p["name"] == "Ron Comer")
        self.assertTrue(ron["expertise"])

    def test_weak_fit_score_constant(self) -> None:
        self.assertGreaterEqual(MIN_FIT_SCORE, 0.5)
        weak = TeamMemberSelection(name="X", role="Y", fitScore=0.2)
        self.assertLess(weak.fit_score, MIN_FIT_SCORE)

    def test_principal_role_always_injected_first(self) -> None:
        roles = _ensure_principal_role(
            [RequiredTeamRole(role="Media Buying Lead", mustHaveSkills=["media buying"])]
        )
        self.assertEqual(roles[0].role, PIC_ROLE_TITLE)
        self.assertTrue(roles[0].is_leadership)
        self.assertEqual(roles[1].role, "Media Buying Lead")

    def test_agency_owner_forced_as_principal_not_niche_role(self) -> None:
        profiles = [
            {"name": "Ron Comer", "title": "senior account manager", "snippet": ""},
            {"name": "Sonja Anderson", "title": "founder and ceo", "snippet": "agency director"},
        ]
        members = [
            TeamMemberSelection(
                name="Ron Comer",
                role="Account Lead",
                fitScore=0.9,
            ),
            TeamMemberSelection(
                name="Sonja Anderson",
                role="Live Demonstration Lead / Pitch Presenter",
                fitScore=0.71,
            ),
        ]
        fixed = _ensure_owner_as_principal(members, profiles)
        self.assertEqual(fixed[0].name, "Sonja Anderson")
        self.assertEqual(fixed[0].role, PIC_ROLE_TITLE)
        self.assertTrue(all(m.role != "Live Demonstration Lead / Pitch Presenter" for m in fixed))
        self.assertTrue(any(m.name == "Ron Comer" for m in fixed))


if __name__ == "__main__":
    unittest.main()
