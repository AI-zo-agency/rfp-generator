"""Tests for legal attestation gates in Senior Editor KB fact-check."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.evidence_trust.legal_attestation_gate import (
    apply_legal_attestation_gates,
    gate_section_legal_attestations,
    is_locked_legal_verify_tag,
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


class ProcurementGateTests(unittest.TestCase):
    def test_gates_completed_vendor_registration_without_evidence(self) -> None:
        section = ProposalSection(
            id="submission-11",
            title="PDF format proposal submission",
            content=(
                "zö agency completed online vendor registration at www.example.org and "
                "downloaded the complete procurement documents. Registration confirmation "
                "will be included as Attachment A."
            ),
        )
        updated, report = gate_section_legal_attestations(
            section,
            evidence_text="",
            rfp_context="Contract Reporter — No documents have been uploaded.",
        )
        self.assertGreaterEqual(report.procurement_flags, 1)
        self.assertIn("[MANUAL FILL:", updated.content or "")
        self.assertNotIn("Attachment A", updated.content or "")


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

    def test_flags_invented_percent_time_table(self) -> None:
        section = ProposalSection(
            id="agency-team",
            title="Agency team qualifications",
            content=(
                "The percent-time commitments below are the commitments CVVB can hold us to.\n\n"
                "| Role | Name | Percent-Time |\n"
                "| --- | --- | --- |\n"
                "| Executive Sponsor | Sonja Anderson | 10% |\n"
                "| Account Manager | Ron Comer | 35% |\n"
                "| Creative Lead | Curt Schultz | 25-30% |\n"
            ),
        )
        updated, report = gate_section_legal_attestations(section, force=True)
        self.assertGreaterEqual(report.percent_time_flags, 1)
        self.assertIn("[VERIFY: percent time]", updated.content or "")
        self.assertNotIn("| 10% |", updated.content or "")
        self.assertNotIn("| 35% |", updated.content or "")

    def test_scrub_invented_percent_time_public(self) -> None:
        from app.services.evidence_trust.legal_attestation_gate import (
            scrub_invented_percent_time,
        )

        body = (
            "| Role | Percent-Time |\n"
            "| Sonja | 10% |\n"
            "| Ron | 35% |\n"
        )
        scrubbed, n = scrub_invented_percent_time(body)
        self.assertEqual(n, 2)
        self.assertNotIn("10%", scrubbed)
        self.assertIn("[VERIFY: percent time]", scrubbed)

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


class NoHardcodedClientSteeringTests(unittest.TestCase):
    """The RNO injection mechanism is gone and must not come back.

    A keyword detector classified each RFP and, on a "health/coalition" match,
    appended a FLAG naming Recovery Network of Oregon into references or
    experience. Its acronym alternative `ARCHI` was unanchored under a global
    (?i) flag, so "social media architecture" matched — and a garlic-festival
    proposal was told to cite a health-stigma case study.

    Which past work fits an RFP is decided by KB retrieval and the evidence
    trust gate. A client name in a module or prompt cannot know that, so the
    detector, the flag, and the scrubber written to clean up after it were all
    removed rather than re-tuned.
    """

    def test_the_injection_api_is_gone(self) -> None:
        import app.services.evidence_trust.legal_attestation_gate as gate

        for removed in (
            "ensure_rno_flagged_for_health_rfp",
            "strip_rno_flags_when_not_health_rfp",
            "rfp_needs_health_coalition_proof",
            "_HEALTH_COALITION_RFP_RE",
            "_RNO_FLAG",
        ):
            self.assertFalse(
                hasattr(gate, removed), f"{removed} should no longer exist"
            )

    def test_no_client_name_steering_left_in_generic_prompts(self) -> None:
        """Prompts must not tell the model which client to cite."""
        import pathlib as _pl

        root = _pl.Path(__file__).resolve().parents[1] / "app" / "services"
        offenders: list[str] = []
        for path in root.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            for num, line in enumerate(path.read_text().splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or "Recovery Network of Oregon" not in line:
                    continue
                lowered = line.lower()
                if any(verb in lowered for verb in ("include", "prefer", "proof point")):
                    offenders.append(f"{path.name}:{num}")
        self.assertEqual(offenders, [], f"client-name steering found: {offenders}")


class ConferenceAttendanceGateTests(unittest.TestCase):
    def test_gates_false_past_attendance_before_event_date(self) -> None:
        from datetime import date

        from app.services.evidence_trust.legal_attestation_gate import (
            _replace_false_conference_attendance,
        )

        body = (
            "We attended the Mandatory Pre-Proposal Conference on Tuesday, "
            "September 15, 2026. Our representative was present at the designated "
            "date and time, signed in with District staff, and participated in "
            "the full conference proceedings.\n\n"
            "Attendee of Record: Confirm before submit — Insert name of zö "
            "representative who attended"
        )
        updated, n = _replace_false_conference_attendance(
            body,
            rfp_context="Mandatory Pre-Proposal Conference September 15, 2026",
            reference_date=date(2026, 8, 31),
        )
        self.assertGreaterEqual(n, 1)
        self.assertIn("MANUAL FILL", updated)
        self.assertNotIn("We attended the Mandatory", updated)

    def test_gate_section_conference_attendance(self) -> None:
        section = ProposalSection(
            id="conf",
            title="Evidence of Mandatory Pre-Proposal Conference Attendance",
            content=(
                "We attended the Mandatory Pre-Proposal Conference on September 15, 2026. "
                "Our representative signed in with District staff."
            ),
        )
        updated, report = gate_section_legal_attestations(section)
        self.assertGreaterEqual(report.conference_attendance_flags, 1)
        self.assertIn("MANUAL FILL", updated.content or "")


if __name__ == "__main__":
    unittest.main()
