"""Semantic evidence judgment, with citations that cannot be invented.

Cases are the real ones from two live runs:
  * false positives (Fairmont): CMS/hosting/content-migration asserted Verified
    with nothing in the KB.
  * false negatives (next RFP): 0 of 13 evidenced when WordPress work evidenced
    CMS, three case studies evidenced website redesign, and "improve clarity and
    user flow" evidenced UX.
"""

from __future__ import annotations

import unittest

from app.services.go_no_go_adjudicator import (
    build_adjudication_payload,
    quote_is_grounded,
    rows_from_assessments,
)
from app.services.go_no_go_requirements import RfpRequirement


def _hit(name: str, content: str) -> dict:
    return {"title": name, "content": content}


SHAWN = _hit(
    "04_Bio_ShawnDiCriscio.pdf",
    "Shawn DiCriscio, Web Developer. Specializes in WordPress. Built and "
    "worked on hundreds of websites over 10+ years.",
)
TORRENT = _hit(
    "03_CS_TorrentLaboratories.pdf",
    "Homepage redesign and Articles and Resources page redesign. Goals: "
    "improve clarity and user flow for visitors.",
)

REQS = [
    RfpRequirement(requirement="CMS implementation", isCore=True),
    RfpRequirement(requirement="Website redesign and modernization", isCore=True),
    RfpRequirement(requirement="Government website experience", isCore=True),
]
HITS = {
    "CMS implementation": [SHAWN],
    "Website redesign and modernization": [TORRENT],
    "Government website experience": [TORRENT],
}


class QuoteGroundingTests(unittest.TestCase):
    def test_verbatim_quote_is_grounded(self) -> None:
        self.assertTrue(
            quote_is_grounded("Specializes in WordPress", SHAWN["content"])
        )

    def test_whitespace_differences_are_tolerated(self) -> None:
        self.assertTrue(
            quote_is_grounded("Specializes  in\nWordPress", SHAWN["content"])
        )

    def test_invented_quote_is_not_grounded(self) -> None:
        self.assertFalse(
            quote_is_grounded("Ten years of Drupal CMS migrations", SHAWN["content"])
        )

    def test_trivially_short_quote_rejected(self) -> None:
        self.assertFalse(quote_is_grounded("web", SHAWN["content"]))


