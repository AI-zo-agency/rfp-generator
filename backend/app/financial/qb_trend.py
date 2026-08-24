"""Revenue, margin, concentration, and whether per-client margin can be trusted.

`build_overview` assembles twenty-three panels and the insight evidence read six
of them. The four that answer "is revenue growing", "which clients make money",
and "how does this compare to last year" were computed nightly, cached, rendered
elsewhere on the tab, and never shown to the model. This is those four.

Two partial-period traps live in the real data and are handled here rather than
trusted to the prompt, because both produce confident false statements:

The current month is incomplete. August 2026 showed $24,614 on the 24th. Against
July's $101,829 that reads as a 76% collapse; it is three-quarters of a month.
So the newest booked month is dropped from every comparison.

The current year is incomplete. 2026 income of $1,047,937 against 2025's
$1,278,963 is eight months against twelve; read as a decline it is simply false.
So every comparison is same-months-both-years.

The intra-year series alone is actively misleading: 2026 descends from $213,212
in March to $101,829 in July, which reads as collapse. Against 2025 it is a
strong year losing its lead — the opposite conclusion.
"""

from __future__ import annotations

from typing import Any

from app.financial.qb_signals import js_round, usd

_MIN_OVERLAP_MONTHS = 3
_CONCENTRATION_PCT = 20.0
_ATTRIBUTION_PCT = 70.0


