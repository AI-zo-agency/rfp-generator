"""Unit tests for bid-scope API request normalization and endpoint behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1.rfps import BidScopeRequest, put_bid_scope
from app.models.rfp import RfpRecord


def _sample_rfp(**overrides) -> RfpRecord:
    base = dict(
        id="rfp-1",
        title="Mason County Tourism",
        client="Mason County",
        dueDate="2026-09-30",
        receivedDate="2026-09-01",
        lastActivity="2026-09-01T00:00:00Z",
        lastActivityNote="",
    )
    base.update(overrides)
    return RfpRecord(**base)


def test_bid_scope_request_requires_selected_tracks_alias():
    body = BidScopeRequest.model_validate(
        {"selectedTracks": ["III.B VIS"], "invalidateArtifacts": False}
    )
    assert body.selected_tracks == ["III.B VIS"]
    assert body.invalidate_artifacts is False


def test_bid_scope_request_defaults_invalidate_true():
    body = BidScopeRequest.model_validate({"selectedTracks": ["A"]})
    assert body.invalidate_artifacts is True


@pytest.mark.asyncio
async def test_put_bid_scope_rejects_empty_after_normalize():
    with patch("app.api.v1.rfps.get_rfp", return_value=_sample_rfp()):
        with pytest.raises(HTTPException) as exc:
            await put_bid_scope(
                "rfp-1",
                BidScopeRequest(selectedTracks=["  ", ""]),
            )
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_put_bid_scope_persists_deduped_tracks_and_clears_artifacts():
    locked = _sample_rfp(
        selectedTracks=["III.B VIS"],
        bidScopeLockedAt="2026-09-23T10:00:00Z",
    )
    with (
        patch("app.api.v1.rfps.get_rfp", side_effect=[_sample_rfp(), locked]),
        patch(
            "app.api.v1.rfps.save_rfp_bid_scope",
            return_value=locked,
        ) as save_mock,
        patch(
            "app.api.v1.rfps._invalidate_proposal_artifacts",
            new_callable=AsyncMock,
        ) as clear_mock,
    ):
        result = await put_bid_scope(
            "rfp-1",
            BidScopeRequest(
                selectedTracks=["III.B VIS", "III.B VIS", "  III.B VIS  "],
                invalidateArtifacts=True,
            ),
        )

    save_mock.assert_called_once()
    args, kwargs = save_mock.call_args
    assert args[0] == "rfp-1"
    assert args[1] == ["III.B VIS"]
    assert kwargs["locked_at"]
    clear_mock.assert_awaited_once_with("rfp-1")
    assert result.selected_tracks == ["III.B VIS"]


@pytest.mark.asyncio
async def test_put_bid_scope_skips_clear_when_invalidate_false():
    locked = _sample_rfp(
        selectedTracks=["A", "B"],
        bidScopeLockedAt="2026-09-23T10:00:00Z",
    )
    with (
        patch("app.api.v1.rfps.get_rfp", return_value=_sample_rfp()),
        patch("app.api.v1.rfps.save_rfp_bid_scope", return_value=locked),
        patch(
            "app.api.v1.rfps._invalidate_proposal_artifacts",
            new_callable=AsyncMock,
        ) as clear_mock,
    ):
        await put_bid_scope(
            "rfp-1",
            BidScopeRequest(selectedTracks=["A", "B"], invalidateArtifacts=False),
        )
    clear_mock.assert_not_awaited()