class AdjudicationTests(unittest.TestCase):
    def _rows(self, assessments):
        _body, sources = build_adjudication_payload(REQS, HITS)
        return rows_from_assessments(REQS, assessments, sources)

    def test_semantic_match_survives_with_grounded_quote(self) -> None:
        """WordPress evidences CMS — the case keyword matching could not see."""
        rows, rejected = self._rows(
            [
                {
                    "requirement": "CMS implementation",
                    "status": "verified",
                    "kbSource": "04_Bio_ShawnDiCriscio.pdf",
                    "quote": "Specializes in WordPress",
                    "reason": "WordPress is a CMS",
                }
            ]
        )
        cms = next(r for r in rows if r.requirement == "CMS implementation")
        self.assertEqual(cms.status, "verified")
        self.assertIn("WordPress", cms.evidence)
        self.assertEqual(rejected, [])

    def test_ux_language_counts_as_evidence(self) -> None:
        rows, _ = self._rows(
            [
                {
                    "requirement": "Website redesign and modernization",
                    "status": "verified",
                    "kbSource": "03_CS_TorrentLaboratories.pdf",
                    "quote": "Homepage redesign and Articles and Resources page redesign",
                }
            ]
        )
        row = next(
            r for r in rows if r.requirement == "Website redesign and modernization"
        )
        self.assertEqual(row.status, "verified")

    def test_fabricated_quote_is_downgraded(self) -> None:
        """The Fairmont failure mode: an assertion with no grounding."""
        rows, rejected = self._rows(
            [
                {
                    "requirement": "CMS implementation",
                    "status": "verified",
                    "kbSource": "04_Bio_ShawnDiCriscio.pdf",
                    "quote": "Ten years of enterprise Drupal CMS implementations",
                }
            ]
        )
        cms = next(r for r in rows if r.requirement == "CMS implementation")
        self.assertEqual(cms.status, "gap")
        self.assertIn("does not appear", cms.downgrade_reason)
        self.assertEqual(len(rejected), 1)

    def test_citation_to_unretrieved_document_is_downgraded(self) -> None:
        rows, _ = self._rows(
            [
                {
                    "requirement": "CMS implementation",
                    "status": "verified",
                    "kbSource": "03_CS_MunicipalCMS.pdf",
                    "quote": "Specializes in WordPress",
                }
            ]
        )
        cms = next(r for r in rows if r.requirement == "CMS implementation")
        self.assertEqual(cms.status, "gap")
        self.assertIn("was not retrieved", cms.downgrade_reason)

    def test_partial_status_is_preserved(self) -> None:
        rows, _ = self._rows(
            [
                {
                    "requirement": "Website redesign and modernization",
                    "status": "partial",
                    "kbSource": "03_CS_TorrentLaboratories.pdf",
                    "quote": "improve clarity and user flow",
                }
            ]
        )
        row = next(
            r for r in rows if r.requirement == "Website redesign and modernization"
        )
        self.assertEqual(row.status, "partial")

    def test_explicit_gap_keeps_its_reason(self) -> None:
        rows, _ = self._rows(
            [
                {
                    "requirement": "Government website experience",
                    "status": "gap",
                    "reason": "all website case studies are private sector",
                }
            ]
        )
        row = next(
            r for r in rows if r.requirement == "Government website experience"
        )
        self.assertEqual(row.status, "gap")
        self.assertIn("private sector", row.downgrade_reason)

    def test_missing_assessment_defaults_to_gap(self) -> None:
        rows, _ = self._rows([])
        self.assertTrue(all(r.status == "gap" for r in rows))
        self.assertEqual(len(rows), len(REQS))

    def test_payload_includes_every_requirement(self) -> None:
        body, sources = build_adjudication_payload(REQS, HITS)
        for requirement in REQS:
            self.assertIn(requirement.requirement, body)
        self.assertEqual(set(sources), {r.requirement for r in REQS})



class SharedEvidencePoolTests(unittest.TestCase):
    """Evidence retrieved under one requirement must be visible to the others.

    Live failure: every rejection read "cited source
    '02_MasterTemplate_OrgStructure' does not evidence X". Shawn's bio and the
    case studies HAD been retrieved — under different requirements — so the
    model could only cite the org chart, and every claim was correctly but
    uselessly rejected. 9 core requirements went unevidenced as a result.
    """

    ORG = _hit(
        "02_MasterTemplate_OrgStructure.pdf",
        "Organizational structure. Departments: Creative, Accounts, Digital.",
    )

    REQS = [
        RfpRequirement(requirement="Web developer role", isCore=True),
        RfpRequirement(requirement="CMS implementation", isCore=True),
    ]
    # Shawn's bio came back only for the ROLE query, not the CMS query.
    HITS = {
        "Web developer role": [SHAWN],
        "CMS implementation": [ORG],
    }

    def test_bio_from_another_requirement_is_citable(self) -> None:
        _body, sources = build_adjudication_payload(
            self.REQS, self.HITS, all_hits=[SHAWN, self.ORG]
        )
        self.assertIn("04_Bio_ShawnDiCriscio.pdf", sources["CMS implementation"])

    def test_cross_requirement_citation_now_validates(self) -> None:
        _body, sources = build_adjudication_payload(
            self.REQS, self.HITS, all_hits=[SHAWN, self.ORG]
        )
        rows, rejected = rows_from_assessments(
            self.REQS,
            [
                {
                    "requirement": "CMS implementation",
                    "status": "verified",
                    "kbSource": "04_Bio_ShawnDiCriscio.pdf",
                    "quote": "Specializes in WordPress",
                }
            ],
            sources,
        )
        cms = next(r for r in rows if r.requirement == "CMS implementation")
        self.assertEqual(cms.status, "verified")
        self.assertEqual(rejected, [])

    def test_without_shared_pool_the_same_claim_fails(self) -> None:
        """Confirms the isolation really was the cause."""
        _body, sources = build_adjudication_payload(self.REQS, self.HITS)
        rows, rejected = rows_from_assessments(
            self.REQS,
            [
                {
                    "requirement": "CMS implementation",
                    "status": "verified",
                    "kbSource": "04_Bio_ShawnDiCriscio.pdf",
                    "quote": "Specializes in WordPress",
                }
            ],
            sources,
        )
        cms = next(r for r in rows if r.requirement == "CMS implementation")
        self.assertEqual(cms.status, "gap")
        self.assertEqual(len(rejected), 1)

    def test_own_hits_still_come_first(self) -> None:
        _body, sources = build_adjudication_payload(
            self.REQS, self.HITS, all_hits=[SHAWN, self.ORG]
        )
        self.assertEqual(
            next(iter(sources["Web developer role"])), "04_Bio_ShawnDiCriscio.pdf"
        )



