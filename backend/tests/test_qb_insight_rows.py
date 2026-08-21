from app.financial.qb_insight_rows import chase_rows, hygiene_rows, row_ids


def _ar(clients):
    return {"total": sum(c["amount"] for c in clients), "invoice_count": 0,
            "overdue_total": 0, "buckets": [], "clients": clients}


def test_chase_ranks_by_dollar_days_not_raw_amount():
    data = {"ar": _ar([
        {"client": "Big Recent", "amount": 20_000, "invoices": 1, "oldest_days": 5},
        {"client": "Small Ancient", "amount": 3_000, "invoices": 2, "oldest_days": 200},
    ])}
    rows = chase_rows(data)
    # 3,000 x 200 = 600,000 beats 20,000 x 5 = 100,000.
    assert [r["client"] for r in rows] == ["Small Ancient", "Big Recent"]


def test_chase_row_ids_are_stable_when_the_amount_changes():
    first = chase_rows({"ar": _ar([
        {"client": "City of Umatilla", "amount": 14_419, "invoices": 3, "oldest_days": 61},
    ])})
    second = chase_rows({"ar": _ar([
        {"client": "City of Umatilla", "amount": 9_000, "invoices": 2, "oldest_days": 74},
    ])})
    assert first[0]["id"] == second[0]["id"] == "chase:cityofumatilla"


def test_chase_skips_clients_with_nothing_overdue():
    rows = chase_rows({"ar": _ar([
        {"client": "Paid Up", "amount": 5_000, "invoices": 1, "oldest_days": 0},
    ])})
    assert rows == []


def test_chase_flags_slow_payers_at_the_same_threshold_as_the_signal():
    data = {
        "ar": _ar([
            {"client": "Slow Co", "amount": 9_000, "invoices": 1, "oldest_days": 70},
            {"client": "Fine Co", "amount": 9_000, "invoices": 1, "oldest_days": 70},
        ]),
        "dso": {
            "dso_days": 30,
            "sample_size": 10,
            "slowest_clients": [
                {"client": "Slow Co", "avg_days": 60, "amount": 9_000},
                {"client": "Fine Co", "avg_days": 40, "amount": 9_000},
            ],
        },
    }
    by_client = {r["client"]: r for r in chase_rows(data)}
    # threshold = max(30 * 1.75, 40) = 52.5
    assert by_client["Slow Co"]["slow_payer"] is True
    assert by_client["Fine Co"]["slow_payer"] is False


def test_chase_honours_the_limit_and_formats_the_figure():
    clients = [
        {"client": f"C{i}", "amount": 1_000 * i, "invoices": 1, "oldest_days": 100}
        for i in range(1, 9)
    ]
    rows = chase_rows({"ar": _ar(clients)}, limit=3)
    assert len(rows) == 3
    assert rows[0]["figure"] == "$8,000"


def test_chase_on_empty_payload_is_empty():
    assert chase_rows({}) == []


def test_hygiene_lists_cost_of_service_accounts_only():
    rows = hygiene_rows({"unattached_cost": {
        "purchase_count": 0, "purchase_total": 0, "unattached_count": 0,
        "unattached_pct": 0, "cost_of_service_unattached": 0,
        "accounts": [
            {"account": "Contract Labor", "amount": 12_000, "is_cost_of_service": True},
            {"account": "Office Supplies", "amount": 40_000, "is_cost_of_service": False},
        ],
    }})
    assert [r["label"] for r in rows] == ["Contract Labor"]
    assert rows[0]["id"] == "hygiene:contractlabor"
    assert rows[0]["kind"] == "untagged_cost"


def test_hygiene_adds_unclassified_income_below_ninety_percent_coverage():
    rows = hygiene_rows({"revenue_by_class": {
        "matrix": [], "parents": [], "segments": [],
        "unclassified": 55_000, "total": 500_000, "coverage_pct": 89,
    }})
    assert rows[0]["id"] == "hygiene:unclassified-income"
    assert rows[0]["figure"] == "$55,000"
    assert rows[0]["kind"] == "unclassified_income"


def test_hygiene_stays_quiet_at_ninety_percent_coverage():
    rows = hygiene_rows({"revenue_by_class": {
        "matrix": [], "parents": [], "segments": [],
        "unclassified": 55_000, "total": 500_000, "coverage_pct": 90,
    }})
    assert rows == []


def test_hygiene_orders_by_amount():
    rows = hygiene_rows({
        "unattached_cost": {
            "purchase_count": 0, "purchase_total": 0, "unattached_count": 0,
            "unattached_pct": 0, "cost_of_service_unattached": 0,
            "accounts": [
                {"account": "Small", "amount": 1_000, "is_cost_of_service": True},
                {"account": "Large", "amount": 90_000, "is_cost_of_service": True},
            ],
        },
        "revenue_by_class": {
            "matrix": [], "parents": [], "segments": [],
            "unclassified": 50_000, "total": 500_000, "coverage_pct": 80,
        },
    })
    assert [r["label"] for r in rows] == ["Large", "Unclassified income", "Small"]


def test_row_ids_collects_both_lists():
    assert row_ids([{"id": "a"}, {"id": "b"}]) == {"a", "b"}
