from app.financial.qb_signals import derive_signals, js_round, usd


def _base() -> dict:
    return {"errors": {}, "sync_status": "ok"}


def test_js_round_matches_javascript_half_away_from_zero():
    # Python's round() would give 2 here; JS Math.round gives 3.
    assert js_round(2.5) == 3
    assert js_round(0.5) == 1
    assert js_round(1.4) == 1


def test_usd_formats_whole_dollars_with_separators():
    assert usd(14419.33) == "$14,419"
    assert usd(0) == "$0"


def test_clean_payload_produces_no_signals():
    assert derive_signals(_base()) == []


def test_aged_ar_surfaces_as_critical_and_names_oldest_debtor():
    signals = derive_signals({
        **_base(),
        "ar": {
            "total": 100_000,
            "invoice_count": 10,
            "overdue_total": 30_000,
            "buckets": [
                {"label": "Not yet due", "amount": 70_000},
                {"label": "90+ days", "amount": 30_000},
            ],
            "clients": [
                {"client": "Acme", "amount": 30_000, "invoices": 2, "oldest_days": 120}
            ],
        },
    })
    assert signals[0]["id"] == "ar-late"
    assert signals[0]["severity"] == "critical"
    assert signals[0]["figure"] == "$30,000"
    assert "Acme" in signals[0]["detail"]
    assert "120 days" in signals[0]["detail"]


def test_payables_over_cash_is_critical_and_sorts_first():
    signals = derive_signals({
        **_base(),
        "liquidity": {"as_of": "", "cash": 10_000, "net_cash_change": None},
        "ap": {"total": 50_000, "bill_count": 5, "buckets": [], "vendors": []},
        "expenses_by_vendor": {
            "total": 200_000,
            "vendor_count": 20,
            "top3_concentration_pct": 60,
            "vendors": [],
        },
    })
    assert signals[0]["id"] == "ap-over-cash"
    assert signals[0]["severity"] == "critical"
    assert signals[-1]["severity"] == "info"


def test_untagged_cost_threshold_is_exclusive_below_25_pct():
    quiet = derive_signals({
        **_base(),
        "unattached_cost": {
            "purchase_count": 100,
            "purchase_total": 0,
            "unattached_count": 24,
            "unattached_pct": 24,
            "cost_of_service_unattached": 5_000,
            "accounts": [],
        },
    })
    assert quiet == []

    loud = derive_signals({
        **_base(),
        "unattached_cost": {
            "purchase_count": 100,
            "purchase_total": 0,
            "unattached_count": 25,
            "unattached_pct": 25,
            "cost_of_service_unattached": 5_000,
            "accounts": [],
        },
    })
    assert loud[0]["id"] == "cost-untagged"


def test_slow_payers_uses_dso_multiple_with_forty_day_floor():
    signals = derive_signals({
        **_base(),
        "dso": {
            "dso_days": 30,
            "sample_size": 20,
            "slowest_clients": [
                {"client": "Slow Co", "avg_days": 60, "amount": 9_000},
                {"client": "Fine Co", "avg_days": 40, "amount": 1_000},
            ],
        },
    })
    # threshold = max(30 * 1.75, 40) = 52.5 — only Slow Co clears it.
    assert signals[0]["id"] == "slow-payers"
    assert signals[0]["figure"] == "$9,000"
    assert "1 client" in signals[0]["headline"]


def test_failed_sync_reports_itself():
    signals = derive_signals({"errors": {}, "sync_status": "failed"})
    assert signals[0]["id"] == "sync"
    assert signals[0]["headline"] == "The last QuickBooks sync failed"


def test_panel_errors_are_reported_as_info():
    signals = derive_signals({"errors": {"ar": "boom", "dso": "boom"}, "sync_status": "ok"})
    assert signals[0]["id"] == "sync"
    assert signals[0]["severity"] == "info"
    assert "2 panels" in signals[0]["headline"]
