"""Tests for iWorker AI insight persistence."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.financial import iworker_insights as mod


def test_scope_key_is_period_scoped():
    assert mod.scope_key("week", "2026-08-25") == "week:2026-08-25"


def test_build_payload_round_trips_generate_shape():
    payload = mod.build_payload(
        {
            "generated_at": "Aug 31, 2026 at 05:00 PM",
            "summary": {
                "leadership_brief_text": "Brief",
                "top_3_risks": ["r1"],
                "top_3_wins": ["w1"],
                "margin_recommendations": ["m1"],
            },
            "stats": {"period_label": "Aug 25–31, 2026"},
        }
    )
    assert payload["brief"] == "Brief"
    assert payload["top_3_risks"] == ["r1"]
    assert payload["stats"]["period_label"] == "Aug 25–31, 2026"


def test_persist_insight_calls_upsert():
    mock_upsert = MagicMock()
    result = {
        "generated_at": "now",
        "summary": {
            "leadership_brief_text": "Brief",
            "top_3_risks": [],
            "top_3_wins": [],
            "margin_recommendations": [],
        },
        "stats": {"period_label": "Week"},
    }
    with patch.object(mod, "upsert_insight", mock_upsert):
        ok = mod.persist_insight(
            granularity="week",
            period_start="2026-08-25",
            period_end="2026-08-31",
            result=result,
            evidence={"signals": []},
            provider="test",
            model="test-model",
        )
    assert ok is True
    mock_upsert.assert_called_once()
    kwargs = mock_upsert.call_args.kwargs
    assert kwargs["source"] == "iworker"
    assert kwargs["scope_key"] == "week:2026-08-25"
    assert kwargs["as_of"] == "2026-08-31"
    assert kwargs["status"] == "ok"


def test_response_from_row_maps_empty_state():
    body = mod.response_from_row(None, period_label="Aug 25–31, 2026")
    assert body["status"] == "empty"
    assert body["summary"]["leadership_brief_text"] == ""
    assert body["stats"]["period_label"] == "Aug 25–31, 2026"