class NonCapabilitySourceTests(unittest.TestCase):
    """A pricing document cannot evidence delivery capability.

    Live run validated "Discovery and stakeholder engagement" against
    00_Guide_Pricing.docx because the guide's text happened to contain those
    words. A rate sheet says what zö charges, never what it has delivered.
    """

    GUIDE = _hit(
        "00_Guide_Pricing.docx",
        "Discovery and stakeholder engagement workshops are billed at the "
        "Average tier rate.",
    )
    REQS = [RfpRequirement(requirement="Discovery and stakeholder engagement",
                           isCore=True)]

    def test_pricing_guide_cannot_evidence_capability(self) -> None:
        _b, sources = build_adjudication_payload(
            self.REQS, {"Discovery and stakeholder engagement": [self.GUIDE]}
        )
        rows, rejected = rows_from_assessments(
            self.REQS,
            [{
                "requirement": "Discovery and stakeholder engagement",
                "status": "verified",
                "kbSource": "00_Guide_Pricing.docx",
                "quote": "Discovery and stakeholder engagement workshops",
            }],
            sources,
        )
        self.assertEqual(rows[0].status, "gap")
        self.assertIn("pricing/rate document", rows[0].downgrade_reason)
        self.assertEqual(len(rejected), 1)

    def test_case_studies_and_bios_still_allowed(self) -> None:
        from app.services.go_no_go_adjudicator import source_can_evidence_capability

        for name in ("03_CS_TorrentLaboratories.pdf", "04_Bio_ShawnDiCriscio.pdf",
                     "06_WON_CityOfBend.pdf", "01_companyfacts.docx"):
            self.assertTrue(source_can_evidence_capability(name), name)

    def test_pricing_variants_are_all_blocked(self) -> None:
        from app.services.go_no_go_adjudicator import source_can_evidence_capability

        for name in ("00_Guide_Pricing.docx", "05_Pricing_2026.pdf",
                     "Pricing Guide.docx", "rate-card.xlsx", "price_sheet.pdf"):
            self.assertFalse(source_can_evidence_capability(name), name)


class RecallQueryTests(unittest.TestCase):
    def test_literal_requirement_query_is_always_added(self) -> None:
        from app.services.go_no_go_requirements import parse_requirements

        out = parse_requirements({"requirements": [{
            "requirement": "CMS implementation",
            "kbQueries": ["zö agency something unrelated"],
        }]})
        blob = " | ".join(out[0].kb_queries)
        self.assertIn("CMS implementation", blob)



