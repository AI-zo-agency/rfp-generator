"""The two ranked lists the QuickBooks insight panel renders.

Pure functions over the overview payload. Every figure the reader sees is
produced here, so the language model never has to compute or repeat one — it
only annotates these rows by id.
"""

from __future__ import annotations

import re
from typing import Any

from app.financial.qb_signals import usd

_CHASE_LIMIT = 5
_HYGIENE_LIMIT = 5


def _slug(name: str) -> str:
    """Stable id fragment. "City of Umatilla" -> "cityofumatilla"."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def row_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {row["id"] for row in rows}


def chase_rows(data: dict[str, Any], limit: int = _CHASE_LIMIT) -> list[dict[str, Any]]:
    """Open receivables worth a phone call, ranked by dollar-days outstanding.

    Dollar-days (amount x days late) is a standard receivables measure and needs
    no invented weighting, which keeps the ordering explainable when someone asks
    why a row sits where it does.

    ponytail: `amount` is the client's whole balance while `oldest_days` is the
    age of their oldest invoice, because QuickBooks' AR panel carries no
    per-client overdue split. That overstates dollar-days for a client with a
    mix of current and late invoices. Move to per-invoice rows if the ordering
    ever looks wrong.
    """
    ar = data.get("ar") or {}
    dso = data.get("dso") or {}

    dso_days = dso.get("dso_days")
    slow: set[str] = set()
    if dso_days is not None:
        threshold = max(float(dso_days) * 1.75, 40)
        slow = {
            _slug(c.get("client", ""))
            for c in (dso.get("slowest_clients") or [])
            if float(c.get("avg_days") or 0) >= threshold
        }

    rows: list[dict[str, Any]] = []
    for client in ar.get("clients") or []:
        amount = float(client.get("amount") or 0)
        days = int(client.get("oldest_days") or 0)
        if amount <= 0 or days <= 0:
            continue
        slug = _slug(client.get("client", ""))
        rows.append({
            "id": f"chase:{slug}",
            "client": client.get("client", ""),
            "amount": amount,
            "figure": usd(amount),
            "oldest_days": days,
            "invoices": int(client.get("invoices") or 0),
            "slow_payer": slug in slow,
            "dollar_days": amount * days,
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

    rbc = data.get("revenue_by_class") or {}
    cc = data.get("class_coverage") or {}
    coverage = rbc.get("coverage_pct")
    if coverage is None:
        coverage = cc.get("coverage_pct")
    unclassified = rbc.get("unclassified")
    if unclassified is None:
        unclassified = cc.get("unclassified") or 0
    if coverage is not None and float(coverage) < 90 and float(unclassified) > 0:
        rows.append({
            "id": "hygiene:unclassified-income",
            "label": "Unclassified income",
            "amount": float(unclassified),
            "figure": usd(float(unclassified)),
            "kind": "unclassified_income",
        })

    rows.sort(key=lambda r: (-r["amount"], r["label"]))
    return rows[:limit]
