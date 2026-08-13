"""General packed KB evidence for section improve — no vertical hardcodes."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.proposal_section_kb_evidence import (
    ACCURATE_KB_EDITOR_RULES,
    build_section_kb_question,
    fetch_packed_section_kb_evidence,
    inject_packed_evidence_into_instruction,
)


class NoVerticalGateTests(unittest.IsolatedAsyncioTestCase):
    """Evidence packing must not depend on a client's industry vocabulary.

    The old gate was a regex listing `destination|tourism|visitor` alongside
    `case stud|results|kpi`, in a module whose docstring promises "No vertical
    hardcodes (tourism, SF Travel, etc.)". A tourism section matched and got KB
    evidence; the same section for a roofing or pond client did not, and was left to
    write from nothing.
    """

    async def _fetch(self, *, title: str, content: str) -> str:
        with patch(
            "app.services.kb_rag_retrieve.retrieve_for_question",
            new=AsyncMock(return_value=("Crew of 12 certified installers.", ["kb.md"], [])),
        ):
            block, _sources = await fetch_packed_section_kb_evidence(
                section_title=title,
                section_content=content,
            )
        return block

    async def test_tourism_section_gets_evidence(self):
        block = await self._fetch(
            title="Destination Marketing Accounts Managed",
            content="We ran visitor campaigns.",
        )
        self.assertIn("Crew of 12", block)

    async def test_non_tourism_section_gets_the_same_evidence(self):
        """The regression: no industry words, previously skipped entirely."""
        block = await self._fetch(
            title="Our Crews",
            content="We install pond liners across three counties.",
        )
        self.assertIn("Crew of 12", block)


def test_question_carries_entities_named_only_in_prose():
    """The regression that killed heading extraction.

    Entity hints were pulled with a regex that only matched markdown headings
    (`## X` / `**X**`), so a client named in an ordinary sentence never reached the
    retriever. The section prose is now passed through directly, so the semantic
    retriever sees the real text regardless of formatting.
    """
    q = build_section_kb_question(
        section_title="Client Examples",
        section_content="We ran paid social for North Bend Outfitters last spring.",
    )
    assert "North Bend Outfitters" in q


def test_question_carries_entities_in_headings_too():
    q = build_section_kb_question(
        section_title="Client Examples",
        section_content="## Acme Destination Board\nWe ran social.",
    )
    assert "Acme Destination Board" in q


def test_question_includes_requirements_and_focus():
    q = build_section_kb_question(
        section_title="Client Examples",
        user_message="fill KPIs from KB",
        requirements=["before/after metrics", "accounts managed"],
        section_content="## Acme Destination Board\nBody",
    )
    assert "before/after metrics" in q
    assert "fill KPIs" in q
    # Must not hardcode a tourism win list
    assert "San Francisco Travel" not in q
    assert "Seventh Mountain" not in q


def test_question_does_not_filter_generic_headings():
    """No stopword list: a section legitimately about strategy keeps its own words.

    The old stoplist dropped "Strategy", "Results", "Approach" — and, verbatim,
    "tourism portfolio approach", one client's phrase baked into shared code.
    """
    q = build_section_kb_question(
        section_title="Approach",
        section_content="## Strategy\nOur phased rollout.",
    )
    assert "phased rollout" in q


def test_question_is_bounded():
    """Long sections must not blow past the retriever's question budget."""
    q = build_section_kb_question(
        section_title="Client Examples",
        section_content="word " * 5000,
    )
    assert len(q) <= 1200


def test_user_asks_kb_fetch_or_fill_patterns():
    from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill

    assert user_asks_kb_fetch_or_fill(
        "here San Francisco Travel case study is empty fetch that"
    )
    assert user_asks_kb_fetch_or_fill("fill San Francisco Travel from KB")
    assert user_asks_kb_fetch_or_fill("fetch case study from knowledge base")
    assert user_asks_kb_fetch_or_fill("get the firm address from knowledge base")
    assert user_asks_kb_fetch_or_fill("search KB for contact info")
    assert not user_asks_kb_fetch_or_fill("Does 3.3 meet the RFP?")


def test_fetch_ask_triggers_packed_kb_evidence():
    out = inject_packed_evidence_into_instruction(
        "Improve this section.",
        "=== PACKED KB EVIDENCE ===\nKPI: bookings up",
    )
    assert "Improve this section." in out
    assert "PACKED KB EVIDENCE" in out
    assert "ACCURATE KB EVIDENCE RULES" in ACCURATE_KB_EDITOR_RULES
    assert "do not invent" in out.casefold() or "ACCURATE KB" in out
