from app.financial.qb_signals import (
    aged_ar,
    coverage_gap,
    derive_signals,
    derived_figures,
    js_round,
    slow_payer_threshold,
    usd,
)


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


def test_segment_gap_fires_below_90_pct_coverage():
    signals = derive_signals({
        **_base(),
        "revenue_by_class": {
            "coverage_pct": 80,
            "unclassified": 5_000,
        },
    })
    assert signals[0]["id"] == "segment-gap"
    assert signals[0]["figure"] == "$5,000"
    assert "80%" in signals[0]["detail"]


def test_segment_gap_stays_quiet_at_exactly_90_pct():
    signals = derive_signals({
        **_base(),
        "revenue_by_class": {
            "coverage_pct": 90,
            "unclassified": 5_000,
        },
    })
    assert not any(s["id"] == "segment-gap" for s in signals)


def test_segment_gap_severity_is_warn_below_70_pct():
    signals = derive_signals({
        **_base(),
        "revenue_by_class": {
            "coverage_pct": 65,
            "unclassified": 5_000,
        },
    })
    assert signals[0]["id"] == "segment-gap"
    assert signals[0]["severity"] == "warn"


def test_segment_gap_severity_is_info_at_70_pct_and_above():
    signals = derive_signals({
        **_base(),
        "revenue_by_class": {
            "coverage_pct": 70,
            "unclassified": 5_000,
        },
    })
    assert signals[0]["id"] == "segment-gap"
    assert signals[0]["severity"] == "info"


def test_segment_gap_falls_back_to_class_coverage_when_revenue_by_class_absent():
    signals = derive_signals({
        **_base(),
        "class_coverage": {
            "coverage_pct": 80,
            "unclassified": 3_000,
        },
    })
    assert signals[0]["id"] == "segment-gap"
    assert signals[0]["figure"] == "$3,000"


def test_segment_gap_does_not_fall_back_when_coverage_pct_is_zero():
    # Critical test: coverage_pct of 0 is a valid value (not None), so it must be
    # used, not fallen back. This would fail if the port had used `or` instead of
    # the TypeScript's `??` (which only checks for null/undefined).
    signals = derive_signals({
        **_base(),
        "revenue_by_class": {
            "coverage_pct": 0,
            "unclassified": 5_000,
        },
        "class_coverage": {
            "coverage_pct": 50,
            "unclassified": 5_000,
        },
    })
    # With coverage_pct = 0 (not None), it should fire and show 0%
    assert signals[0]["id"] == "segment-gap"
    assert "0%" in signals[0]["detail"]


def test_collection_rate_fires_below_85_pct():
    signals = derive_signals({
        **_base(),
        "billing_vs_cash": {
            "invoiced_total": 100_000,
            "collection_rate_pct": 80,
            "open_ar": 20_000,
        },
    })
    assert signals[0]["id"] == "collection-rate"
    assert signals[0]["figure"] == "80%"
    assert "$20,000" in signals[0]["detail"]


def test_collection_rate_stays_quiet_at_exactly_85_pct():
    signals = derive_signals({
        **_base(),
        "billing_vs_cash": {
            "invoiced_total": 100_000,
            "collection_rate_pct": 85,
            "open_ar": 15_000,
        },
    })
    assert not any(s["id"] == "collection-rate" for s in signals)


def test_collection_rate_severity_is_warn_below_70_pct():
    signals = derive_signals({
        **_base(),
        "billing_vs_cash": {
            "invoiced_total": 100_000,
            "collection_rate_pct": 65,
            "open_ar": 35_000,
        },
    })
    assert signals[0]["id"] == "collection-rate"
    assert signals[0]["severity"] == "warn"


def test_collection_rate_severity_is_info_at_70_pct_and_above():
    signals = derive_signals({
        **_base(),
        "billing_vs_cash": {
            "invoiced_total": 100_000,
            "collection_rate_pct": 70,
            "open_ar": 30_000,
        },
    })
    assert signals[0]["id"] == "collection-rate"
    assert signals[0]["severity"] == "info"


def test_collection_rate_severity_is_info_well_above_70_pct():
    signals = derive_signals({
        **_base(),
        "billing_vs_cash": {
            "invoiced_total": 100_000,
            "collection_rate_pct": 75,
            "open_ar": 25_000,
        },
    })
    assert signals[0]["id"] == "collection-rate"
    assert signals[0]["severity"] == "info"


def test_slow_payer_threshold_is_dso_multiple_with_forty_day_floor():
    assert slow_payer_threshold(30) == 52.5
    assert slow_payer_threshold(10) == 40  # floor kicks in below ~22.9


def test_slow_payer_threshold_is_none_when_dso_days_is_none():
    assert slow_payer_threshold(None) is None


