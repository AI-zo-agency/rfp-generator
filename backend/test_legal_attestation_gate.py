"""Tests for legal attestation gates in Senior Editor KB fact-check."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.evidence_trust.legal_attestation_gate import (
    apply_legal_attestation_gates,
    gate_section_legal_attestations,
    is_locked_legal_verify_tag,
    rfp_needs_health_coalition_proof,
)
from app.services.proposal_manual_flags import _replace_verify_tags_from_blob


def _rfp(**kwargs: object) -> SimpleNamespace:
    base = {
        "title": "ARCHI Health Policy Communications",
        "client": "Georgia State University",
        "sector": "Public Health",
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _draft(*sections: ProposalSection) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-test",
        sections=list(sections),
        updatedAt="2026-07-22T00:00:00+00:00",
    )


class LegalVerifyLockTests(unittest.TestCase):
    def test_locks_everify_and_conflict_tags(self) -> None:
        self.assertTrue(
            is_locked_legal_verify_tag(
                "E-Verify enrollment — unconfirmed in KB — Sonja/Operations must confirm"
            )
        )
        self.assertTrue(
            is_locked_legal_verify_tag(
                "conflict-of-interest disclosure — must be confirmed by Sonja"
            )
        )
        self.assertFalse(is_locked_legal_verify_tag("primary contact name and title"))

    def test_blob_fill_does_not_clear_locked_everify(self) -> None:
        content = (
            "Affidavit: [VERIFY: E-Verify enrollment — unconfirmed in KB — "
            "Sonja/Operations must confirm]"
        )
        blob = "zö maintains active participation in the federal E-Verify system."
        updated, fills = _replace_verify_tags_from_blob(content, blob)
        self.assertEqual(fills, 0)
        self.assertIn("[VERIFY: E-Verify enrollment", updated)


class EVerifyGateTests(unittest.TestCase):
    def test_gates_sworn_everify_assertion(self) -> None:
        section = ProposalSection(
            id="section-20",
            title="20. E-Verify Affidavit",
            content=(
                "The undersigned attests under penalty of perjury that the information "
                "provided regarding E-Verify compliance is true and accurate. "
                "zö maintains active participation in the federal E-Verify system. "
                "False statements may result in contract termination and legal penalties."
            ),
        )
        updated, report = gate_section_legal_attestations(section)
        self.assertGreaterEqual(report.everify_flags, 1)
        self.assertIn("[VERIFY:", updated.content or "")
        self.assertIn("E-Verify", updated.content or "")
        self.assertNotRegex(
            updated.content or "",
            r"(?i)maintains active participation in the federal E-Verify",
        )

    def test_gates_conflict_disclosure_assertion(self) -> None:
        section = ProposalSection(
            id="section-17",
            title="17. Disclosure Statement",
            content=(
                "We have no financial relationships that would create conflicts of interest "
                "with Georgia State University or ARCHI."
            ),
        )
        updated, report = gate_section_legal_attestations(section)
        self.assertGreaterEqual(report.conflict_flags, 1)
        self.assertIn("conflict", (updated.content or "").casefold())
        self.assertIn("[VERIFY:", updated.content or "")
        self.assertNotIn("We have no financial relationships", updated.content or "")


class HoursAndFillerTests(unittest.TestCase):
    def test_flags_invented_staffing_hours(self) -> None:
        section = ProposalSection(
            id="section-staff",
            title="Staffing Plan",
            content=(
                "Annual allocation: Strategy Lead 400 hours, Creative Director 320 hours, "
                "Digital Manager 280 hours, Account Manager 200 hours, Project Coordinator "
                "160 hours."
            ),
        )
        updated, report = gate_section_legal_attestations(section, force=True)
        self.assertGreaterEqual(report.hours_flags, 1)
        self.assertIn("[VERIFY:", updated.content or "")
        self.assertIn("staffing hours", (updated.content or "").casefold())

    def test_replaces_ten_year_filler(self) -> None:
        section = ProposalSection(
            id="section-1",
            title="Who We Are",
            content=(
                "Our 10-year corporate-creative partnership model delivers lasting value."
            ),
        )
        updated, report = gate_section_legal_attestations(section, force=True)
        self.assertGreaterEqual(report.filler_flags, 1)
        self.assertIn("2013", updated.content or "")
        self.assertNotIn("10-year corporate-creative", updated.content or "")


class RnoFlagTests(unittest.TestCase):
    def test_health_rfp_detection(self) -> None:
        self.assertTrue(rfp_needs_health_coalition_proof(_rfp()))  # type: ignore[arg-type]
        self.assertFalse(
            rfp_needs_health_coalition_proof(
                _rfp(title="Website Redesign", client="City IT", sector="Technology")  # type: ignore[arg-type]
            )
        )

    def test_flags_missing_rno_on_health_rfp(self) -> None:
        draft = _draft(
            ProposalSection(
                id="section-18",
                title="18. References",
                content=(
                    "1. Oregon Employment Department — [VERIFY: contact]\n"
                    "2. Ninkasi Brewing — beer rebrand"
                ),
            ),
            ProposalSection(
                id="section-3-work-1",
                title="Case Studies",
                content="Oregon Employment Department digital campaign.",
            ),
        )
        updated, report = apply_legal_attestation_gates(
            draft,
            rfp=_rfp(),  # type: ignore[arg-type]
            rfp_context="ARCHI stigma reduction coalition health policy",
        )
        self.assertEqual(report.rno_flags, 1)
        blob = "\n".join(s.content or "" for s in updated.sections)
        self.assertIn("Recovery Network of Oregon", blob)
        self.assertIn("FLAG FOR SONJA", blob)

    def test_skips_rno_flag_when_already_present(self) -> None:
        draft = _draft(
            ProposalSection(
                id="section-18",
                title="18. References",
                content="1. Recovery Network of Oregon — comparable coalition work",
            ),
        )
        _, report = apply_legal_attestation_gates(draft, rfp=_rfp())  # type: ignore[arg-type]
        self.assertEqual(report.rno_flags, 0)


if __name__ == "__main__":
    unittest.main()
