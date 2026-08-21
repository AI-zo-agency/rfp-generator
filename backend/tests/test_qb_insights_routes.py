from unittest.mock import patch

from app.financial import router as fin_router


def _overview():
    return {
        "year": 2026,
        "errors": {},
        "sync_status": "ok",
        "as_of": "2026-08-21",
        "ar": {
            "total": 14_419, "invoice_count": 3, "overdue_total": 14_419,
            "buckets": [{"label": "90+ days", "amount": 14_419}],
            "clients": [{"client": "City of Umatilla", "amount": 14_419,
                         "invoices": 3, "oldest_days": 95}],
        },
    }


def test_empty_state_when_no_insight_row_exists():
    with patch.object(fin_router, "_load_overview", return_value=_overview()), \
         patch.object(fin_router, "get_latest_insight", return_value=None):
        result = fin_router.quickbooks_ai_insights(year=2026)

    assert result["status"] == "empty"
    assert result["brief"] == ""
    assert result["notes"] == {}
    # Rows still render without a brief — they are computed, not generated.
    assert result["chase"][0]["client"] == "City of Umatilla"


def test_serves_the_stored_brief_and_joins_notes_to_recomputed_rows():
    row = {
        "as_of": "2026-08-21",
        "generated_at": "2026-08-21T03:00:00Z",
        "provider": "openrouter",
        "payload": {"brief": "Umatilla is the problem.",
                    "notes": {"chase:cityofumatilla": "95 days out."}},
    }
    with patch.object(fin_router, "_load_overview", return_value=_overview()), \
         patch.object(fin_router, "get_latest_insight", return_value=row):
        result = fin_router.quickbooks_ai_insights(year=2026)

    assert result["status"] == "ok"
    assert result["brief"] == "Umatilla is the problem."
    assert result["notes"]["chase:cityofumatilla"] == "95 days out."
    assert result["as_of"] == "2026-08-21"


def test_marks_an_older_brief_as_stale():
    row = {
        "as_of": "2026-08-14",
        "generated_at": "2026-08-14T03:00:00Z",
        "provider": "openrouter",
        "payload": {"brief": "last week", "notes": {}},
    }
    with patch.object(fin_router, "_load_overview", return_value=_overview()), \
         patch.object(fin_router, "get_latest_insight", return_value=row), \
         patch.object(fin_router, "_today_iso", return_value="2026-08-21"):
        result = fin_router.quickbooks_ai_insights(year=2026)

    assert result["stale"] is True


def test_regenerate_writes_today_and_returns_the_fresh_brief():
    row = {
        "as_of": "2026-08-21",
        "generated_at": "2026-08-21T09:00:00Z",
        "provider": "openrouter",
        "payload": {"brief": "fresh", "notes": {}},
    }
    with patch.object(fin_router, "_load_overview", return_value=_overview()), \
         patch.object(fin_router, "generate_and_store", return_value="ok") as gen, \
         patch.object(fin_router, "get_latest_insight", return_value=row):
        result = fin_router.quickbooks_ai_insights_regenerate(year=2026)

    assert gen.called
    assert result["brief"] == "fresh"
