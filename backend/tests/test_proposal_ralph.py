"""Ralph — RFP fidelity (page limit when stated + anti-invention)."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_ralph import (
    WORDS_PER_PAGE,
    apply_ralph_to_draft,
    ralph_document_word_budget,
    ralph_non_draft_reserve_fraction,
    strip_invented_asset_promises,
)


def _section(
    sid: str,
    content: str,
    *,
    word_target: int | None = None,
    title: str | None = None,
    weight: float | None = None,
) -> ProposalSection:
    kwargs: dict = {
        "id": sid,
        "title": title or sid,
        "content": content,
    }
    if word_target is not None:
        kwargs["wordTarget"] = word_target
    if weight is not None:
        kwargs["evaluationWeight"] = weight
    return ProposalSection(**kwargs)


class RalphFidelityTests(unittest.TestCase):
    def test_short_rfp_holds_larger_reserve(self) -> None:
        self.assertGreater(
            ralph_non_draft_reserve_fraction(12),
            ralph_non_draft_reserve_fraction(30),
        )

    def test_twelve_page_budget_is_under_hard_cap(self) -> None:
        budget = ralph_document_word_budget(12)
        assert budget is not None
        self.assertLessEqual(budget, int(12 * WORDS_PER_PAGE * 0.92))
        self.assertGreater(budget, 2000)

    def test_strips_invented_designer_diagram_notes(self) -> None:
        body = (
            "We will report monthly.\n\n"
            "[DESIGNER NOTE: At a Glance timeline — horizontal milestone graphic]\n\n"
            "Next steps follow."
        )
        cleaned, logs = strip_invented_asset_promises(body)
        self.assertNotIn("DESIGNER NOTE", cleaned)
        self.assertTrue(any("designer-note" in x for x in logs))
        self.assertIn("report monthly", cleaned)

    def test_replaces_fake_attached_diagram_claims(self) -> None:
        body = "Please see the attached reporting dashboard diagram for KPIs."
        cleaned, logs = strip_invented_asset_promises(body)
        self.assertIn("[VERIFY:", cleaned)
        self.assertTrue(logs)

    def test_no_page_limit_does_not_hard_fit(self) -> None:
        fat = "word " * 400
        draft = ProposalDraft(
            rfpId="rfp-ralph",
            sections=[_section(f"s-{i}", fat, word_target=400) for i in range(10)],
            updatedAt="2026-01-01T00:00:00Z",
        )
        before = sum(len((s.content or "").split()) for s in draft.sections)
        updated, logs = apply_ralph_to_draft(
            draft,
            page_limit=None,
            rfp_text="This solicitation has no page restriction mentioned.",
        )
        after = sum(len((s.content or "").split()) for s in updated.sections)
        self.assertEqual(after, before)
        self.assertFalse(any("page-hard-fit" in x for x in logs))

    def test_stated_page_limit_hard_fits_unprotected_first(self) -> None:
        # 5 pages → budget ~1365 words with short-RFP reserve.
        identity = "We are zö agency with thirteen years of destination marketing experience. " * 8
        scored = (
            "Scored methodology covering Meta Business Suite targeting overnight visitation "
            "conversion and monthly reporting. "
        ) * 40
        fluff = ("Padding narrative about generic social engagement and brand voice. " * 50) + (
            "\n\nMore fluff paragraph about trends.\n\nEven more fluff about algorithms."
        )
        draft = ProposalDraft(
            rfpId="rfp-ralph",
            sections=[
                _section("section-1-who-we-are", identity, title="Who We Are", word_target=200),
                _section(
                    "rfp-method",
                    scored,
                    title="Methodology",
                    word_target=300,
                    weight=25.0,
                ),
                _section("rfp-fluff-a", fluff, title="Extra Narrative A", word_target=200),
                _section("rfp-fluff-b", fluff, title="Extra Narrative B", word_target=200),
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        budget = ralph_document_word_budget(5)
        assert budget is not None
        updated, logs = apply_ralph_to_draft(
            draft,
            page_limit=None,
            rfp_text="The proposal is limited to five (5) pages.",
        )
        after = sum(len((s.content or "").split()) for s in updated.sections)
        self.assertLessEqual(after, budget)
        self.assertTrue(any("page-hard-fit" in x or "page-limit:" in x for x in logs))
        who = next(s for s in updated.sections if s.id == "section-1-who-we-are")
        method = next(s for s in updated.sections if s.id == "rfp-method")
        # Identity untouched; scored kept meaningful.
        self.assertEqual(
            len((who.content or "").split()),
            len(identity.split()),
        )
        self.assertGreaterEqual(len((method.content or "").split()), 120)

    def test_apply_ralph_trims_section_that_overshoots_word_target(self) -> None:
        overshoot = "word " * 800
        draft = ProposalDraft(
            rfpId="rfp-ralph",
            sections=[_section("approach", overshoot, word_target=400)],
            updatedAt="2026-01-01T00:00:00Z",
        )
        updated, logs = apply_ralph_to_draft(draft, page_limit=None)
        after = len((updated.sections[0].content or "").split())
        self.assertLessEqual(after, 500)
        self.assertTrue(any("trim-overshoot" in x for x in logs))

    def test_budget_fee_table_survives_hard_fit(self) -> None:
        fee_table = (
            "## Fee Detail by Phase\n\n"
            "| Role | Hours | Rate | Total |\n|---|---|---|---|\n"
            + "\n".join(f"| Strategist | {i} | $150 | ${i * 150} |" for i in range(1, 40))
        )
        body = ("Transparency and pass-through media. " * 20) + "\n\n" + fee_table
        fluff = ("Generic padding sentence about social media. " * 80) + (
            "\n\nMore.\n\nMore again."
        )
        draft = ProposalDraft(
            rfpId="rfp-budget-ralph",
            sections=[
                _section("budget", body, title="Budget & Pricing", word_target=80),
                _section("fluff", fluff, title="Extra Padding", word_target=200),
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        updated, _logs = apply_ralph_to_draft(
            draft,
            page_limit=3,
            rfp_text="The proposal shall not exceed three (3) pages.",
        )
        budget_sec = next(s for s in updated.sections if s.id == "budget")
        self.assertIn("Fee Detail by Phase", budget_sec.content or "")

    def test_reassert_after_content_passes_still_enforces_limit(self) -> None:
        """Later integrity/fill growth must not leave the draft over the RFP cap."""
        from app.services.proposal_ralph import reassert_rfp_page_limit_after_content_passes

        fluff = "word " * 500
        draft = ProposalDraft(
            rfpId="rfp-reassert",
            sections=[
                _section(f"pad-{i}", fluff, title=f"Padding {i}", word_target=200)
                for i in range(8)
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        budget = ralph_document_word_budget(4)
        assert budget is not None
        # Simulate post-Ralph growth (hollow fill / pointer inserts).
        grown = draft.model_copy(
            update={
                "sections": [
                    s.model_copy(
                        update={"content": (s.content or "") + (" extra " * 80)}
                    )
                    for s in draft.sections
                ]
            }
        )
        updated, logs = reassert_rfp_page_limit_after_content_passes(
            grown,
            page_limit=4,
            rfp_text="Proposals are limited to four (4) pages.",
            label="unit-test",
        )
        after = sum(len((s.content or "").split()) for s in updated.sections)
        self.assertLessEqual(after, budget)
        self.assertTrue(any("reassert:unit-test" in x for x in logs))
        self.assertTrue(any("page-hard-fit" in x or "page-limit:" in x for x in logs))


if __name__ == "__main__":
    unittest.main()
