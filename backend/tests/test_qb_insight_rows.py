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
    assert rows[0]["overdue_figure"] == "$8,000"


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


# ── ranking on the overdue portion ───────────────────────────────────────────
# Oregon-Canadian Forest Products is the case that motivated this: $11,966 owed
# but only $1,200 of it actually past due, on one invoice 72 days old. Pairing
# the whole balance with the oldest invoice's age read as "$11,966 is 72 days
# late" and put OCF at the top of a list it did not belong at the top of.

def _split_ar(clients):
    return {
        "total": sum(c["amount"] for c in clients), "invoice_count": 0,
        "overdue_total": sum(c["overdue_amount"] for c in clients),
        "buckets": [], "clients": clients,
    }


def test_chase_ranks_on_the_overdue_portion_not_the_whole_balance():
    data = {"ar": _split_ar([
        {"client": "OCF", "amount": 11_966, "invoices": 8, "oldest_days": 72,
         "overdue_amount": 1_200, "overdue_days": 72},
        {"client": "Steady Co", "amount": 9_000, "invoices": 2, "oldest_days": 40,
         "overdue_amount": 9_000, "overdue_days": 40},
    ])}
    rows = chase_rows(data)
    # Overdue dollar-days: Steady 9,000 x 40 = 360,000 beats OCF 1,200 x 72 = 86,400.
    # On the whole balance OCF would have won with 11,966 x 72 = 861,552.
    assert [r["client"] for r in rows] == ["Steady Co", "OCF"]


def test_chase_reports_the_overdue_amount_and_the_balance_separately():
    rows = chase_rows({"ar": _split_ar([
        {"client": "OCF", "amount": 11_966, "invoices": 8, "oldest_days": 72,
         "overdue_amount": 1_200, "overdue_days": 72},
    ])})
    assert rows[0]["overdue_figure"] == "$1,200"
    assert rows[0]["overdue_days"] == 72
    assert rows[0]["balance_figure"] == "$11,966"
    assert rows[0]["invoice_count"] == 8


def test_chase_is_unchanged_when_the_whole_balance_is_overdue():
    rows = chase_rows({"ar": _split_ar([
        {"client": "All Late", "amount": 5_000, "invoices": 2, "oldest_days": 61,
         "overdue_amount": 5_000, "overdue_days": 61},
    ])})
    assert rows[0]["overdue_figure"] == rows[0]["balance_figure"] == "$5,000"
    assert rows[0]["dollar_days"] == 5_000 * 61


def test_chase_excludes_a_client_whose_balance_is_entirely_not_yet_due():
    rows = chase_rows({"ar": _split_ar([
        {"client": "Current", "amount": 20_000, "invoices": 3, "oldest_days": 0,
         "overdue_amount": 0, "overdue_days": 0},
    ])})
    assert rows == []


def test_chase_falls_back_to_the_old_fields_for_a_stale_panel_cache():
    """The cache serves last night's payload until the next sync writes one."""
    stale = {"ar": _ar([
        {"client": "Big Recent", "amount": 20_000, "invoices": 1, "oldest_days": 5},
        {"client": "Small Ancient", "amount": 3_000, "invoices": 2, "oldest_days": 200},
    ])}
    rows = chase_rows(stale)
    assert [r["client"] for r in rows] == ["Small Ancient", "Big Recent"]
    assert rows[0]["overdue_figure"] == "$3,000"
    assert rows[0]["balance_figure"] == "$3,000"
    assert rows[0]["overdue_days"] == 200


def test_chase_row_ids_survive_the_new_row_shape():
    """Stored notes are keyed by row id, so a shape change must not orphan them."""
    rows = chase_rows({"ar": _split_ar([
        {"client": "City of Umatilla", "amount": 14_419, "invoices": 3,
         "oldest_days": 61, "overdue_amount": 14_419, "overdue_days": 61},
    ])})
    assert rows[0]["id"] == "chase:cityofumatilla"


# ── average age, not the oldest invoice's age ────────────────────────────────
# Splitting off the overdue portion is only half the fix. OCF's eight invoices
# are all overdue but run 24 to 73 days, so pairing $11,966 with 73 makes the
# whole balance look ancient. The exact dollar-days sum gives the honest age.

def test_chase_uses_the_exact_dollar_days_sum_when_the_panel_carries_it():
    rows = chase_rows({"ar": _split_ar([
        {"client": "OCF", "amount": 11_966, "invoices": 8, "oldest_days": 73,
         "overdue_amount": 11_966, "overdue_days": 73,
         "overdue_dollar_days": 558_503},
    ])})
    assert rows[0]["dollar_days"] == 558_503
    # The rectangle would have said 11,966 x 73 = 873,518, overstating by 56%.
    assert rows[0]["avg_overdue_days"] == 47
    assert rows[0]["overdue_days"] == 73


def test_avg_equals_oldest_when_every_overdue_invoice_shares_an_age():
    rows = chase_rows({"ar": _split_ar([
        {"client": "Single", "amount": 2_750, "invoices": 1, "oldest_days": 55,
         "overdue_amount": 2_750, "overdue_days": 55,
         "overdue_dollar_days": 2_750 * 55},
    ])})
    assert rows[0]["avg_overdue_days"] == rows[0]["overdue_days"] == 55


def test_exact_dollar_days_can_reorder_the_list_against_the_rectangle():
    """A spread-out debtor should rank below a uniformly-late one it would
    otherwise beat on the oldest-invoice approximation."""
    rows = chase_rows({"ar": _split_ar([
        # Rectangle: 10,000 x 90 = 900,000. Truth: mostly recent.
        {"client": "Spread", "amount": 10_000, "invoices": 5, "oldest_days": 90,
         "overdue_amount": 10_000, "overdue_days": 90,
         "overdue_dollar_days": 200_000},
        {"client": "Uniform", "amount": 6_000, "invoices": 2, "oldest_days": 60,
         "overdue_amount": 6_000, "overdue_days": 60,
         "overdue_dollar_days": 360_000},
    ])})
    assert [r["client"] for r in rows] == ["Uniform", "Spread"]
    assert rows[0]["avg_overdue_days"] == 60
    assert rows[1]["avg_overdue_days"] == 20


def test_a_stale_cache_without_dollar_days_falls_back_to_the_rectangle():
    rows = chase_rows({"ar": _ar([
        {"client": "Old Shape", "amount": 3_000, "invoices": 2, "oldest_days": 200},
    ])})
    assert rows[0]["dollar_days"] == 3_000 * 200
    assert rows[0]["avg_overdue_days"] == 200
