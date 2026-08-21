"""Turns the QuickBooks overview payload into a short list of things a human
should actually do something about.

A signal only exists once a threshold is crossed, and it says so in a sentence.

Ported from frontend/src/financial/lib/qb-signals.ts. The nightly insight job and
the dashboard both read this, so the thresholds live in exactly one place.
"""

from __future__ import annotations

import math
from typing import Any

_SEVERITY_RANK = {"critical": 0, "warn": 1, "info": 2}

# QuickBooks buckets that mean genuinely late, not merely outstanding.
_LATE_BUCKETS = {"61-90 days", "90+ days"}


def js_round(value: float) -> int:
    """Round half away from zero, matching JavaScript's Math.round.

    Python's built-in round() is banker's rounding, which would render 2.5 as 2
    and silently disagree with the figures this dashboard has always shown.
    """
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def usd(value: float) -> str:
    """Whole dollars with thousands separators: 14419.33 -> "$14,419"."""
    rounded = js_round(value or 0)
    return f"-${abs(rounded):,}" if rounded < 0 else f"${rounded:,}"


def _plural(count: int, one: str, many: str | None = None) -> str:
    return one if count == 1 else (many or f"{one}s")


def slow_payer_threshold(dso_days: float | None) -> float | None:
    """Days-late cutoff for a "slow payer": 1.75x the fleet average, floored at 40.

    Shared by the slow-payers signal and the chase rows so tuning one always
    tunes the other.
    """
    if dso_days is None:
        return None
    return max(float(dso_days) * 1.75, 40)


def coverage_gap(data: dict[str, Any]) -> tuple[float, float] | None:
    """Resolve (coverage_pct, unclassified) for the revenue-segment gap.

    Reads `revenue_by_class` first, falling back to `class_coverage`. Returns
    None when there's no gap worth reporting: coverage missing, coverage at or
    above 90%, or nothing unclassified.

    The `is None` checks are load-bearing — a legitimate coverage_pct of 0 must
    be used as-is, not treated as absent and fallen back from.
    """
    rbc = data.get("revenue_by_class") or {}
    cc = data.get("class_coverage") or {}
    coverage = rbc.get("coverage_pct")
    if coverage is None:
        coverage = cc.get("coverage_pct")
    unclassified = rbc.get("unclassified")
    if unclassified is None:
        unclassified = cc.get("unclassified") or 0
    if coverage is None or float(coverage) >= 90 or float(unclassified) <= 0:
        return None
    return float(coverage), float(unclassified)


