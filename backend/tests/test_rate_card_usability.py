"""Rate-card usability guard."""

from types import SimpleNamespace

import pytest

from app.services.proposal_common import ProposalError
from app.services.proposal_pricing_service import assert_rate_card_usable


def test_missing_guide_skips_usability_guard() -> None:
    assert_rate_card_usable(
        rate_card=SimpleNamespace(rates=[]),
        guide_text="(No 00_Guide_Pricing found)",
        guide_missing=True,
    )


def test_junk_single_rate_card_raises() -> None:
    with pytest.raises(ProposalError, match="rate card unusable"):
        assert_rate_card_usable(
            rate_card=SimpleNamespace(rates=[SimpleNamespace(rate_id="junk")]),
            guide_text="00_Guide_Pricing " + ("x" * 2000),
            guide_missing=False,
        )


def test_usable_rate_card_passes() -> None:
    assert_rate_card_usable(
        rate_card=SimpleNamespace(
            rates=[SimpleNamespace(rate_id="a"), SimpleNamespace(rate_id="b")]
        ),
        guide_text="00_Guide_Pricing " + ("x" * 2000),
        guide_missing=False,
    )