def _booked(trend: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [m for m in ((trend or {}).get("months") or []) if m.get("amount")]


def _short(month: str) -> str:
    """"Jan 2026" -> "Jan", so two years line up by name."""
    return (month or "").split()[0] if month else ""


def _signed_pct(value: float) -> str:
    """"+68%" / "-12%" / "0%". Flat reads as flat, not as "+0%"."""
    rounded = js_round(value)
    return "0%" if rounded == 0 else f"{rounded:+d}%"


def _range_label(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{names[0]}-{names[-1]}"


def _overlap(
    current: dict[str, Any] | None, prior: dict[str, Any] | None
) -> tuple[list[str], dict[str, dict], dict[str, dict]]:
    """Months complete in the current year and also booked in the prior one.

    The current year drops its newest booked month — that is the one still being
    invoiced. The prior year keeps all of its; only the intersection is used.
    """
    current_months = _booked(current)[:-1]
    prior_by = {_short(m["month"]): m for m in _booked(prior)}
    current_by = {_short(m["month"]): m for m in current_months}
    names = [_short(m["month"]) for m in current_months if _short(m["month"]) in prior_by]
    return names, current_by, prior_by


def _revenue_trend(names: list[str], current: dict, prior: dict) -> dict | None:
    current_total = sum(current[n]["amount"] for n in names)
    prior_total = sum(prior[n]["amount"] for n in names)
    if prior_total <= 0:
        return None

    change = (current_total - prior_total) / prior_total * 100
    first, last = names[0], names[-1]

    def month_change(name: str) -> str | None:
        was = prior[name]["amount"]
        return None if was <= 0 else _signed_pct(
            (current[name]["amount"] - was) / was * 100
        )

    detail = (
        f"{_range_label(names)}: {usd(current_total)} against "
        f"{usd(prior_total)} over the same months last year."
    )
    first_change, last_change = month_change(first), month_change(last)
    if len(names) >= 2 and first_change and last_change:
        # Two computed facts rather than an invented "deceleration" metric: the
        # arc is what matters, and +68% in January against 0% in July says it.
        detail += (
            f" Month by month the gap ran {first_change} in {first} "
            f"to {last_change} in {last}."
        )
    return {
        "id": "margin:revenue-trend",
        "label": "Revenue against last year",
        "figure": _signed_pct(change),
        "detail": detail,
        "kind": "revenue_trend",
    }


def _margin_trend(names: list[str], current: dict, prior: dict) -> dict | None:
    if not all(
        "gross_profit" in current[n] and "gross_profit" in prior[n] for n in names
    ):
        return None
    current_income = sum(current[n]["amount"] for n in names)
    prior_income = sum(prior[n]["amount"] for n in names)
    if current_income <= 0 or prior_income <= 0:
        return None

    current_pct = sum(current[n]["gross_profit"] for n in names) / current_income * 100
    prior_pct = sum(prior[n]["gross_profit"] for n in names) / prior_income * 100
    points = js_round(current_pct) - js_round(prior_pct)
    direction = "up" if points > 0 else "down" if points < 0 else "level at"
    return {
        "id": "margin:margin-trend",
        "label": "Gross margin against last year",
        "figure": f"{js_round(current_pct)}%",
        "detail": (
            f"{_range_label(names)}: {js_round(current_pct)}% against "
            f"{js_round(prior_pct)}% over the same months last year, "
            f"{direction} {abs(points)} points."
        ),
        "kind": "margin_trend",
    }


def _concentration(data: dict[str, Any]) -> dict | None:
    sales = data.get("sales_by_customer") or {}
    total = float(sales.get("total") or 0)
    clients = sales.get("clients") or []
    if total <= 0 or not clients:
        return None

    top = clients[0]
    share = float(top.get("amount") or 0) / total * 100
    if share < _CONCENTRATION_PCT:
        return None

    top_five = sum(float(c.get("amount") or 0) for c in clients[:5]) / total * 100
    return {
        "id": "margin:concentration",
        "label": "Revenue concentration",
        "figure": f"{js_round(share)}%",
        "detail": (
            f"{top.get('client', 'The largest client')} is {js_round(share)}% of "
            f"revenue and the top five are {js_round(top_five)}%."
        ),
        "kind": "concentration",
    }


def _attribution(data: dict[str, Any]) -> dict | None:
    """Whether the per-client margins on this dashboard mean anything yet.

    Deliberately an error bar, not a correction. Spreading the unattributed cost
    pro-rata by revenue pulls every client toward the company average by
    construction — EverFast's 85% becomes ~51%, which is just the company margin
    restated — so it manufactures precision while destroying the discrimination
    that made the number worth reading. Stating the size of the gap is the
    honest answer to "which clients make money": you cannot tell yet, and here
    is how far off you are.
    """
    profitability = data.get("client_profitability") or {}
    pl = data.get("pl_summary") or {}
    cost = pl.get("cost_of_services")
    if cost is None or float(cost) <= 0:
        return None

    attributed = float(profitability.get("attributed_expense") or 0)
    coverage = attributed / float(cost) * 100
    if coverage >= _ATTRIBUTION_PCT:
        return None

    unattributed = float(cost) - attributed
    detail = (
        f"Only {js_round(coverage)}% of cost of services lands on a client, so "
        f"per-client margin cannot be measured yet."
    )
    income = pl.get("income")
    if income and float(income) > 0:
        overstated = js_round(unattributed / float(income) * 100)
        # The last sentence is load-bearing. Without it the model reads the
        # points figure as a haircut on company gross margin and writes "the
        # 52% is partly illusory" — false, since this cost is already inside
        # cost of services and therefore already inside that 52%. Only the
        # per-client split is affected.
        detail += (
            f" Spread across revenue the missing cost is about {overstated} "
            f"points, so every per-client margin on this dashboard reads that "
            f"much too high. Company gross margin already counts this cost and "
            f"is not affected."
        )
    return {
        "id": "margin:attribution",
        "label": "Client margin is not measurable yet",
        "figure": usd(unattributed),
        "detail": detail,
        "kind": "attribution",
    }


def margin_rows(
    data: dict[str, Any], prior: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Revenue, margin, concentration and attribution, each only when it holds.

    Ordered as a reader would ask: how is the top line, how is profit, what is
    the risk, can any of it be trusted. That ordering carries the same judgment
    an impact score would, without a weighted guess to defend.

    A missing prior year drops the two trend rows and leaves the rest standing,
    the same degradation rule as everything else in this panel.
    """
    rows: list[dict[str, Any]] = []

    names, current, prior_by = _overlap(
        data.get("monthly_trend"), (prior or {}).get("monthly_trend")
    )
    if len(names) >= _MIN_OVERLAP_MONTHS:
        for builder in (_revenue_trend, _margin_trend):
            row = builder(names, current, prior_by)
            if row is not None:
                rows.append(row)

    for builder in (_concentration, _attribution):
        row = builder(data)
        if row is not None:
            rows.append(row)

    return rows