class EvidenceStateTests(unittest.TestCase):
    """"Nothing in the KB" and "the KB contradicts it" are different findings.

    The first may be fixable by re-ingesting a document; the second never is.
    Collapsing both into "Gap" hid which one a reader was looking at.
    """

    REQS = [RfpRequirement(requirement="Programming and development", isCore=True)]
    CURT = _hit(
        "02_MasterTemplate_OrgStructure.pdf",
        "Curt Schultz, Creative Director. Web Design/Development "
        "(Not Programming) - 15 years.",
    )

    def _rows(self, assessment):
        _b, sources = build_adjudication_payload(
            self.REQS, {"Programming and development": [self.CURT]}
        )
        return rows_from_assessments(self.REQS, [assessment], sources)[0]

    def test_contradicted_is_recorded(self) -> None:
        rows = self._rows({
            "requirement": "Programming and development",
            "status": "gap",
            "evidenceState": "contradicted",
            "reason": "bio explicitly excludes programming",
        })
        self.assertEqual(rows[0].evidence_state, "contradicted")

    def test_absent_is_the_default_for_gaps(self) -> None:
        rows = self._rows({
            "requirement": "Programming and development", "status": "gap",
        })
        self.assertEqual(rows[0].evidence_state, "absent")

    def test_unknown_state_falls_back(self) -> None:
        rows = self._rows({
            "requirement": "Programming and development",
            "status": "gap", "evidenceState": "nonsense",
        })
        self.assertEqual(rows[0].evidence_state, "absent")

    def test_state_is_rendered_in_the_table(self) -> None:
        from app.services.go_no_go_capability import render_capability_table

        rows = self._rows({
            "requirement": "Programming and development",
            "status": "gap", "evidenceState": "contradicted",
            "reason": "bio excludes programming",
        })
        self.assertIn("KB contradicts", render_capability_table(rows))



class LongDocumentWindowingTests(unittest.TestCase):
    """Evidence buried deep in a long file must still reach the model.

    02_MasterTemplate_OrgStructure_AllTeamBios.pdf holds every bio in one file.
    Taking the first N characters shows whoever is alphabetically first and
    hides everyone else, so the adjudicator never sees the person who proves
    the requirement.
    """

    FILLER = "Departmental overview and administrative notes. " * 120
    ROSTER = _hit(
        "02_MasterTemplate_OrgStructure_AllTeamBios.pdf",
        FILLER
        + "Shawn DiCriscio, Web Developer. Specializes in WordPress. "
        + FILLER,
    )
    REQS = [RfpRequirement(requirement="WordPress website development",
                           isCore=True)]

    def test_precondition_evidence_is_past_the_head_slice(self) -> None:
        from app.services.go_no_go_adjudicator import _MAX_DOC_CHARS

        head = self.ROSTER["content"][:_MAX_DOC_CHARS]
        self.assertNotIn("Shawn DiCriscio", head)

    def test_buried_evidence_is_shown_to_the_model(self) -> None:
        body, sources = build_adjudication_payload(
            self.REQS, {"WordPress website development": [self.ROSTER]}
        )
        self.assertIn("Shawn DiCriscio", body)
        self.assertIn(
            "Shawn DiCriscio",
            sources["WordPress website development"][
                "02_MasterTemplate_OrgStructure_AllTeamBios.pdf"
            ],
        )

    def test_quote_from_buried_evidence_validates(self) -> None:
        _b, sources = build_adjudication_payload(
            self.REQS, {"WordPress website development": [self.ROSTER]}
        )
        rows, rejected = rows_from_assessments(
            self.REQS,
            [{
                "requirement": "WordPress website development",
                "status": "verified",
                "kbSource": "02_MasterTemplate_OrgStructure_AllTeamBios.pdf",
                "quote": "Shawn DiCriscio, Web Developer. Specializes in WordPress.",
            }],
            sources,
        )
        self.assertEqual(rows[0].status, "verified")
        self.assertEqual(rejected, [])


if __name__ == "__main__":
    unittest.main()
