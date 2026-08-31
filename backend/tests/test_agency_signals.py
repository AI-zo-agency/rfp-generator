from app.financial.agency_signals import build_signals


def _overview() -> dict:
    return {"position": {"join_mapped": 2, "join_total": 4}}


def _kpis(**over):
    base = {
        "join_mapped": 2,
        "join_total": 4,
        "queue_count": 2,
        "unlinked_invoice_count": 1,
        "orphan_count": 0,
        "orphan_billed_sum": 0,
        "open_ar": 0,
    }
    base.update(over)
    return base


def test_build_signals_includes_carryover_and_aging_when_prior_exists():
    open_items = [
        {"id": "delivery:1", "kind": "delivery", "title": "Late: Alpha", "weeks_open": 4, "amount": 500},
        {"id": "mapping:2", "kind": "mapping", "title": "Map: Beta", "weeks_open": 1, "amount": 0},
    ]
    carryover = [open_items[0]]

    signals = build_signals(
        overview=_overview(),
        open_items=open_items,
        carryover=carryover,
        resolved=[],
        new_items=[open_items[1]],
        kpis=_kpis(),
        prior_kpis={"join_mapped": 1, "join_total": 4, "queue_count": 3, "unlinked_invoice_count": 2},
        brief_week_start="2026-08-25",
        brief_week_end="2026-08-29",
        has_prior_snapshot=True,
    )

    ids = {row["id"] for row in signals}
    assert "carryover:week" in ids
    assert "aging:queue" in ids
    assert "new:week" in ids
    assert "kpi:week" in ids
    assert "join:health" in ids
    assert any(row["id"] == "priority:delivery:1" for row in signals)


def test_bootstrap_skips_week_over_week_and_adds_baseline_cards():
    open_items = [
        {"id": "delivery:1", "kind": "delivery", "title": "Late: Alpha", "weeks_open": 1, "amount": 500},
        {"id": "invoice:9", "kind": "invoice", "title": "Reconcile invoice 9", "weeks_open": 1, "amount": 100},
    ]

    signals = build_signals(
        overview=_overview(),
        open_items=open_items,
        carryover=[],
        resolved=[],
        new_items=open_items,
        kpis=_kpis(queue_count=1, unlinked_invoice_count=40, orphan_count=23, orphan_billed_sum=793573.51, open_ar=105860.36),
        prior_kpis=None,
        brief_week_start="2026-08-25",
        brief_week_end="2026-08-29",
        has_prior_snapshot=False,
    )

    ids = {row["id"] for row in signals}
    assert "new:week" not in ids
    assert "carryover:week" not in ids
    assert "queue:baseline" in ids
    assert "invoices:unlinked" in ids
    assert "orphans:billed" in ids
    assert "ar:open" in ids


def test_join_health_omitted_when_fully_mapped():
    signals = build_signals(
        overview=_overview(),
        open_items=[],
        carryover=[],
        resolved=[],
        new_items=[],
        kpis=_kpis(join_mapped=4, join_total=4, queue_count=0, unlinked_invoice_count=0),
        prior_kpis=None,
        brief_week_start="2026-08-25",
        brief_week_end="2026-08-29",
        has_prior_snapshot=False,
    )

    assert not any(row["id"] == "join:health" for row in signals)
