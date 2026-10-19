"""Required cost-binder sentences are copied by meaning and kept only when verbatim.

The authority pass used to see a shortened excerpt that dropped the mandatory
fee-binder sentences. Those sentences are recovered by an LLM quote pass over
fixed character slices of the full RFP, then kept only when the quote is
actually in the source. No pattern table decides what counts as a cost submittal.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app.services.proposal_submission_authority import (
    _AUTHORITY_SYSTEM,
    character_slices,
    collect_cost_submittal_quotes,
    format_cost_submittal_quotes,
    grounded_verbatim_quotes,
)

_BINDER = (
    "2. Cost Proposal or Fee Schedule - The cost proposal must be submitted "
    "in a separate binder and clearly labeled Cost Proposal or Fee Schedule. "
    "Only one original version is needed."
)
_ACCOMPANIED = (
    "The offeror's proposal must be accompanied by the completed Cost Proposal. "
    "Cost Proposal MUST be submitted separate from proposal."
)


def _long_qualifications_rfp() -> str:
    head = (
        "REQUEST FOR QUALIFICATION BASED PROPOSALS\n"
        "TABLE OF CONTENTS\n"
        "6. Cost Proposal ................................................ 23\n"
        "EVALUATION POINT SUMMARY\n"
        "TOTAL POSSIBLE POINTS 110 points\n"
    )
    filler = ("General terms and evaluation criteria. " * 4000)[:70_000]
    tail = "APPENDIX H disclosure form. " * 400
    return head + filler + _BINDER + "\n" + _ACCOMPANIED + "\n" + tail


def test_character_slices_still_cover_a_sentence_past_the_excerpt_cutoff():
    text = _long_qualifications_rfp()
    assert _ACCOMPANIED not in text[:50_000]
    slices = character_slices(text, size=24_000, overlap=800)
    assert any(_ACCOMPANIED in sl for sl in slices)
    assert any("separate binder" in sl.lower() for sl in slices)


def test_grounding_keeps_verbatim_sentences_and_drops_inventions():
    text = _long_qualifications_rfp()
    kept = grounded_verbatim_quotes(
        [
            _ACCOMPANIED,
            _BINDER,
            "The Village will pay a fabricated retainer of $9,999,999.",
            "6. Cost Proposal",
        ],
        text,
    )
    joined = "\n".join(kept)
    assert _ACCOMPANIED in joined
    assert "separate binder" in joined.lower()
    assert "9,999,999" not in joined
    assert "6. Cost Proposal" not in joined
    block = format_cost_submittal_quotes(kept)
    assert _ACCOMPANIED in block


def test_pricing_asks_see_verbatim_cost_quotes_ahead_of_the_excerpt():
    from app.services.pricing_plan_service import pricing_asks_user_message

    message = pricing_asks_user_message("excerpt without the binder", [_ACCOMPANIED, _BINDER])
    assert message.index(_ACCOMPANIED) < message.index("excerpt without the binder")
    assert "separate binder" in message.lower()


def test_authority_prompt_confirms_a_required_binder_with_no_form_and_no_points():
    low = _AUTHORITY_SYSTEM.casefold()
    assert "separate binder" in low
    assert "zero evaluation points" in low
    assert "does not make it absent or ambiguous" in low
    assert "buyer's own program budget" in low


def test_collect_quotes_grounds_what_the_model_copies():
    text = _long_qualifications_rfp()

    async def fake_chat(messages, **kwargs):
        content = messages[-1]["content"]
        quotes: list[str] = []
        if _ACCOMPANIED in content:
            quotes.append(_ACCOMPANIED)
        if "separate binder" in content.lower():
            quotes.append(_BINDER)
        quotes.append("Offeror shall submit a made-up fee of $888,888.")
        return {"quotes": quotes}, "test"

    with patch(
        "app.services.proposal_intelligence.agent_base.safe_chat_json",
        AsyncMock(side_effect=fake_chat),
    ):
        kept = asyncio.run(collect_cost_submittal_quotes(text))
    joined = "\n".join(kept)
    assert _ACCOMPANIED in joined
    assert "separate binder" in joined.lower()
    assert "888,888" not in joined
