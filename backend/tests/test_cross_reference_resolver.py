"""An open VERIFY/MANUAL FILL tag must resolve from a fact already stated
elsewhere in the SAME manuscript, generically — no per-RFP wording, and
never by trusting an unverifiable claim.

Observed on a live RFP: "1.4 — Certifications" states plainly that WBENC and
WOSB are "current through April 30, 2027." A different section re-asked the
identical question as an open tag anyway — the document already answered its
own open question and nothing checked. proposal_kb_fact_checker.py only
searches the EXTERNAL knowledge base; this module closes the in-document gap
with the same zero-fabrication discipline: an LLM proposes a match, a
deterministic literal-text check disposes of anything it cannot verify.
"""

from __future__ import annotations

import unittest

from app.services.proposal_cross_reference_resolver import (
    _extract_open_tags,
    _quote_appears_in_manuscript,
    resolve_tags_from_manuscript,
)


class ExtractOpenTagsTests(unittest.TestCase):
    def test_finds_both_tag_flavors(self) -> None:
        content = (
            "Some prose. [VERIFY: expiration date — Sonja to confirm] more text "
            "[MANUAL FILL: Sonja — list of states]."
        )
        tags = _extract_open_tags(content)
        self.assertEqual(len(tags), 2)
        full_texts = [t[0] for t in tags]
        self.assertTrue(any("VERIFY" in t for t in full_texts))
        self.assertTrue(any("MANUAL FILL" in t for t in full_texts))

    def test_bare_short_tags_are_not_real_asks(self) -> None:
        # A tag with no real question text is not worth a resolution attempt.
        content = "Some prose. [VERIFY: x] more text."
        self.assertEqual(_extract_open_tags(content), [])

    def test_no_tags_in_clean_prose(self) -> None:
        self.assertEqual(_extract_open_tags("Nothing but plain sentences here."), [])


class QuoteVerificationGateTests(unittest.TestCase):
    """The deterministic safety gate — this is what makes the LLM step safe."""

    MANUSCRIPT = (
        "Our certification is current through April 30, 2027. "
        "We serve public-sector clients nationally."
    )

    def test_a_genuine_verbatim_quote_passes(self) -> None:
        self.assertTrue(
            _quote_appears_in_manuscript(
                "Our certification is current through April 30, 2027.",
                self.MANUSCRIPT,
            )
        )

    def test_cosmetic_whitespace_and_quote_style_differences_still_pass(self) -> None:
        # LLM copies faithfully but rendering can vary spacing/smart-quotes —
        # the gate must not punish that as if it were a paraphrase.
        noisy = "Our  certification is\ncurrent through April 30, 2027."
        self.assertTrue(_quote_appears_in_manuscript(noisy, self.MANUSCRIPT))

    def test_a_paraphrase_fails(self) -> None:
        """This is the actual safety property: a plausible-sounding but
        NOT-literally-present claim must never be trusted."""
        paraphrase = "Certifications remain valid until spring 2027."
        self.assertFalse(_quote_appears_in_manuscript(paraphrase, self.MANUSCRIPT))

    def test_an_unrelated_sentence_fails(self) -> None:
        self.assertFalse(
            _quote_appears_in_manuscript(
                "We hold a five-year contract with Maricopa County.",
                self.MANUSCRIPT,
            )
        )

    def test_a_short_or_empty_quote_never_passes(self) -> None:
        """A trivially short "quote" could match almost anything by accident —
        the gate has a floor specifically so that can't happen."""
        self.assertFalse(_quote_appears_in_manuscript("the", self.MANUSCRIPT))
        self.assertFalse(_quote_appears_in_manuscript("", self.MANUSCRIPT))


class NoTagsMeansNoLlmCallTests(unittest.IsolatedAsyncioTestCase):
    """A manuscript with nothing to resolve must never spend a call finding
    that out — the function short-circuits before touching the LLM."""

    async def test_a_draft_with_no_open_tags_returns_unchanged(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalSection

        draft = ProposalDraft(
            rfpId="r",
            updatedAt="2026-08-26T00:00:00Z",
            sections=[
                ProposalSection(
                    id="s1", title="Clean Section", content="No open tags here.", status="generated"
                )
            ],
        )
        updated, applied = await resolve_tags_from_manuscript(draft)
        self.assertEqual(applied, [])
        self.assertEqual(updated.sections[0].content, "No open tags here.")

    async def test_only_section_ids_with_no_matching_tags_short_circuits(self) -> None:
        """The chat-driven 'just this section' path: scoping to a section
        with no open tags of its own must never spend a call, even when a
        DIFFERENT, unscoped section in the same draft has open tags."""
        from app.models.proposal import ProposalDraft, ProposalSection

        draft = ProposalDraft(
            rfpId="r",
            updatedAt="2026-08-26T00:00:00Z",
            sections=[
                ProposalSection(id="s1", title="Clean", content="No tags.", status="generated"),
                ProposalSection(
                    id="s2",
                    title="Has a tag",
                    content="[VERIFY: something — Sonja to confirm]",
                    status="generated",
                ),
            ],
        )
        updated, applied = await resolve_tags_from_manuscript(
            draft, only_section_ids={"s1"}
        )
        self.assertEqual(applied, [])
        self.assertEqual(updated.sections[1].content, draft.sections[1].content)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
