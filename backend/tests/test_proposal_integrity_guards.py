"""Tests for proposal integrity guards (references, tier, case-study fidelity)."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalBudget, ProposalDraft, ProposalSection
from app.services.evidence_trust.rfp_hard_facts import extract_rfp_hard_facts
from app.services.proposal_integrity_guards import (
    apply_manuscript_integrity_guards,
    apply_reference_content_scrubs,
    case_study_fidelity_ok,
    case_study_has_required_structure,
    case_study_looks_like_source_dump,
    drop_incomplete_reference_entries,
    enforce_pricing_tier_for_cost_weight,
    infer_cost_weight_pct,
    prefer_case_study_kb_text,
    references_section_has_preservable_content,
    scrub_agency_contact_as_client_reference,
    scrub_case_study_overbuild,
    scrub_duplicate_reference_contact_lines,
    scrub_reference_withholding,
)


class TestReferenceIntegrity(unittest.TestCase):
    def test_upon_request_replaced(self) -> None:
        text = (
            "Oregon Employment Department\n"
            "Reference contact details available upon request.\n"
            "We have pre-cleared all three for direct contact by Tarrant County's "
            "evaluation team, and each has agreed to respond to reference checks."
        )
        out, logs = scrub_reference_withholding(text)
        self.assertIn("[VERIFY: reference contact", out)
        self.assertNotIn("upon request", out.casefold())
        self.assertNotIn("pre-cleared", out.casefold())
        self.assertTrue(logs)

    def test_draft_guard_on_references_section(self) -> None:
        draft = ProposalDraft(
            rfpId="t1",
            sections=[
                ProposalSection(
                    id="rfp-ref",
                    title="21. References",
                    content="Client X — contact information available upon request.",
                    status="generated",
                    source="rfp",
                    mode="write",
                )
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        updated, logs = apply_manuscript_integrity_guards(draft)
        body = updated.sections[0].content
        # Incomplete / upon-request packages are omitted — not left as VERIFY shells.
        self.assertIn("MANUAL FILL", body)
        self.assertNotIn("upon request", body.casefold())
        self.assertNotIn("[VERIFY: contact name", body.casefold())
        self.assertTrue(logs)

    def test_drops_verify_shell_reference_rows(self) -> None:
        body = (
            "We provide the following client references.\n\n"
            "Reference 1: Oregon Employment Department\n"
            "Contact: Jordan Lee, Communications Director\n"
            "Organization: Oregon Employment Department\n"
            "Phone: 503-555-0100\n"
            "Email: jordan.lee@oregon.gov\n"
            "Project: Workforce campaign\n\n"
            "Reference 2: Travel San Francisco\n"
            "Contact: [VERIFY: contact name and title]\n"
            "Organization: Travel San Francisco\n"
            "Phone: [VERIFY: phone number]\n"
            "Email: [VERIFY: email address]\n"
            "Project: Tourism marketing\n\n"
            "Reference 3: City of Bend\n"
            "Contact: [VERIFY: contact name and title]\n"
            "Organization: City of Bend\n"
            "Phone: [VERIFY: phone number]\n"
            "Email: [VERIFY: email address]\n"
        )
        out, logs = drop_incomplete_reference_entries(body)
        self.assertIn("Oregon Employment Department", out)
        self.assertIn("jordan.lee@oregon.gov", out)
        self.assertNotIn("Travel San Francisco", out)
        self.assertNotIn("City of Bend", out)
        self.assertIn("MANUAL FILL", out)
        self.assertIn("Reference 1:", out)
        self.assertTrue(any("Dropped" in line for line in logs))

    def test_collapses_duplicate_sonja_exhibit_k_lines(self) -> None:
        body = (
            "Sonja Anderson, Agency Director, zö agency, (541) 350-2778, connect@zo.agency\n"
            "Sonja Anderson, Agency Director, zö agency, (541) 350-2778, connect@zo.agency\n"
        )
        out, logs = apply_reference_content_scrubs(body)
        self.assertEqual(
            out.casefold().count("sonja anderson"),
            0,
            msg="Agency director must not remain as a client reference row",
        )
        self.assertIn("MANUAL FILL", out)
        self.assertTrue(logs)

    def test_duplicate_contact_line_scrub(self) -> None:
        line = (
            "Jordan Lee, Communications Director, Oregon Employment Department, "
            "503-555-0100, jordan.lee@oregon.gov"
        )
        body = f"{line}\n{line}\n"
        out, logs = scrub_duplicate_reference_contact_lines(body)
        self.assertEqual(out.count("Jordan Lee"), 1)
        self.assertTrue(logs)

    def test_agency_contact_removed_from_references(self) -> None:
        body = (
            "Sonja Anderson, Agency Director, zö agency, (541) 350-2778, connect@zo.agency\n"
        )
        out, logs = scrub_agency_contact_as_client_reference(body)
        self.assertNotIn("Sonja Anderson", out)
        self.assertIn("MANUAL FILL", out)
        self.assertTrue(logs)

    def test_preservable_content_detects_kb_reference(self) -> None:
        body = (
            "Reference 1: Oregon Employment Department\n"
            "Contact: Jordan Lee, Communications Director\n"
            "Organization: Oregon Employment Department\n"
            "Phone: 503-555-0100\n"
            "Email: jordan.lee@oregon.gov\n"
            "Project: Workforce campaign\n"
        )
        self.assertTrue(references_section_has_preservable_content(body))


class TestPricingTierGuard(unittest.TestCase):
    def test_force_low_when_cost_heavy(self) -> None:
        budget = ProposalBudget(
            rfpId="t1",
            pricingTier="Average",
            feeStructure="Pricing is built from the industry Average tier in our approved Pricing Guide.",
            lineItems=[],
            updatedAt="2026-01-01T00:00:00Z",
        )
        out, logs = enforce_pricing_tier_for_cost_weight(budget, cost_weight_pct=35.0)
        self.assertEqual(out.pricing_tier, "Low")
        self.assertTrue(any("Low" in f for f in (out.pricing_flags or [])))
        self.assertIn("Low tier", out.fee_structure)
        self.assertTrue(logs)

    def test_infer_cost_weight_from_points_phrase(self) -> None:
        text = "Price is worth 350 of 1,000 points — 35%, the single largest scoring category."
        pct = infer_cost_weight_pct(text, None)
        self.assertIsNotNone(pct)
        assert pct is not None
        self.assertGreaterEqual(pct, 30)
        self.assertLessEqual(pct, 40)


class TestHardFactsLargeScale(unittest.TestCase):
    def test_accepts_points_over_100(self) -> None:
        text = (
            "Evaluation criteria — points will be awarded as follows:\n"
            "Qualifications: 300 points\n"
            "Portfolio: 300 points\n"
            "Price: 350 points\n"
            "Total: 950 points\n"
        )
        facts = extract_rfp_hard_facts(text)
        joined = " ".join(facts.get("evaluation_lines") or [])
        self.assertIn("350", joined)
        self.assertGreaterEqual(int(facts.get("evaluation_total") or 0), 900)


class TestCaseStudyFidelity(unittest.TestCase):
    def test_generic_rewrite_fails(self) -> None:
        source = (
            "City of Umatilla Digital Campaign 2006. Rock the Locks Festival drove "
            "ticket sales and VIP sellouts in a compressed launch window for festival attendance."
        )
        generic = (
            "a digital campaign tied to municipal communications and community outreach. "
            "Existing outreach lacked the structure to track results or scale across channels."
        )
        ok, reason = case_study_fidelity_ok(source, generic)
        self.assertFalse(ok)
        self.assertIn("Rock the Locks", reason)

    def test_faithful_write_passes(self) -> None:
        source = (
            "City of Umatilla Digital Campaign 2006. Rock the Locks Festival drove "
            "ticket sales and VIP sellouts."
        )
        written = (
            "For the City of Umatilla, we built Rock the Locks Festival — a digital and "
            "traditional campaign that sold out VIP packages and outperformed prior ticket benchmarks."
        )
        ok, _ = case_study_fidelity_ok(source, written)
        self.assertTrue(ok)

    def test_focused_write_from_all_case_studies_dump_passes(self) -> None:
        """Master 03_CS_AllCaseStudies dumps many projects; a focused Medford card
        must not be stubbed just because Idaho / Bend names are absent."""
        source = (
            "CITY OF MEDFORD Community-Centered Development. Working with Parks & "
            "Recreation we developed the brand identity for Rogue X around "
            "Community Thrives Here for Medford residents.\n\n"
            "UNIVERSITY OF IDAHO Tourism campaign across Oregon and Northern "
            "California visitor markets.\n\n"
            "CITY OF BEND From Chaos to Consistency brand system across departments.\n\n"
            "DESCHUTES COUNTY One County One Brand logo modernization."
        )
        written = (
            "### City of Medford\n\n"
            "**Challenge**\n\n"
            "Medford needed a community-centered brand for Rogue X.\n\n"
            "**Solution / Our Approach**\n\n"
            "We developed the complete brand identity around Community Thrives Here "
            "with city staff and Parks & Recreation.\n\n"
            "Client Voice: [VERIFY: no client quote found in source material]"
        )
        ok, reason = case_study_fidelity_ok(source, written)
        self.assertTrue(ok, reason)


class TestCaseStudyOverbuildScrub(unittest.TestCase):
    def test_strips_strategy_goals_kpis_and_why_matters(self) -> None:
        raw = (
            "Deschutes Brewery, Heritage on Tap\n\n"
            "As one of the original craft breweries, Deschutes has a story worth telling.\n\n"
            '> "Their ability to quickly pivot on a media buy is impressive." '
            "> Ashley Picerno, Deschutes Brewery\n\n"
            "Challenge\n"
            "The brand needed messaging that could flex across product lines.\n\n"
            "Solution / Our Approach\n"
            "We built a messaging platform from heritage to humor.\n\n"
            "Strategy\n"
            "We created a messaging platform that could flex.\n\n"
            "Goals\n"
            "Reconnect with core fans\n"
            "Build messaging architecture\n\n"
            "KPIs\n"
            "Message consistency across over 20 product lines\n\n"
            "Creative Deliverables\n"
            "Messaging platform for flagship and seasonal brews\n\n"
            "Why this matters for MSU Denver\n"
            "Deschutes is not a higher education client, but it shows media pivot skill.\n"
        )
        cleaned, logs = scrub_case_study_overbuild(raw)
        self.assertIn("Challenge", cleaned)
        self.assertIn("Solution / Our Approach", cleaned)
        self.assertIn("story worth telling", cleaned)
        self.assertNotIn("Strategy", cleaned)
        self.assertNotIn("Goals", cleaned)
        self.assertNotIn("KPIs", cleaned)
        self.assertNotIn("Creative Deliverables", cleaned)
        self.assertNotIn("Why this matters for MSU Denver", cleaned)

    def test_caps_long_challenge_and_solution(self) -> None:
        long_challenge = " ".join(["Carbondale needed brand clarity"] * 25)
        long_solution = " ".join(["We delivered a destination strategy"] * 30)
        raw = (
            "### City of Carbondale\n\n"
            "**Challenge**\n\n"
            f"{long_challenge}\n\n"
            "**Solution / Our Approach**\n\n"
            f"{long_solution}\n\n"
            "Client Voice: [VERIFY: no client quote found in source material]"
        )
        cleaned, logs = scrub_case_study_overbuild(raw)
        challenge_body = cleaned.split("**Challenge**")[1].split("**Solution")[0]
        solution_body = cleaned.split("**Solution / Our Approach**")[1].split("Client Voice")[0]
        self.assertLessEqual(len(challenge_body.split()), 45)
        self.assertLessEqual(len(solution_body.split()), 55)
        self.assertTrue(any("capped" in log.lower() for log in logs))
        # Blank lines so manuscript renderer keeps sections as separate blocks.
        self.assertIn("**Challenge**\n\n", cleaned)
        self.assertIn("**Solution / Our Approach**\n\n", cleaned)

    def test_splits_inline_headings_into_blocks(self) -> None:
        raw = (
            "### Deschutes County\n\n"
            "**Challenge** Deschutes County needed a unified brand. "
            "**Solution / Our Approach** We built a visual system. "
            "Client Voice: [VERIFY: no client quote found in source material]"
        )
        cleaned, _logs = scrub_case_study_overbuild(raw)
        self.assertRegex(cleaned, r"\*\*Challenge\*\*\s*\n\n")
        self.assertRegex(cleaned, r"\*\*Solution / Our Approach\*\*\s*\n\n")
        # Not one smashed paragraph with Solution mid-line after Challenge body.
        challenge_block = cleaned.split("**Challenge**")[1].split("**Solution")[0]
        self.assertNotIn("Solution", challenge_block)

    def test_manuscript_guard_scrubs_our_work_section(self) -> None:
        draft = ProposalDraft(
            rfpId="t1",
            sections=[
                ProposalSection(
                    id="section-3-work-02-deschutes",
                    title="3.2 — Deschutes Brewery",
                    content=(
                        "Challenge\nProblem.\n\n"
                        "Goals\nGrow awareness.\n\n"
                        "Why this matters for Acme U\nNot a sector match.\n"
                    ),
                    status="generated",
                    source="generated",
                    mode="select",
                )
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        updated, logs = apply_manuscript_integrity_guards(draft)
        body = updated.sections[0].content
        self.assertIn("Challenge", body)
        self.assertNotIn("Goals", body)
        self.assertNotIn("Why this matters", body)
        self.assertTrue(any("overbuild" in line.casefold() for line in logs))

    def test_detects_umatilla_style_source_dump(self) -> None:
        dump = (
            "### 3.3 — City of Umatilla Digital Campaign 2006\n\n"
            "### 03_CS_City of Umatilla_Digital Campaign_2006.pdf\n\n"
            "### 06_WON_CityofUmatilla_Proposal_2026.pdf zoagency # CITY OF UMATILLA\n"
            "> **[photo]** A color photograph of a woman...\n"
            "SECTION 1 - FIRM OVERVIEW Who We Are\n"
            "Dear Esmeralda Perches and the Rock the Locks Selection Committee,\n"
        )
        is_dump, reason = case_study_looks_like_source_dump(dump)
        self.assertTrue(is_dump)
        self.assertTrue(reason)

    def test_structured_case_study_not_dump(self) -> None:
        body = (
            "Challenge\n"
            "The city needed festival ticket demand.\n\n"
            "Solution / Our Approach\n"
            "We ran Rock the Locks digital media.\n\n"
            "Client Voice\n"
            "[VERIFY: no client quote found in source material]\n"
        )
        is_dump, _ = case_study_looks_like_source_dump(body)
        self.assertFalse(is_dump)
        self.assertTrue(case_study_has_required_structure(body))

    def test_detects_multi_client_all_case_studies_catalog(self) -> None:
        catalog = (
            "RELEVANT CASE STUDIES\n"
            "CITY OF MEDFORD\n"
            "Community-Centered Development\n"
            "Working with Parks & Recreation on Rogue X.\n\n"
            "DESCHUTES COUNTY\n"
            "One County, One Brand\n"
            "We modernized an 80-year-old logo.\n\n"
            "CITY OF BEND\n"
            "From Chaos to Consistency\n"
            "Brand system across departments.\n\n"
            "OREGON EMPLOYMENT DEPARTMENT\n"
            "Precision Targeting Excellence\n"
            "Geofencing for unemployed audiences.\n"
        )
        is_dump, reason = case_study_looks_like_source_dump(catalog)
        self.assertTrue(is_dump)
        self.assertTrue(
            "catalog" in reason.casefold() or "relevant" in reason.casefold(),
            reason,
        )

    def test_prefer_03_cs_over_06_won_blocks(self) -> None:
        pack = (
            "### 03_CS_City of Umatilla_Digital Campaign_2006.pdf\n"
            "Rock the Locks Festival case study narrative.\n\n"
            "### 06_WON_CityofUmatilla_Proposal_2026.pdf\n"
            "Dear Selection Committee, full proposal TOC...\n"
        )
        filtered, labels = prefer_case_study_kb_text(pack)
        self.assertIn("03_CS_City of Umatilla", filtered)
        self.assertNotIn("06_WON_", filtered)
        self.assertTrue(any("03_CS" in label for label in labels))


if __name__ == "__main__":
    unittest.main()
