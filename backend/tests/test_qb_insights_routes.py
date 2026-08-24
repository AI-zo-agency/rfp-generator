from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.financial import router as fin_router
from app.main import app

client = TestClient(app)


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
        result = fin_router.quickbooks_ai_insights()

    assert result["status"] == "empty"
    assert result["brief"] == ""
    assert result["notes"] == {}
    # Rows still render without a brief — they are computed, not generated.
    assert result["chase"][0]["client"] == "City of Umatilla"
    # A brief that does not exist is never stale.
    assert result["stale"] is False


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
        result = fin_router.quickbooks_ai_insights()

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
        result = fin_router.quickbooks_ai_insights()

    assert result["stale"] is True


def test_ai_insights_uses_current_year_regardless_of_the_overview_cache():
    """No year parameter exists anymore — the route always asks for the current
    year's overview, the same way the nightly sync always writes for
    started.year. A brief is never stored for a past year."""
    with patch.object(fin_router, "_load_overview", return_value=_overview()) as load, \
         patch.object(fin_router, "get_latest_insight", return_value=None):
        fin_router.quickbooks_ai_insights()

    load.assert_called_once_with(datetime.now().year)


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
        result = fin_router.quickbooks_ai_insights_regenerate()

    assert gen.called
    assert result["brief"] == "fresh"
    assert result["generated"] == "ok"


def test_regenerate_reports_failed_status_without_discarding_it():
    row = {
        "as_of": "2026-08-20",
        "generated_at": "2026-08-20T09:00:00Z",
        "provider": "openrouter",
        "payload": {"brief": "yesterday", "notes": {}},
    }
    with patch.object(fin_router, "_load_overview", return_value=_overview()), \
         patch.object(fin_router, "generate_and_store", return_value="failed"), \
         patch.object(fin_router, "get_latest_insight", return_value=row):
        result = fin_router.quickbooks_ai_insights_regenerate()

    assert result["generated"] == "failed"
    # The previous brief is still returned — regenerate degrades, not blanks.
    assert result["brief"] == "yesterday"


def test_regenerate_uses_current_year_regardless_of_the_overview_cache():
    with patch.object(fin_router, "_load_overview", return_value=_overview()) as load, \
         patch.object(fin_router, "generate_and_store", return_value="ok"), \
         patch.object(fin_router, "get_latest_insight", return_value=None):
        fin_router.quickbooks_ai_insights_regenerate()

    load.assert_called_once_with(datetime.now().year)


def test_a_broken_insight_lookup_degrades_to_the_empty_state_instead_of_500():
    with patch.object(fin_router, "_load_overview", return_value=_overview()), \
         patch.object(
             fin_router, "get_latest_insight", side_effect=RuntimeError("relation missing")
         ):
        result = fin_router.quickbooks_ai_insights()

    assert result["status"] == "empty"
    assert result["brief"] == ""
    # The computed tables are pure functions over the overview and never
    # depend on the ai_insights table, so they still render.
    assert result["chase"][0]["client"] == "City of Umatilla"


def test_regenerate_tolerates_a_broken_insight_lookup_on_both_sides():
    with patch.object(fin_router, "_load_overview", return_value=_overview()), \
         patch.object(fin_router, "generate_and_store", return_value="ok"), \
         patch.object(
             fin_router, "get_latest_insight", side_effect=RuntimeError("relation missing")
         ):
        result = fin_router.quickbooks_ai_insights_regenerate()

    assert result["status"] == "empty"
    assert result["generated"] == "ok"


def test_regenerate_skips_the_model_call_when_the_panel_cache_is_missing():
    """Spec: panel cache missing for the year -> no insights generated. There is
    nothing worth spending a model call on over an all-None skeleton."""
    empty_overview = {**_overview(), "sync_status": "missing"}
    with patch.object(fin_router, "_load_overview", return_value=empty_overview), \
         patch.object(fin_router, "generate_and_store") as gen, \
         patch.object(fin_router, "get_latest_insight", return_value=None):
        result = fin_router.quickbooks_ai_insights_regenerate()

    assert not gen.called
    assert result["status"] == "empty"
    assert "generated" not in result


