"""Evidence trust gate — ClientList, provenance, empty-slot VERIFY."""

from __future__ import annotations

import unittest

from app.services.evidence_trust.claim_validator import validate_and_flag_section
from app.services.evidence_trust.client_list import parse_client_list_markdown
from app.services.evidence_trust.flags import verify_gap
from app.services.evidence_trust.gate import (
    ClaimIntent,
    GateDecision,
    filter_evidence_hits,
    gate_client_for_claim,
)
from app.services.evidence_trust.load_client_list import hit_as_dict
from app.services.evidence_trust.provenance import (
    ProvenanceKind,
    classify_provenance,
    is_win_eligible,
)
from app.services.evidence_trust.rfp_hard_facts import extract_rfp_hard_facts


CLIENT_LIST_FIXTURE = """
# 01_ClientList_Approved

| Client | Sector | Work Type | Public |
|---|---|---|---|
| City of Medford | Municipal Government | Rogue X facility brand — parks and recreation | Yes |
| City of Santa Clara | Municipal Government | PR and brand partner for city and stadium authority | Yes |
| Maricopa County | County Government | Preferred vendor, multi-department brand and marketing partner | Yes |
| Thrive Guides | Senior Care | Brand foundation, website, digital marketing | Confirm |
| Deschutes County Title | Title Insurance | Brand overhaul, website with custom mortgage calculators | Yes |
| San Francisco Travel | Destination Marketing | Global events strategy, Summer of Love campaign for meeting and conference planners | Yes |
| Deschutes Brewery | Food and Beverage | Branding and marketing | Yes |
| Bend Gynecology | Healthcare | Full rebrand, website, multi-channel marketing campaign | Confirm |
"""


class ClientListParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = parse_client_list_markdown(CLIENT_LIST_FIXTURE)

    def test_parses_public_and_confirm(self) -> None:
        thrive = self.registry.find("Thrive Guides")
        assert thrive is not None
        self.assertTrue(thrive.is_confirm)
        medford = self.registry.find("Medford")
        assert medford is not None
        self.assertTrue(medford.is_public_yes)

    def test_website_claim_rejects_brand_only_municipal(self) -> None:
        medford = self.registry.find("City of Medford")
        assert medford is not None
        self.assertFalse(self.registry.work_type_supports_claim(medford, "website_build"))
        title = self.registry.find("Deschutes County Title")
        assert title is not None
        self.assertTrue(self.registry.work_type_supports_claim(title, "website_build"))

    def test_sf_travel_is_mci_not_leisure(self) -> None:
        sf = self.registry.find("San Francisco Travel")
        assert sf is not None
        self.assertTrue(self.registry.work_type_supports_claim(sf, "tourism_mci"))
        self.assertFalse(self.registry.work_type_supports_claim(sf, "tourism_leisure"))


