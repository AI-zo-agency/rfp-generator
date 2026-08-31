"""Broken [VERIFY] leftovers — ] inside the tag body + scrub orphan tails."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_manual_flags import (
    VERIFY_TAG_RE,
    repair_orphan_verify_leftovers,
    repair_orphan_verify_leftovers_in_draft,
    sanitize_verify_tag_interior,
)
from app.services.proposal_scan_rfp_contradictions import (
    ContradictionFinding,
    _append_verify_note,
)
from app.services.proposal_verify_optional_scrub import (
    strip_verify_tags_not_required_by_rfp,
)


class SanitizeVerifyInteriorTests(unittest.TestCase):
    def test_strips_brackets_so_tag_stays_one_match(self) -> None:
        raw = 'Section "Technical Proposal"] with no actual content'
        cleaned = sanitize_verify_tag_interior(raw)
        self.assertNotIn("]", cleaned)
        self.assertNotIn("[", cleaned)
        note = f"[VERIFY: resolve RFP contradiction — {cleaned} | RFP requires: fee schedule]"
        matches = VERIFY_TAG_RE.findall(note)
        self.assertEqual(len(matches), 1)
        self.assertIn("Technical Proposal", matches[0])
        self.assertIn("RFP requires", matches[0])


class AppendVerifyNoteSanitizationTests(unittest.TestCase):
    def test_append_survives_scrub_when_contradiction_has_bracket(self) -> None:
        section = ProposalSection(
            id="rfp-closing-technical_proposal",
            title="Technical Proposal",
            content="## Technical Proposal\n\n",
            status="generated",
        )
        finding = ContradictionFinding(
            section_id=section.id,
            section_title=section.title,
            rfp_requirement=(
                "RFP Project Details section requires a Technical Proposal covering: "
                "experience, operating history, qualifications, proposed fee (pricing guidance)"
            ),
            manuscript_contradiction=(
                'Section "Technical Proposal"] with no actual content. '
                "This is a required submission"
            ),
            severity="major",
            fix_action="verify",
        )
        updated = _append_verify_note(section, finding)
        body = updated.content or ""
        self.assertEqual(len(VERIFY_TAG_RE.findall(body)), 1)
        scrubbed, removed = strip_verify_tags_not_required_by_rfp(body, rfp_text="")
        self.assertGreaterEqual(removed, 1)
        self.assertNotIn("RFP requires:", scrubbed)
        self.assertNotIn("no actual content", scrubbed)
        self.assertNotIn("pricing gui", scrubbed)


class OrphanLeftoverRepairTests(unittest.TestCase):
    def test_repairs_exact_ui_orphan_fragment(self) -> None:
        # Produced when VERIFY_TAG_RE matches only through the first ] then scrub
        # deletes that prefix — leaves this tail (screenshot on Orland Park).
        orphan = (
            "\"' with no actual content. This is a required submissi — RFP requires: "
            "RFP Project Details section requires a Technical Proposal covering: "
            "experience, operating history, qualifications, proposed fee (pricing gui]"
        )
        body = f"{orphan}\n\n## Technical Proposal\n\n"
        cleaned, n = repair_orphan_verify_leftovers(body)
        self.assertGreaterEqual(n, 1)
        self.assertNotIn("RFP requires:", cleaned)
        self.assertNotIn("no actual content", cleaned)
        self.assertNotIn("pricing gui", cleaned)
        self.assertIn("## Technical Proposal", cleaned)

    def test_draft_hollow_section_gets_manual_fill_stub(self) -> None:
        orphan = (
            " with no actual content. This is a required submission | RFP requires: "
            "a Technical Proposal covering experience (pricing gui]"
        )
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="rfp-closing-technical_proposal",
                    title="Technical Proposal",
                    content=orphan,
                    status="generated",
                )
            ],
            updatedAt="2026-08-26T00:00:00Z",
        )
        out, logs = repair_orphan_verify_leftovers_in_draft(draft)
        body = out.sections[0].content or ""
        self.assertTrue(any("orphan VERIFY" in line for line in logs))
        self.assertIn("[MANUAL FILL: Draft this RFP-required section", body)
        self.assertNotIn("RFP requires:", body)


if __name__ == "__main__":
    unittest.main()
