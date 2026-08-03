"""Outline sections must be REQUESTED by the RFP, not merely mentioned in it.

Real symptom: a 12-page-capped RFP produced 9 sections the buyer never asked
for — PERA Retiree Notification, Sex Offender Registration Compliance,
Acknowledgement of Contract Terms, a standalone WCAG statement. Each survived
because its topic appeared somewhere in the solicitation, so the filter's
"mentioned" test passed. Mentioned is not requested.
"""

from __future__ import annotations

import unittest

from app.services.proposal_closing_package import rfp_requires_topic
from app.services.proposal_outline_dedup import filter_lean_outline_sections

# Procedural clauses: standing obligations, never proposal contents.
PROCEDURAL_RFP = """
1.6 ADDENDA. The County may issue addenda prior to the due date. Vendors must
monitor ColoradoVSS for addenda affecting this proposal.
1.10 SEX OFFENDER REGISTRATION. Contractor staff working on campus must comply
with sex offender registration requirements.
1.11 PERA. If a PERA retiree performs work, the contractor shall notify the
County after award.
Minimum qualifications: vendors must meet WCAG 2.1 AA accessibility.
SECTION IV DOCUMENTATION. Each quote shall contain a company overview, past
performance examples, the assigned team, methodology, and pricing.
"""


def _sections(*titles: str):
    return [
        {"id": f"rfp-sec-{i}", "title": t, "required": False, "order": i}
        for i, t in enumerate(titles, start=1)
    ]


def _kept_titles(sections, rfp: str) -> list[str]:
    kept, _dropped = filter_lean_outline_sections(sections, rfp_context=rfp)
    return [s["title"] for s in kept]


class RequestedTopicTests(unittest.TestCase):
    def test_procedural_clause_is_not_a_request(self) -> None:
        self.assertFalse(rfp_requires_topic(PROCEDURAL_RFP, ["pera", "retiree"]))
        self.assertFalse(
            rfp_requires_topic(PROCEDURAL_RFP, ["sex offender", "registration"])
        )

    def test_real_submission_requirement_is_a_request(self) -> None:
        self.assertTrue(
            rfp_requires_topic(PROCEDURAL_RFP, ["methodology"]),
        )

    def test_empty_inputs_are_safe(self) -> None:
        self.assertFalse(rfp_requires_topic("", ["methodology"]))
        self.assertFalse(rfp_requires_topic(PROCEDURAL_RFP, []))
        self.assertFalse(rfp_requires_topic(PROCEDURAL_RFP, ["ab"]))


class OutlineFilterTests(unittest.TestCase):
    def test_requested_sections_kept_unrequested_dropped(self) -> None:
        # Section IV asks for a company overview and methodology; it never asks
        # for a timeline or an "our approach" narrative.
        kept, dropped = filter_lean_outline_sections(
            _sections("Methodology", "Overview", "Timeline", "Our Approach"),
            rfp_context=PROCEDURAL_RFP,
        )
        titles = " | ".join(s["title"] for s in kept)
        joined = " | ".join(dropped)

        self.assertIn("Methodology", titles)
        self.assertIn("Overview", titles)
        self.assertIn("Timeline", joined)
        self.assertIn("Our Approach", joined)

    def test_dropped_reason_distinguishes_mentioned_from_filler(self) -> None:
        _kept, dropped = filter_lean_outline_sections(
            _sections("Timeline"), rfp_context=PROCEDURAL_RFP
        )
        joined = " | ".join(dropped)
        self.assertTrue(
            "mentioned but not requested" in joined or "generic filler" in joined,
            joined,
        )

    def test_requested_sections_survive(self) -> None:
        titles = _kept_titles(
            _sections("Scope of Work", "References"), PROCEDURAL_RFP
        )
        self.assertEqual(len(titles), 2, titles)

    def test_no_rfp_text_keeps_sections(self) -> None:
        kept, _ = filter_lean_outline_sections(
            _sections("Methodology", "Timeline"), rfp_context=""
        )
        self.assertEqual(len(kept), 2)


if __name__ == "__main__":
    unittest.main()