def derive_signals(data: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    # 1. Receivables aged past the point of "they'll get to it".
    ar = data.get("ar") or {}
    ar_total = float(ar.get("total") or 0)
    if ar_total > 0:
        late = sum(
            float(b.get("amount") or 0)
            for b in (ar.get("buckets") or [])
            if b.get("label") in _LATE_BUCKETS
        )
        if late > 0:
            share = late / ar_total
            clients = sorted(
                ar.get("clients") or [],
                key=lambda c: c.get("oldest_days") or 0,
                reverse=True,
            )
            worst = clients[0] if clients else None
            pct = js_round(share * 100)
            detail = f"{pct}% of what's owed."
            if worst:
                detail = (
                    f"{pct}% of what's owed. "
                    f"Oldest is {worst['client']} at {worst['oldest_days']} days."
                )
            out.append({
                "id": "ar-late",
                "severity": "critical" if share >= 0.1 else "warn",
                "headline": "Receivables have aged past 60 days",
                "figure": usd(late),
                "detail": detail,
                "go_to": "open",
            })

    # 2. Payables exceed the cash on hand to cover them.
    cash = (data.get("liquidity") or {}).get("cash")
    ap_total = float((data.get("ap") or {}).get("total") or 0)
    if cash is not None and float(cash) > 0 and ap_total > float(cash):
        out.append({
            "id": "ap-over-cash",
            "severity": "critical",
            "headline": "You owe more than you're holding",
            "figure": usd(ap_total - float(cash)),
            "detail": (
                f"{usd(ap_total)} in bills against {usd(float(cash))} cash. "
                "Collections need to land before these do."
            ),
            "go_to": "open",
        })

    # 3. Clients who take far longer than everyone else to pay.
    dso = data.get("dso") or {}
    dso_days = dso.get("dso_days")
    slowest = dso.get("slowest_clients") or []
    threshold = slow_payer_threshold(dso_days)
    if threshold is not None and slowest:
        slow = [c for c in slowest if float(c.get("avg_days") or 0) >= threshold]
        if slow:
            tied = sum(float(c.get("amount") or 0) for c in slow)
            out.append({
                "id": "slow-payers",
                "severity": "warn",
                "headline": (
                    f"{len(slow)} {_plural(len(slow), 'client')} "
                    "pay well after everyone else"
                ),
                "figure": usd(tied),
                "detail": (
                    f"{js_round(threshold)}+ days versus a {dso_days}-day average."
                ),
                "go_to": "clients",
            })

    # 4. Cost that can't be attributed to a client — why margins read high.
    unattached = data.get("unattached_cost") or {}
    unattached_pct = unattached.get("unattached_pct")
    if unattached_pct is not None and float(unattached_pct) >= 25:
        out.append({
            "id": "cost-untagged",
            "severity": "warn" if float(unattached_pct) >= 50 else "info",
            "headline": (
                f"{js_round(float(unattached_pct))}% of purchases "
                "aren't tied to a client"
            ),
            "figure": usd(float(unattached.get("cost_of_service_unattached") or 0)),
            "detail": (
                "Billable cost with nowhere to land, so per-client margin "
                "reads higher than it is."
            ),
            "go_to": "costs",
        })

    # 5. Income landing outside any revenue segment.
    gap = coverage_gap(data)
    if gap is not None:
        coverage, unclassified = gap
        out.append({
            "id": "segment-gap",
            "severity": "warn" if coverage < 70 else "info",
            "headline": "Some income isn't assigned to a segment",
            "figure": usd(unclassified),
            "detail": (
                f"{js_round(coverage)}% of income is classified — "
                "the rest can't be split by line of business."
            ),
            "go_to": "revenue",
        })

    # 6. Spend concentrated in a handful of vendors.
    ev = data.get("expenses_by_vendor") or {}
    top3 = ev.get("top3_concentration_pct")
    if top3 is not None and float(top3) >= 50 and int(ev.get("vendor_count") or 0) > 3:
        out.append({
            "id": "vendor-concentration",
            "severity": "info",
            "headline": f"Top 3 vendors carry {js_round(float(top3))}% of spend",
            "figure": usd(float(ev.get("total") or 0)),
            "detail": f"Across {ev.get('vendor_count')} vendors total.",
            "go_to": "costs",
        })

    # 7. Collections falling behind billing.
    bvc = data.get("billing_vs_cash") or {}
    invoiced = float(bvc.get("invoiced_total") or 0)
    rate = bvc.get("collection_rate_pct")
    if invoiced > 0 and rate is not None and float(rate) < 85:
        out.append({
            "id": "collection-rate",
            "severity": "warn" if float(rate) < 70 else "info",
            "headline": "Collections are trailing what you billed",
            "figure": f"{js_round(float(rate))}%",
            "detail": (
                f"{usd(float(bvc.get('open_ar') or 0))} of this year's invoicing "
                "is still outstanding."
            ),
            "go_to": "today",
        })

    # 8. The data itself is stale or incomplete — say so rather than showing gaps.
    failed = list((data.get("errors") or {}).keys())
    if data.get("sync_status") == "failed" or failed:
        sync_failed = data.get("sync_status") == "failed"
        out.append({
            "id": "sync",
            "severity": "warn" if sync_failed else "info",
            "headline": (
                "The last QuickBooks sync failed"
                if sync_failed
                else f"{len(failed)} {_plural(len(failed), 'panel')} couldn't load"
            ),
            "detail": (
                ", ".join(failed) if failed else "Figures below may be out of date."
            ),
        })

    out.sort(key=lambda s: _SEVERITY_RANK[s["severity"]])
    return out
