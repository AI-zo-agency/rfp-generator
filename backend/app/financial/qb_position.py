"""The cash position strip: what you hold, what is late out, what is late in.

Computed in Python and rendered directly, which is the whole point. Once the
reader can see these figures the brief has no reason to state one, and a brief
that names no figure cannot misstate one. That makes this the cheapest figure-
integrity measure available as well as the most useful panel on the tab.

It also replaces the sentence the first live brief got wrong. "Bills due total
nearly four times what we have on hand" is arithmetically close and
strategically misleading: cash plus overdue receivables minus overdue payables
is positive, and the business collects in under 22 days.
"""

from __future__ import annotations

from typing import Any

from app.financial.qb_signals import usd

_NOT_YET_DUE = "Not yet due"


def _overdue_from_buckets(panel: dict[str, Any]) -> float:
    return sum(
        float(bucket.get("amount") or 0)
        for bucket in (panel.get("buckets") or [])
        if bucket.get("label") != _NOT_YET_DUE
    )


def position(data: dict[str, Any]) -> dict[str, Any] | None:
    """Cash, overdue payables, overdue receivables, and the net of the three.

    Returns None when there is no cash figure to anchor it, so the strip
    disappears rather than rendering a hole.

    `net` is deliberately not a forecast. Overdue receivables are money already
    earned and already past its due date — a defensible near-term claim in a way
    that a projection over future collections is not.
    """
    liquidity = data.get("liquidity") or {}
    raw_cash = liquidity.get("cash")
    if raw_cash is None:
        return None
    cash = float(raw_cash)

    ap = data.get("ap") or {}
    ar = data.get("ar") or {}

    overdue_ap = _overdue_from_buckets(ap)
    # ar_aging computes this already; ap_aging does not. `is None` rather than
    # `or`, so a real zero is used as-is instead of falling through to a bucket
    # sum — the same trap coverage_gap documents.
    raw_overdue_ar = ar.get("overdue_total")
    overdue_ar = (
        _overdue_from_buckets(ar) if raw_overdue_ar is None else float(raw_overdue_ar)
    )

    net = cash + overdue_ar - overdue_ap
    return {
        "cash_figure": usd(cash),
        "overdue_ap_figure": usd(overdue_ap),
        "overdue_ar_figure": usd(overdue_ar),
        "net_figure": usd(net),
        "net_amount": net,
    }
