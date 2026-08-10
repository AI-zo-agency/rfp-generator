"""Section chat must find the named section from the user ask before patching."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_section_editor import _resolve_section_from_message


def _sec(sid: str, title: str) -> ProposalSection:
    return ProposalSection(
        id=sid,
        title=title,
        content="body",
        word_target=200,
        required=True,
        custom=False,
        status="generated",
        source="rfp",
    )


class ResolveSectionFromMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.draft = ProposalDraft(
            rfpId="r1",
            updatedAt=datetime.now(timezone.utc).isoformat(),
            sections=[
                _sec("rfp-sec-2", "Past Performance and References"),
                _sec("rfp-sec-3", "Technical Ability"),
                _sec("section-1-who-we-are", "1.1 — Who We Are"),
            ],
        )

    def test_named_past_performance_beats_open_tab(self) -> None:
        hit = _resolve_section_from_message(
            self.draft,
            "Clean the Past Performance and References section so all references are relevant",
            "rfp-sec-3",
        )
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.id, "rfp-sec-2")

    def test_fuzzy_title_tokens(self) -> None:
        self.draft.sections[0] = _sec("rfp-sec-2", "2 — Past Performance & References")
        hit = _resolve_section_from_message(
            self.draft,
            "clean past performance references — drop irrelevant ones",
            "rfp-sec-3",
        )
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.id, "rfp-sec-2")

    def test_generic_ask_keeps_open_tab(self) -> None:
        hit = _resolve_section_from_message(
            self.draft, "make this tighter", "rfp-sec-3"
        )
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.id, "rfp-sec-3")

    def test_umatilla_not_hijacked_by_incidental_references_mention(self) -> None:
        """'before the References fix' must not steal the Umatilla case study."""
        self.draft.sections = [
            _sec("s1", "1. Cover Letter"),
            _sec("s2", "2. Who We Are"),
            _sec(
                "section-3-work-umatilla",
                "3.1 — City of Umatilla Digital Campaign",
            ),
            _sec("rfp-ref-21", "21. References — Current Clients Within Past Two Years"),
        ]
        ask = (
            "1. Section 11 (Umatilla case study) still misrepresents what the "
            "engagement actually was. I flagged this before the References fix, "
            "and it hasn't been addressed. The verified source file is entirely "
            "about the Rock the Locks Festival. Needs a rewrite."
        )
        hit = _resolve_section_from_message(self.draft, ask, "rfp-ref-21")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.id, "section-3-work-umatilla")

    def test_intentional_references_fix_still_resolves(self) -> None:
        self.draft.sections = [
            _sec("s1", "1. Cover Letter"),
            _sec("rfp-ref-21", "21. References — Current Clients"),
        ]
        hit = _resolve_section_from_message(
            self.draft,
            "Fix §21 References only. Replace upon request with KB contacts.",
            "s1",
        )
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.id, "rfp-ref-21")

    def test_implement_budget_here_stays_on_open_compliance_tab(self) -> None:
        self.draft.sections = [
            _sec(
                "compliance",
                "General Requirements Compliance Statement — SOW, Timelines, Budgets",
            ),
            _sec("cost", "Cost of Base Proposal / Fee Schedule"),
        ]
        hit = _resolve_section_from_message(
            self.draft,
            "implement budget table here",
            "compliance",
        )
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.id, "compliance")

    def test_rfp_fit_eval_stays_on_open_tab_not_umatilla_our_work(self) -> None:
        """Tourism examples tab: 'is Umatilla best for this RFP?' must not jump to 3.1."""
        self.draft.sections = [
            _sec(
                "section-3-work-umatilla",
                "3.1 — City of Umatilla Digital Campaign",
            ),
            _sec(
                "rfp-tourism-sm",
                "Examples of Tourism or Destination Marketing Social Media Accounts Managed",
            ),
        ]
        ask = "in this case study is Umatilla best suited for this rfp case?"
        hit = _resolve_section_from_message(self.draft, ask, "rfp-tourism-sm")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.id, "rfp-tourism-sm")

    def test_explicit_rewrite_still_targets_umatilla_our_work(self) -> None:
        self.draft.sections = [
            _sec(
                "section-3-work-umatilla",
                "3.1 — City of Umatilla Digital Campaign",
            ),
            _sec("rfp-tourism-sm", "Tourism Social Media Examples"),
        ]
        ask = "rewrite the Umatilla case study with KB evidence"
        hit = _resolve_section_from_message(self.draft, ask, "rfp-tourism-sm")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.id, "section-3-work-umatilla")

    def test_generic_edit_stays_on_open_tab(self) -> None:
        self.draft.sections = [
            _sec("section-3-work-umatilla", "3.1 — City of Umatilla Digital Campaign"),
            _sec("rfp-tourism-sm", "Tourism Social Media Examples"),
        ]
        hit = _resolve_section_from_message(
            self.draft, "make this tighter and more RFP-aligned", "rfp-tourism-sm"
        )
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.id, "rfp-tourism-sm")

    def test_compliance_with_budgets_word_is_not_cost_section(self) -> None:
        from app.services.proposal_budget_playbook import section_is_budget_related
        from app.models.proposal import ProposalSection

        compliance = ProposalSection(
            id="c",
            title=(
                "General Requirements Compliance Statement — SOW, Timelines, "
                "Budgets, Reporting, Records Retention (Section II)"
            ),
            content="x",
            status="generated",
            source="rfp",
            mode="write",
        )
        cost = ProposalSection(
            id="cost",
            title="Cost of Base Proposal",
            content="x",
            status="generated",
            source="rfp",
            mode="write",
        )
        self.assertFalse(section_is_budget_related(compliance))
        self.assertTrue(section_is_budget_related(cost))


if __name__ == "__main__":
    unittest.main()