class ProvenanceTests(unittest.TestCase):
    def test_fin_not_win_eligible(self) -> None:
        hit = hit_as_dict(
            source="07_FIN_City_of_San_Leandro.pdf",
            excerpt="Finalist proposal narrative",
        )
        self.assertEqual(classify_provenance(hit), ProvenanceKind.FINALIST)
        self.assertFalse(is_win_eligible(hit))

    def test_won_is_win_eligible(self) -> None:
        hit = hit_as_dict(
            source="06_WON_Deschutes_County.pdf",
            excerpt="Won engagement summary",
        )
        self.assertTrue(is_win_eligible(hit))

    def test_resonance_is_competitor(self) -> None:
        hit = hit_as_dict(
            source="08_Lost_Lynchburg.pdf",
            excerpt="Resonance was selected as the agency of record",
        )
        self.assertEqual(classify_provenance(hit), ProvenanceKind.COMPETITOR)


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = parse_client_list_markdown(CLIENT_LIST_FIXTURE)

    def test_confirm_blocked(self) -> None:
        result = gate_client_for_claim(
            "Thrive Guides",
            registry=self.registry,
            intent=ClaimIntent(slot="experience", claim="website_build"),
        )
        self.assertEqual(result.decision, GateDecision.BLOCK_CONFIRM)
        self.assertIn("FLAG", result.gap_tag or "")
        self.assertIn("Confirm", result.gap_tag or "")

    def test_website_filter_keeps_title_drops_medford(self) -> None:
        hits = [
            hit_as_dict(
                source="03_CS_Medford.md",
                excerpt="City of Medford Rogue X facility brand work",
            ),
            hit_as_dict(
                source="03_CS_Deschutes_County_Title.md",
                excerpt="Deschutes County Title website with custom mortgage calculators",
            ),
            hit_as_dict(
                source="03_CS_Thrive.md",
                excerpt="Thrive Guides website and digital marketing",
            ),
        ]
        result = filter_evidence_hits(
            hits,
            registry=self.registry,
            intent=ClaimIntent(slot="website proof", claim="website_build"),
        )
        self.assertEqual(result.decision, GateDecision.ALLOW)
        labels = [h["source"] for h in result.allowed_hits]
        self.assertIn("03_CS_Deschutes_County_Title.md", labels)
        self.assertNotIn("03_CS_Medford.md", labels)
        self.assertNotIn("03_CS_Thrive.md", labels)

    def test_empty_after_gate_returns_verify_not_invent(self) -> None:
        hits = [
            hit_as_dict(
                source="03_CS_Medford.md",
                excerpt="City of Medford brand work only",
            ),
            hit_as_dict(
                source="07_FIN_San_Leandro.pdf",
                excerpt="City of Santa Clara appears in competitor narrative",
            ),
        ]
        result = filter_evidence_hits(
            hits,
            registry=self.registry,
            intent=ClaimIntent(slot="references", claim="website_build"),
        )
        self.assertEqual(result.decision, GateDecision.EMPTY)
        self.assertTrue(result.gap_tag)
        tag = result.gap_tag or ""
        self.assertTrue(tag.startswith("[VERIFY:") or tag.startswith("[FLAG:"))
        self.assertNotIn("@", tag)


class ClaimValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = parse_client_list_markdown(CLIENT_LIST_FIXTURE)

    def test_flags_confirm_in_prose(self) -> None:
        prose = "We delivered a website for Thrive Guides with measurable results."
        out, report = validate_and_flag_section(prose, registry=self.registry)
        self.assertGreaterEqual(report.flags_inserted, 1)
        self.assertIn("[FLAG:", out)
        self.assertIn("Thrive Guides", out)

    def test_clears_invented_references(self) -> None:
        prose = (
            "References:\n"
            "1. Jane Doe, Travel Oregon, jane@traveloregon.example, 503-555-0100\n"
            "2. John Smith, Visit Bend, john@visitbend.example, 541-555-0199\n"
            "3. Amy Lee, City of Sisters, amy@sistersorg.example\n"
        )
        out, report = validate_and_flag_section(
            prose, registry=self.registry, slot="references"
        )
        self.assertGreaterEqual(report.blocks_replaced, 1)
        self.assertIn("[verify: references", out.casefold())
        self.assertNotIn("jane@traveloregon.example", out.casefold())

    def test_verify_gap_format(self) -> None:
        tag = verify_gap(
            "references",
            "no public ClientList match with website work type; Thrive Guides blocked (Confirm)",
        )
        self.assertEqual(
            tag,
            "[VERIFY: references — no public ClientList match with website work type; "
            "Thrive Guides blocked (Confirm)]",
        )


class HardFactsSharedTests(unittest.TestCase):
    def test_extracts_ceiling(self) -> None:
        text = (
            "Section 2.4 Compensation. The contract is a fixed-price ceiling of $2,950,000.\n"
            "Overall Capabilities 35 points\nBrand Marketing Plan 35 points\n"
        )
        facts = extract_rfp_hard_facts(text)
        blob = " ".join(facts["contract_value_lines"]).replace(",", "")
        self.assertTrue("2950000" in blob or "2.95" in blob.lower() or "$3M" in blob)


if __name__ == "__main__":
    unittest.main()
