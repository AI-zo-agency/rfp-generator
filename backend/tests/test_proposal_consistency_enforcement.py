"""Tests for deterministic proposal consistency enforcement."""

from __future__ import annotations

import unittest

from app.models.proposal import (
    ManuscriptLocks,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.services.proposal_consistency_enforcement import (
    apply_consistency_enforcement,
    apply_first_pass_manuscript_polish,
    compress_schedule_restating_approach,
    ensure_signed_cover_designer_note,
    infer_rfp_delivery_window_weeks,
    max_week_number_claimed,
    scrub_conflicting_primary_contact,
    scrub_duplicate_reference_emails,
    scrub_schedule_calendar_overrun,
)


class PrimaryContactScrubTests(unittest.TestCase):
    def test_rewrites_haley_as_primary_to_locked_ron(self) -> None:
        body = (
            "Ron Comer leads strategy. "
            "Haley Neff is our dedicated primary contact for day-to-day liaison."
        )
        out, logs = scrub_conflicting_primary_contact(body, locked_name="Ron Comer")
        self.assertTrue(logs)
        self.assertIn("Ron Comer", out)
        self.assertNotIn("Haley Neff is our dedicated primary", out)

    def test_leaves_haley_as_secondary(self) -> None:
        body = "Haley Neff provides backup support when the primary contact is unavailable."
        out, logs = scrub_conflicting_primary_contact(body, locked_name="Ron Comer")
        self.assertEqual(logs, [])
        self.assertIn("Haley Neff", out)


class DuplicateReferenceEmailTests(unittest.TestCase):
    def test_collapses_triplicate_sonja_email(self) -> None:
        body = (
            "1. City A — sonja@zo.agency\n"
            "2. City B — sonja@zo.agency\n"
            "3. City C — sonja@zo.agency\n"
        )
        out, logs = scrub_duplicate_reference_emails(body)
        self.assertTrue(logs)
        self.assertEqual(out.casefold().count("sonja@zo.agency"), 1)
        self.assertIn("[VERIFY: distinct reference contact", out)


class ScheduleApproachDedupeTests(unittest.TestCase):
    def test_compresses_schedule_that_restates_approach_phases(self) -> None:
        approach = ProposalSection(
            id="approach",
            title="12. Project Approach and Work Plan",
            content=(
                "Phase 1: Discovery & Research\nWe begin with stakeholders.\n\n"
                "Phase 2: Strategy Development\nMessaging framework.\n\n"
                "Phase 3: Creative Development\nAssets.\n\n"
                "Phase 4: Implementation Planning\nRoadmap.\n\n"
                "Phase 5: Final Deliverables\nHandoff.\n"
            ),
            status="generated",
        )
        schedule = ProposalSection(
            id="schedule",
            title="15. Project Schedule",
            content=(
                "Phase 1: Discovery & Research (Weeks 1-2) Stakeholder meetings.\n\n"
                "Phase 2: Strategy Development (Weeks 3-4) Brand positioning.\n\n"
                "Phase 3: Creative Development (Weeks 5-7) Campaign theme.\n\n"
                "Phase 4: Implementation Planning (Weeks 8-9) Measurement.\n\n"
                "Phase 5: Final Deliverables (Week 10) Transition package.\n"
            ),
            status="generated",
        )
        out, n = compress_schedule_restating_approach([approach, schedule])
        self.assertEqual(n, 1)
        sched = next(s for s in out if s.id == "schedule")
        self.assertIn("methodology detail lives", sched.content or "")
        self.assertIn("RFP award→launch", sched.content or "")
        self.assertNotIn("Weeks 1-2", sched.content or "")


class CalendarOverrunTests(unittest.TestCase):
    def test_infers_window_from_within_weeks_of_award(self) -> None:
        text = "Campaign must launch within 4 weeks of award / notice to proceed."
        self.assertEqual(infer_rfp_delivery_window_weeks(text), 4)

    def test_max_week_from_phase_labels(self) -> None:
        body = (
            "Phase 4: Implementation Planning (Weeks 8-9)\n"
            "Phase 5: Final Deliverables (Week 10)\n"
        )
        self.assertEqual(max_week_number_claimed(body), 10)

    def test_scrubs_schedule_overrunning_short_rfp_window(self) -> None:
        schedule = ProposalSection(
            id="schedule",
            title="15. Project Schedule",
            content=(
                "Phase 1 (Weeks 1-2)\nPhase 2 (Weeks 3-4)\n"
                "Phase 3 (Weeks 5-7)\nPhase 4 (Weeks 8-9)\n"
                "Phase 5 (Week 10) Transition.\n"
            ),
            status="generated",
        )
        rfp = (
            "Work begins upon award. The talent campaign must launch within "
            "4 weeks of contract execution / notice to proceed."
        )
        out, logs = scrub_schedule_calendar_overrun([schedule], rfp_text=rfp)
        self.assertTrue(logs)
        body = out[0].content or ""
        self.assertIn("RFP constraint", body)
        self.assertIn("[VERIFY:", body)
        self.assertNotIn("Week 10", body)

    def test_scrubs_approach_week_labels_keeps_methodology(self) -> None:
        approach = ProposalSection(
            id="approach",
            title="12. Project Approach and Work Plan",
            content=(
                "Phase 4: Implementation Planning (Weeks 8-9) involving "
                "performance measurement plans.\n\n"
                "Phase 5: Final Deliverables (Week 10) involving transition.\n"
            ),
            status="generated",
        )
        rfp = (
            "Award mid-cycle. Launch must occur within 3 weeks of award."
        )
        out, logs = scrub_schedule_calendar_overrun([approach], rfp_text=rfp)
        self.assertTrue(logs)
        body = out[0].content or ""
        self.assertIn("performance measurement plans", body)
        self.assertIn("[VERIFY: fit RFP award→launch window]", body)
        self.assertNotIn("Weeks 8-9", body)
        self.assertNotIn("Week 10", body)


class EndToEndConsistencyTests(unittest.TestCase):
    def test_apply_enforcement_fixes_refs_and_primary(self) -> None:
        locks = ManuscriptLocks(
            primaryContactName="Ron Comer",
            primaryContactTitle="Senior Account Manager",
            requiredKpis=[],
            updatedAt="2026-08-07T00:00:00Z",
        )
        research = ProposalResearchCache(
            rfpId="rfp-osh",
            manuscriptLocks=locks,
            updatedAt="2026-08-07T00:00:00Z",
        )
        draft = ProposalDraft(
            rfpId="rfp-osh",
            sections=[
                ProposalSection(
                    id="s1",
                    title="1.2 Org",
                    content="Haley Neff is the dedicated primary contact for this account.",
                    status="generated",
                ),
                ProposalSection(
                    id="s11",
                    title="11. Firm Qualifications and References",
                    content=(
                        "Refs: a@x.com sonja@zo.agency ; b@x.com sonja@zo.agency ; "
                        "c@x.com sonja@zo.agency"
                    ),
                    status="generated",
                ),
            ],
            updatedAt="2026-08-07T00:00:00Z",
        )
        out, logs = apply_consistency_enforcement(draft, research=research)
        self.assertTrue(logs)
        org = next(s for s in out.sections if s.id == "s1")
        self.assertIn("Ron Comer", org.content or "")
        refs = next(s for s in out.sections if s.id == "s11")
        self.assertEqual((refs.content or "").casefold().count("sonja@zo.agency"), 1)


class SignedCoverDesignerNoteTests(unittest.TestCase):
    def test_adds_designer_note_when_rfp_requires_signed_cover(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="cover",
                    title="Cover Letter",
                    content="Dear Selection Committee,\n\nWe are pleased to submit.",
                    status="generated",
                ),
            ],
            updatedAt="2026-08-07T00:00:00Z",
        )
        out, logs = ensure_signed_cover_designer_note(
            draft,
            rfp_text="Proposals must include a physically signed cover letter.",
        )
        self.assertTrue(logs)
        body = out.sections[0].content or ""
        self.assertIn("[DESIGNER NOTE:", body)
        self.assertIn("physically signed cover letter", body.casefold())
        self.assertNotRegex(body, r"(?i)\$\d|notary\s*#\s*\d")

    def test_idempotent_when_note_already_present(self) -> None:
        note = (
            "[DESIGNER NOTE: Attach the physically signed cover letter PDF. "
            "Do not invent signature dates.]"
        )
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="cover",
                    title="Letter of Transmittal",
                    content=f"{note}\n\nBody text.",
                    status="generated",
                ),
            ],
            updatedAt="2026-08-07T00:00:00Z",
        )
        out, logs = ensure_signed_cover_designer_note(
            draft,
            attachment_labels=[
                "Physically signed cover letter / letter of transmittal (attachment)"
            ],
        )
        self.assertEqual(logs, [])
        self.assertEqual((out.sections[0].content or "").count("[DESIGNER NOTE:"), 1)

    def test_skips_when_rfp_does_not_require_signed_cover(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="cover",
                    title="Cover Letter",
                    content="We submit this proposal.",
                    status="generated",
                ),
            ],
            updatedAt="2026-08-07T00:00:00Z",
        )
        out, logs = ensure_signed_cover_designer_note(
            draft, rfp_text="Submit a narrative proposal by email."
        )
        self.assertEqual(logs, [])
        self.assertNotIn("[DESIGNER NOTE:", out.sections[0].content or "")

    def test_first_pass_polish_adds_cover_note(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="cover",
                    title="Cover Letter",
                    content="Dear Committee,\n\nWe submit our response.",
                    status="generated",
                ),
                ProposalSection(
                    id="cost",
                    title="Cost Proposal",
                    content="Total not to exceed $20,000.",
                    status="generated",
                ),
            ],
            updatedAt="2026-08-07T00:00:00Z",
        )
        out, logs = apply_first_pass_manuscript_polish(
            draft,
            rfp_text=(
                "Include a physically signed cover letter with the proposal. "
                "Authorized signature page / wet-ink signature required."
            ),
        )
        self.assertTrue(any("DESIGNER NOTE" in line for line in logs))
        blob = "\n".join(s.content or "" for s in out.sections)
        self.assertIn("physically signed cover letter", blob.casefold())
        self.assertIn("authorized signature page", blob.casefold())


if __name__ == "__main__":
    unittest.main()
