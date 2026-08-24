"""The two ranked lists the QuickBooks insight panel renders.

Pure functions over the overview payload. Every figure the reader sees is
produced here, so the language model never has to compute or repeat one — it
only annotates these rows by id.
"""

from __future__ import annotations

import re
from typing import Any

from app.financial.qb_signals import (
    coverage_gap,
    js_round,
    slow_payer_threshold,
    usd,
)

_CHASE_LIMIT = 5
_HYGIENE_LIMIT = 5


def _slug(name: str) -> str:
    """Stable id fragment. "City of Umatilla" -> "cityofumatilla"."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def row_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {row["id"] for row in rows}


def chase_rows(data: dict[str, Any], limit: int = _CHASE_LIMIT) -> list[dict[str, Any]]:
    """Open receivables worth a phone call, ranked by overdue dollar-days.

    Dollar-days is a standard receivables measure and needs no invented
    weighting, which keeps the ordering explainable when someone asks why a row
    sits where it does. Two things make it honest here.

    Ranking the *overdue* portion rather than the whole balance is what "worth a
    phone call" means — a client holding a large not-yet-due invoice owes
    nothing yet. City of Umatilla shows $14,419 outstanding and $3,419 overdue.

    Pairing that amount with an *average* age rather than the oldest invoice's
    age is what stops the row lying. OCF's eight overdue invoices run from 24 to
    73 days; "$11,966, 73 days late" is false, "$11,966, 47 days late on
    average" is not. The average falls out of the exact dollar-days sum, so both
    come from the same number.

    ponytail: `ar.clients` is already truncated to the twelve largest balances,
    so a small-balance client whose whole book is overdue can fall outside the
    list before it gets here. Widen the panel's cut if a known debtor ever goes
    missing from the chase list.
    """
    ar = data.get("ar") or {}
    dso = data.get("dso") or {}

    dso_days = dso.get("dso_days")
    slow: set[str] = set()
    threshold = slow_payer_threshold(dso_days)
    if threshold is not None:
        slow = {
            _slug(c.get("client", ""))
            for c in (dso.get("slowest_clients") or [])
            if float(c.get("avg_days") or 0) >= threshold
        }

    rows: list[dict[str, Any]] = []
    for client in ar.get("clients") or []:
        balance = float(client.get("amount") or 0)
        # A panel cache written before the overdue split existed still has to
        # render between deploy and the next sync, so fall back to the whole
        # balance and the oldest invoice's age — today's behaviour exactly.
        raw_overdue = client.get("overdue_amount")
        overdue = balance if raw_overdue is None else float(raw_overdue)
        raw_days = client.get("overdue_days")
        days = (
            int(client.get("oldest_days") or 0)
            if raw_days is None
            else int(raw_days)
        )
        if overdue <= 0 or days <= 0:
            continue
        # The exact per-invoice sum when the panel carries it, the old
        # rectangle when it does not. The rectangle always overstates: OCF's
        # 11,966 x 73 = 873,518 against a true 558,503.
        raw_dd = client.get("overdue_dollar_days")
        dollar_days = overdue * days if raw_dd is None else float(raw_dd)
        slug = _slug(client.get("client", ""))
        rows.append({
            "id": f"chase:{slug}",
            "client": client.get("client", ""),
            "overdue_amount": overdue,
            "overdue_figure": usd(overdue),
            "overdue_days": days,
            "avg_overdue_days": js_round(dollar_days / overdue),
            "balance_figure": usd(balance),
            "invoice_count": int(client.get("invoices") or 0),
            "slow_payer": slug in slow,
            "dollar_days": dollar_days,
        })

    rows.sort(key=lambda r: (-r["dollar_days"], r["client"]))
    return rows[:limit]


def hygiene_rows(
    data: dict[str, Any], limit: int = _HYGIENE_LIMIT
) -> list[dict[str, Any]]:
    """Bookkeeping gaps worth closing, ordered by the dollars behind them."""
    rows: list[dict[str, Any]] = []

    unattached = data.get("unattached_cost") or {}
    for account in unattached.get("accounts") or []:
        if not account.get("is_cost_of_service"):
            continue
        amount = float(account.get("amount") or 0)
        if amount <= 0:
            continue
        label = account.get("account", "")
        rows.append({
            "id": f"hygiene:{_slug(label)}",
            "label": label,
            "amount": amount,
            "figure": usd(amount),
            "kind": "untagged_cost",
        })

    gap = coverage_gap(data)
    if gap is not None:
        _coverage, unclassified = gap
        rows.append({
            "id": "hygiene:unclassified-income",
            "label": "Unclassified income",
            "amount": unclassified,
            "figure": usd(unclassified),
            "kind": "unclassified_income",
        })

    rows.sort(key=lambda r: (-r["amount"], r["label"]))
    return rows[:limit]