def test_coverage_gap_resolves_revenue_by_class_first():
    assert coverage_gap({
        "revenue_by_class": {"coverage_pct": 80, "unclassified": 5_000},
        "class_coverage": {"coverage_pct": 50, "unclassified": 9_000},
    }) == (80.0, 5_000.0)


def test_coverage_gap_falls_back_to_class_coverage_when_absent():
    assert coverage_gap({
        "class_coverage": {"coverage_pct": 80, "unclassified": 3_000},
    }) == (80.0, 3_000.0)


def test_coverage_gap_does_not_fall_back_when_coverage_pct_is_zero():
    # coverage_pct of 0 is a valid value (not None/missing), so it must be used
    # as-is rather than treated as absent and falling back to class_coverage.
    assert coverage_gap({
        "revenue_by_class": {"coverage_pct": 0, "unclassified": 5_000},
        "class_coverage": {"coverage_pct": 50, "unclassified": 5_000},
    }) == (0.0, 5_000.0)


def test_coverage_gap_is_none_when_coverage_missing_or_at_or_above_ninety():
    assert coverage_gap({}) is None
    assert coverage_gap({
        "revenue_by_class": {"coverage_pct": 90, "unclassified": 5_000},
    }) is None
    assert coverage_gap({
        "revenue_by_class": {"coverage_pct": 80, "unclassified": 0},
    }) is None


def test_vendor_concentration_directly_asserted():
    # Payload where vendor-concentration is the only signal
    signals = derive_signals({
        **_base(),
        "expenses_by_vendor": {
            "total": 200_000,
            "vendor_count": 20,
            "top3_concentration_pct": 60,
            "vendors": [],
        },
    })
    assert len(signals) == 1
    assert signals[0]["id"] == "vendor-concentration"
    assert signals[0]["severity"] == "info"
    assert signals[0]["figure"] == "$200,000"
    assert "20" in signals[0]["detail"]
    assert "60%" in signals[0]["headline"]
    assert signals[0]["go_to"] == "costs"


# ── derived figures ──────────────────────────────────────────────────────────
# The model is forbidden from deriving quantities, so anything worth stating has
# to be computed here. Figures below are the live 2026-08-24 position.

def test_ap_to_cash_ratio_is_the_real_multiple_not_a_rounded_one():
    data = _base() | {
        "ap": {"total": 38_643.22},
        "liquidity": {"cash": 7_742.33},
    }
    # 4.99x. The first live brief called this "nearly four times".
    assert derived_figures(data)["ap_to_cash_ratio"] == "5.0x"


def test_ap_to_cash_ratio_is_omitted_when_there_is_no_cash_to_divide_by():
    for cash in (0, None, -100):
        data = _base() | {"ap": {"total": 38_643.22}, "liquidity": {"cash": cash}}
        assert "ap_to_cash_ratio" not in derived_figures(data)


def test_ap_to_cash_ratio_is_omitted_when_there_are_no_payables():
    data = _base() | {"ap": {"total": 0}, "liquidity": {"cash": 7_742.33}}
    assert "ap_to_cash_ratio" not in derived_figures(data)


def _aged_ar_payload() -> dict:
    return _base() | {
        "ar": {
            "total": 51_244.06,
            "buckets": [
                {"label": "Not yet due", "amount": 22_745.83},
                {"label": "1-30 days", "amount": 13_331.83},
                {"label": "31-60 days", "amount": 13_966.40},
                {"label": "61-90 days", "amount": 1_200.00},
                {"label": "90+ days", "amount": 0.0},
            ],
            "clients": [{"client": "OCF", "amount": 11_966, "oldest_days": 72}],
        },
    }


def test_aged_share_pct_matches_the_percentage_the_signal_displays():
    data = _aged_ar_payload()
    pct = derived_figures(data)["aged_share_pct"]
    signal = next(s for s in derive_signals(data) if s["id"] == "ar-late")
    assert pct == "2%"
    assert signal["detail"].startswith("2% of what's owed.")


def test_aged_ar_returns_both_the_amount_and_the_share():
    late, share = aged_ar(_aged_ar_payload())
    assert late == 1_200.00
    assert round(share, 4) == 0.0234


def test_aged_ar_is_none_when_nothing_is_past_sixty_days():
    data = _base() | {
        "ar": {"total": 51_244.06, "buckets": [{"label": "1-30 days", "amount": 51_244.06}]}
    }
    assert aged_ar(data) is None
    assert "aged_share_pct" not in derived_figures(data)


def test_aged_ar_is_none_when_there_are_no_receivables_at_all():
    assert aged_ar(_base() | {"ar": {"total": 0, "buckets": []}}) is None