def test_regenerate_skips_the_model_call_during_backfill():
    empty_overview = {**_overview(), "sync_status": "backfill_pending"}
    with patch.object(fin_router, "_load_overview", return_value=empty_overview), \
         patch.object(fin_router, "generate_and_store") as gen, \
         patch.object(fin_router, "get_latest_insight", return_value=None):
        fin_router.quickbooks_ai_insights_regenerate()

    assert not gen.called


# ── HTTP wiring ───────────────────────────────────────────────────────────────
# The tests above call the route functions directly, which never exercises
# FastAPI's route registration, verb, or path (including the /api/v1/financials
# prefix). These go through TestClient instead, patching the same seams
# (_load_overview / get_latest_insight / generate_and_store) so nothing reaches
# Supabase or an LLM provider.
#
# Neither route takes a year query parameter (see the direct-call tests above
# for why), so there is exactly one calling convention to cover here.


def test_http_get_ai_insights_returns_expected_shape(monkeypatch):
    monkeypatch.setattr(fin_router, "_load_overview", lambda year: _overview())
    monkeypatch.setattr(
        fin_router, "get_latest_insight", lambda source, scope_key: None
    )
    response = client.get("/api/v1/financials/quickbooks/ai-insights")
    assert response.status_code == 200
    body = response.json()
    for key in ("brief", "notes", "chase", "hygiene", "as_of", "stale", "status"):
        assert key in body


def test_http_post_regenerate_calls_generate_and_store(monkeypatch):
    monkeypatch.setattr(fin_router, "_load_overview", lambda year: _overview())
    monkeypatch.setattr(
        fin_router, "get_latest_insight", lambda source, scope_key: None
    )
    generate_calls = []
    monkeypatch.setattr(
        fin_router,
        "generate_and_store",
        lambda realm_id, overview, as_of: generate_calls.append(as_of) or "ok",
    )
    response = client.post("/api/v1/financials/quickbooks/ai-insights/regenerate")
    assert response.status_code == 200
    assert len(generate_calls) == 1
    assert response.json()["generated"] == "ok"


def test_http_post_regenerate_skips_generate_and_store_when_cache_is_missing(
    monkeypatch,
):
    monkeypatch.setattr(
        fin_router,
        "_load_overview",
        lambda year: {**_overview(), "sync_status": "missing"},
    )
    monkeypatch.setattr(
        fin_router, "get_latest_insight", lambda source, scope_key: None
    )
    generate_calls = []
    monkeypatch.setattr(
        fin_router,
        "generate_and_store",
        lambda realm_id, overview, as_of: generate_calls.append(as_of) or "ok",
    )
    response = client.post("/api/v1/financials/quickbooks/ai-insights/regenerate")
    assert response.status_code == 200
    assert generate_calls == []


def test_http_get_ai_insights_defaults_year_when_omitted(monkeypatch):
    # No year parameter exists anymore, so this is now the only calling
    # convention — kept as its own test since it predates the other route
    # tests and still documents that omitting the query string works.
    monkeypatch.setattr(fin_router, "_load_overview", lambda year: _overview())
    monkeypatch.setattr(
        fin_router, "get_latest_insight", lambda source, scope_key: None
    )
    response = client.get("/api/v1/financials/quickbooks/ai-insights")
    assert response.status_code == 200


def _overview_with_cash():
    return _overview() | {
        "liquidity": {"cash": 7_742.33},
        "ap": {"total": 38_643.22, "buckets": [
            {"label": "Not yet due", "amount": 11_670.58},
            {"label": "1-30 days", "amount": 16_020.40},
            {"label": "31-60 days", "amount": 10_952.24},
        ]},
    }


def test_the_position_strip_is_recomputed_on_read_like_every_other_row():
    with patch.object(fin_router, "_load_overview", return_value=_overview_with_cash()), \
         patch.object(fin_router, "get_latest_insight", return_value=None):
        result = fin_router.quickbooks_ai_insights()

    # Empty state — no brief yet — but the figures still render.
    assert result["status"] == "empty"
    assert result["position"]["cash_figure"] == "$7,742"
    assert result["position"]["overdue_ap_figure"] == "$26,973"
    assert result["position"]["overdue_ar_figure"] == "$14,419"


def test_the_position_strip_is_null_when_there_is_no_cash_figure():
    with patch.object(fin_router, "_load_overview", return_value=_overview()), \
         patch.object(fin_router, "get_latest_insight", return_value=None):
        result = fin_router.quickbooks_ai_insights()
    assert result["position"] is None
