"""The two forecasts a model was measured to be worth paying for.

Chosen by backtest against this ledger, not by reputation. Ten models were run
over rolling origins; `google/gemini-3.6-flash` was the only one that earned a
place, and only at two horizons:

    13-week cash    2.96% against 18.7% (Claude), 22.2% (ChatGPT) and 25.7% for
                    the arithmetic model this replaces
    full year       0.31% on the scored holdout, ~9.2% over 18 origins, against
                    9.8% for `ytd / months * 12`

Nothing else is asked of a model here. Monthly revenue was 19.8% at best across
every model tested and is not generated at all; the quarter is computed in
Python. Both those decisions live in `qb_forecast`.

Two findings from that testing are load-bearing in the prompts below:

**Tables, not conclusions.** An earlier prompt supplied computed year-over-year
growth and prior-year remainders as "help". It made every model worse — gemini
9.2% to 11.3%, sonnet 9.6% to 13.6% — by anchoring them onto the seasonal
method, the weakest statistic tested at 14.5%. The prompts hand over rows and
let the model choose its own method.

**The bill-entry lag has to be stated.** Without it a model reads August's 68.9%
gross margin as real. It is roughly 51% once late supplier bills land, and
`qb_cost_completeness` measures the curve that says so.

Storage reuses the shared `ai_insights` table under its own `source`, so a
failed night degrades to the previous forecast rather than an empty tab, and no
migration is needed. Never raises: the nightly sync completes whether or not the
model cooperates.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from statistics import median
from typing import Any

from app.financial import qb_repository as repo
from app.financial.ai_insights_repository import upsert_insight
from app.services.llm import chat_json_soft, resolve_llm_model

logger = logging.getLogger(__name__)

SOURCE = "quickbooks_forecast"
CASH_NODE = "qb_forecast_cash"
YEAR_NODE = "qb_forecast_year"
# Deliberately NOT in _FORECAST_LLM_NODES, so it routes to the prose model
# rather than the forecasting one. Gemini works out the numbers well and
# explains them badly; this step is only the explaining.
NARRATIVE_NODE = "qb_forecast_narrative"

# 3.6 Flash spends ~1k tokens reasoning before it answers, and a truncated
# response comes back as empty content rather than an error — the failure mode
# that made the first benchmark run look broken. The cash forecast emits 13 week
# objects of five fields each on top of that reasoning and overran 4096 on the
# first live call, so it gets the same 8192 `qb_insights` already justifies.
_MAX_TOKENS = 4096
_CASH_MAX_TOKENS = 8192
_TEMPERATURE = 0.2

# Days sampled from the collection curve. Dense early, where most of the money
# lands and the shape actually matters.
_CURVE_DAYS = (7, 14, 21, 30, 45, 60, 75, 90, 120)

# Below this an invoice is noise in a prompt that already lists every other one.
_MIN_OPEN_BALANCE = 1.0

_WEEKS = 13


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _money(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# ── evidence ─────────────────────────────────────────────────────────────────

def collection_curve(realm_id: str) -> dict[str, Any] | None:
    """Share of invoiced dollars collected by day N, from settled invoices.

    Built from `qb_txn_links`, which carries Payment-to-Invoice links only. That
    is the whole inflow side; there is no BillPayment-to-Bill link in the mirror,
    so the outflow side has no equivalent curve and the prompts say so rather
    than implying a symmetry that does not exist.
    """
    invoices = {
        str(row["qbo_id"]): row
        for row in repo.list_invoices(realm_id)
        if row.get("qbo_id") is not None and not row.get("is_deleted")
    }
    payments = {
        str(row["qbo_id"]): row
        for row in repo.list_payments(realm_id)
        if row.get("qbo_id") is not None and not row.get("is_deleted")
    }
    samples: list[tuple[int, float]] = []
    for link in repo.list_txn_links(realm_id, to_type="Invoice"):
        invoice = invoices.get(str(link.get("to_id")))
        payment = payments.get(str(link.get("from_id")))
        if not invoice or not payment:
            continue
        raised, paid = _as_date(invoice.get("txn_date")), _as_date(payment.get("txn_date"))
        if not raised or not paid or paid < raised:
            continue
        samples.append(((paid - raised).days, _money(link.get("amount"))))

    total = sum(amount for _, amount in samples)
    if not samples or total <= 0:
        return None
    lags = sorted(day for day, _ in samples)
    return {
        "sample_invoices": len(samples),
        "sample_dollars": round(total, 2),
        "median_days": lags[len(lags) // 2],
        "cumulative_pct": {
            str(day): round(
                sum(a for d, a in samples if d <= day) / total * 100, 1
            )
            for day in _CURVE_DAYS
        },
    }


def open_invoices(realm_id: str, *, as_of: date) -> list[dict[str, Any]]:
    """Every unpaid invoice with its age. Aggregated AR totals are not enough —
    a 3-day-old $40,920 invoice and a 132-day-old one collect very differently,
    and the panel's per-client rollup hides which is which."""
    rows: list[dict[str, Any]] = []
    for invoice in repo.list_invoices(realm_id):
        if invoice.get("is_deleted"):
            continue
        balance = _money(invoice.get("balance"))
        raised = _as_date(invoice.get("txn_date"))
        if balance < _MIN_OPEN_BALANCE or not raised or raised > as_of:
            continue
        due = _as_date(invoice.get("due_date"))
        rows.append({
            "client": invoice.get("customer_name") or "Unknown",
            "raised": raised.isoformat(),
            "due": due.isoformat() if due else None,
            "balance": round(balance, 2),
            "age_days": (as_of - raised).days,
            "overdue_days": max(0, (as_of - due).days) if due else 0,
        })
    return sorted(rows, key=lambda r: -r["balance"])


def open_bills(realm_id: str, *, as_of: date, limit: int = 25) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bill in repo.list_bills(realm_id):
        if bill.get("is_deleted"):
            continue
        balance = _money(bill.get("balance"))
        raised = _as_date(bill.get("txn_date"))
        if balance < _MIN_OPEN_BALANCE or not raised or raised > as_of:
            continue
        due = _as_date(bill.get("due_date")) or raised
        rows.append({
            "vendor": bill.get("vendor_name") or "Unknown",
            "due": due.isoformat(),
            "balance": round(balance, 2),
            "overdue_days": max(0, (as_of - due).days),
        })
    return sorted(rows, key=lambda r: -r["balance"])[:limit]


def _label(month: Any, year: Any) -> str:
    text = str(month or "").strip()
    if not text:
        return ""
    return text if (year and str(year) in text) or not year else f"{text} {year}"


def monthly_outflow(realm_id: str) -> dict[str, Any]:
    """Cash out per month, plus how far the ledger rows overstate the bank.

    Purchases and bill payments summed come to $151,498/month on this ledger
    while the cash-flow statement says the bank actually moved $130,517 — some
    rows do not shift cash. Handing over the raw columns without that ratio is
    how a model lands 17% high on burn, which is exactly what happened in
    testing. The correction is a property of the data, not a hint about method.
    """
    by_month: dict[str, float] = {}
    for lister in (repo.list_bill_payments, repo.list_purchases):
        for row in lister(realm_id):
            if row.get("is_deleted"):
                continue
            when = _as_date(row.get("txn_date"))
            if not when:
                continue
            key = when.strftime("%Y-%m")
            by_month[key] = by_month.get(key, 0.0) + _money(row.get("total_amt"))
    return {"by_month": {k: round(v, 2) for k, v in sorted(by_month.items())}}


def outflow_shape(realm_id: str, *, since: str) -> dict[str, Any] | None:
    """When inside a month the money actually leaves.

    Money out is not smooth on this ledger and a forecast that spreads it evenly
    is wrong every week. Measured from 2025: day 10 alone carries 11.7% of all
    outflow and days 23-25 another 19.4% — a semi-monthly payroll shape. Real
    weekly cash out swings 48% around its mean, from $5,593 to $81,324.

    Given only monthly totals the model has nothing to vary on and returns
    thirteen identical weeks, which is exactly what the first live forecast did.
    """
    buckets = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
    by_day: dict[int, float] = {}
    total = 0.0
    for lister in (repo.list_bill_payments, repo.list_purchases):
        for row in lister(realm_id, txn_date__gte=since):
            if row.get("is_deleted"):
                continue
            when = _as_date(row.get("txn_date"))
            if not when:
                continue
            amount = _money(row.get("total_amt"))
            total += amount
            buckets[min((when.day - 1) // 7 + 1, 4)] += amount
            by_day[when.day] = by_day.get(when.day, 0.0) + amount
    if total <= 0:
        return None
    peaks = sorted(by_day.items(), key=lambda kv: -kv[1])[:5]
    return {
        "week_of_month_pct": {k: round(v / total * 100, 1) for k, v in buckets.items()},
        "peak_days": [{"day": d, "pct": round(a / total * 100, 1)} for d, a in peaks],
    }


def _shape_lines(shape: dict[str, Any] | None) -> str:
    if not shape:
        return ""
    weeks = shape.get("week_of_month_pct") or {}
    names = {1: "days 1-7", 2: "days 8-14", 3: "days 15-21", 4: "days 22 to month end"}
    rows = "\n".join(
        f"  {names.get(int(k), k)}: {v}% of the month's outflow"
        for k, v in sorted(weeks.items(), key=lambda kv: int(kv[0]))
    )
    peaks = ", ".join(
        f"day {p['day']} ({p['pct']}%)" for p in (shape.get("peak_days") or [])
    )
    return (
        "Money does not leave evenly through the month. Historically:\n"
        f"{rows}\n"
        f"The heaviest single days are {peaks}. Shape the weekly outflow to match "
        "this pattern rather than spreading it evenly — a flat weekly figure is "
        "wrong every week.\n"
    )


def _outflow_table(outflow: dict[str, Any] | None, months: int = 18) -> str:
    rows = list((outflow or {}).get("by_month", {}).items())[-months:]
    if not rows:
        return "not available"
    return "\n".join(f"{m} | {v:,.0f}" for m, v in rows)


def _month_rows(sources: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    """Booked months oldest first across every year given, cost alongside.

    Two prior years, not one. The benchmark that chose this model ran on 32
    months of history; handing it 20 would be scoring a different prompt than
    the one that won.
    """
    rows: list[dict[str, Any]] = []
    for source in sources:
        trend = (source or {}).get("monthly_trend") or {}
        year = (source or {}).get("year")
        for month in trend.get("months") or []:
            if not month.get("amount"):
                continue
            rows.append({
                # The P&L column is already "Jan 2026"; appending the year
                # again produced "Jan 2024 2024" in the first live prompt.
                "month": _label(month.get("month"), year),
                "income": round(_money(month.get("amount")), 2),
                "cost_of_services": (
                    round(_money(month.get("cost_of_services")), 2)
                    if month.get("cost_of_services") is not None else None
                ),
            })
    return rows


def _prior_overviews(realm_id: str, year: int) -> list[dict[str, Any] | None]:
    """Cached panels for the two preceding years, oldest first.

    A year absent from the cache is skipped rather than fatal — a first-run
    realm has no history and still gets a forecast off what it does have.
    """
    out: list[dict[str, Any] | None] = []
    for past in (year - 2, year - 1):
        cached = repo.get_panel_cache(realm_id, past) or {}
        payload = cached.get("payload")
        if payload:
            out.append({**payload, "year": past})
    return out


def build_evidence(
    realm_id: str,
    overview: dict[str, Any],
    *,
    year: int,
    as_of: date,
) -> dict[str, Any]:
    """Everything both prompts draw on. Rows and totals only — no derived
    growth rates or ratios, because supplying those measurably hurt accuracy."""
    liquidity = overview.get("liquidity") or {}
    completeness = overview.get("cost_completeness") or {}
    history = [*_prior_overviews(realm_id, year), {**overview, "year": year}]
    return {
        "as_of": as_of.isoformat(),
        "cash_on_hand": round(_money(liquidity.get("cash")), 2),
        "open_invoices": open_invoices(realm_id, as_of=as_of),
        "open_bills": open_bills(realm_id, as_of=as_of),
        "collection_curve": collection_curve(realm_id),
        "months": _month_rows(history),
        "billing_vs_cash": (overview.get("billing_vs_cash") or {}).get("by_month"),
        "open_purchase_orders": round(
            _money((overview.get("purchase_orders") or {}).get("open_total")), 2
        ),
        "monthly_outflow": monthly_outflow(realm_id),
        "outflow_shape": outflow_shape(realm_id, since=f"{year - 1}-01-01"),
        # Preserved as None when absent rather than coerced to 0.0, which would
        # silently pass the guard in _outflow_caveat and produce a backwards
        # "rows run -16% high" claim.
        "net_cash_change_ytd": (
            round(_money(liquidity.get("net_cash_change")), 2)
            if liquidity.get("net_cash_change") is not None else None
        ),
        "cost_completeness_curve": completeness.get("curve"),
        "unsettled_cost_months": completeness.get("unsettled_months"),
    }


# ── prompts ──────────────────────────────────────────────────────────────────

_BUSINESS = (
    "Zo Agency is a US marketing and creative agency, trading since 2014, with "
    "roughly 40-56 active accounts. It bills a mix of recurring monthly retainers "
    "and one-off project work, split between government/municipal clients (slower, "
    "budget-cycle driven) and private enterprise. Revenue is recognised on the "
    "accrual basis when an invoice is raised, not when cash arrives.\n\n"
    "Data caveat you must account for: supplier bills are entered into the ledger "
    "weeks after the month they belong to, so the most recent two or three months "
    "understate cost and overstate gross margin. Income does not have this problem "
    "— it is complete once the month closes.\n\n"
    "Volatility: monthly income swings about 34% around its own mean, and there is "
    "no stable seasonality — the correlation of monthly shape between years is "
    "negative and the peak month moves every year. Do not assume last year's "
    "monthly pattern repeats."
)

_CASH_SYSTEM = (
    "You are a treasury analyst forecasting weekly cash for a marketing agency.\n\n"
    f"{_BUSINESS}\n\n"
    "Forecast the next 13 weeks of cash. Respond with ONLY a JSON object, no "
    "markdown fence:\n"
    '{"weeks":[{"week":1,"ending":"YYYY-MM-DD","from_open_invoices":<n>,'
    '"from_new_billing":<n>,"outflow":<n>,"closing_balance":<n>}],'
    '"trough":{"amount":<n>,"week":<n>},"low":<n>,"high":<n>,'
    '"assumptions":"<= 60 words","risks":["<short>","<short>","<short>"]}\n'
    "Give exactly 13 week entries. `low` and `high` bound an 80% interval on the "
    "closing balance at week 13. Separate cash from invoices that already exist "
    "from cash from billing not yet raised — they carry very different confidence "
    "and the reader needs to see the mix shift across the horizon."
)

_YEAR_SYSTEM = (
    "You are a senior FP&A analyst forecasting full-year revenue for a marketing "
    "agency.\n\n"
    f"{_BUSINESS}\n\n"
    "Forecast the calendar year's TOTAL INCOME — all twelve months, including "
    "those already booked. Respond with ONLY a JSON object, no markdown fence:\n"
    '{"reasoning":"<= 60 words","point":<n>,"low":<n>,"high":<n>,'
    '"remaining_months":<n>,"confidence":"low|medium|high"}\n'
    "`point` must equal the booked year-to-date plus your forecast for the "
    "remaining months. `low` and `high` bound an 80% interval."
)


def _invoice_table(rows: list[dict[str, Any]]) -> str:
    header = "client | raised | due | open_balance | age_days | overdue_days"
    lines = [
        f"{r['client']} | {r['raised']} | {r['due'] or 'n/a'} | "
        f"{r['balance']:.0f} | {r['age_days']} | {r['overdue_days']}"
        for r in rows
    ]
    return "\n".join([header, *lines]) if lines else "none"


def _month_table(rows: list[dict[str, Any]]) -> str:
    header = "month | income | cost_of_services"
    lines = []
    for row in rows:
        cost = row["cost_of_services"]
        lines.append(
            f"{row['month']} | {row['income']:.0f} | "
            f"{cost:.0f}" if cost is not None
            else f"{row['month']} | {row['income']:.0f} | n/a"
        )
    return "\n".join([header, *lines]) if lines else "none"


def cash_prompt(evidence: dict[str, Any]) -> str:
    curve = evidence.get("collection_curve") or {}
    curve_lines = "\n".join(
        f"  within {day} days: {pct}%"
        for day, pct in (curve.get("cumulative_pct") or {}).items()
    )
    bills = "\n".join(
        f"{b['vendor']} | due {b['due']} | {b['balance']:.0f} | overdue {b['overdue_days']}d"
        for b in evidence.get("open_bills") or []
    ) or "none"
    open_total = sum(r["balance"] for r in evidence.get("open_invoices") or [])
    return (
        f"Position as of {evidence['as_of']}.\n\n"
        f"Cash in bank: {evidence['cash_on_hand']:,.0f}\n"
        f"Open receivables: {open_total:,.0f} across "
        f"{len(evidence.get('open_invoices') or [])} unpaid invoices\n"
        f"Open purchase orders (committed, not yet billed): "
        f"{evidence['open_purchase_orders']:,.0f}\n\n"
        f"Every unpaid customer invoice:\n{_invoice_table(evidence.get('open_invoices') or [])}\n\n"
        f"Largest unpaid supplier bills:\n{bills}\n\n"
        f"How fast customers pay, from {curve.get('sample_invoices', 0)} settled invoices "
        f"totalling {curve.get('sample_dollars', 0):,.0f} (median "
        f"{curve.get('median_days', 'n/a')} days):\n{curve_lines}\n\n"
        f"Monthly income and cost of services:\n"
        f"{_month_table(evidence.get('months') or [])}\n\n"
        f"Monthly invoiced against collected:\n"
        f"{_billing_table(evidence.get('billing_vs_cash'))}\n\n"
        f"Monthly cash out (supplier bill payments plus direct purchases, as "
        f"recorded in the ledger):\n"
        f"{_outflow_table(evidence.get('monthly_outflow'))}\n\n"
        f"{_outflow_caveat(evidence)}\n\n"
        f"{_shape_lines(evidence.get('outflow_shape'))}\n"
        f"Forecast the {_WEEKS} weeks beginning {evidence['as_of']}."
    )


def _outflow_caveat(evidence: dict[str, Any]) -> str:
    """State how far the outflow rows overrun the bank, measured on this ledger.

    Without it a model either invents an outflow rate — the first live call
    assumed $22-24k/week against a measured $30,142 — or trusts the rows and
    forecasts a burn 17% too steep. Both happened in testing.
    """
    rows = (evidence.get("monthly_outflow") or {}).get("by_month") or {}
    billing = evidence.get("billing_vs_cash") or []
    as_of = str(evidence.get("as_of", ""))
    year, current_month = as_of[:4], as_of[:7]
    # The month in progress is excluded from both sides. Dividing nine months of
    # spend by nine when the ninth is three days old put the baseline 9% low on
    # the first live call.
    ledger = [v for m, v in rows.items() if m.startswith(year) and m < current_month]
    collected = sum(_money(r.get("collected")) for r in billing)
    net_change = evidence.get("net_cash_change_ytd")
    if not ledger or not collected or net_change is None:
        return (
            "Note: the cash-out rows above are ledger entries; some do not move "
            "the bank balance, so treat them as an upper bound."
        )
    # Collections are trimmed to the same complete months so both sides of the
    # subtraction cover the same span.
    collected_complete = sum(
        _money(r.get("collected")) for r in billing[: len(ledger)]
    ) or collected
    true_out = collected_complete - net_change
    if true_out <= 0:
        return (
            "Note: the cash-out rows above are ledger entries; some do not move "
            "the bank balance, so treat them as an upper bound."
        )
    overstatement = sum(ledger) / true_out
    if overstatement <= 1.0:
        # The rows are not overstating after all. Saying they run "-4% high" is
        # worse than saying nothing.
        return (
            f"Note: over the {len(ledger)} complete months of {year} the cash-out "
            f"rows total {sum(ledger):,.0f} against implied real outflow of "
            f"{true_out:,.0f}. Use roughly {true_out / len(ledger):,.0f} per month "
            f"as your outflow baseline."
        )
    return (
        f"Important: those cash-out rows overstate what actually left the bank. "
        f"Over the {len(ledger)} complete months of {year} they total "
        f"{sum(ledger):,.0f}, while collections of {collected_complete:,.0f} "
        f"against an actual net cash change of {net_change:,.0f} imply real "
        f"outflow of {true_out:,.0f} — the rows run about "
        f"{(overstatement - 1) * 100:.0f}% high. Use the implied figure of "
        f"{true_out / len(ledger):,.0f} per month "
        f"({true_out / len(ledger) / 4.33:,.0f} per week) as your outflow "
        f"baseline, not the raw rows."
    )


def _billing_table(rows: list[dict[str, Any]] | None) -> str:
    if not rows:
        return "not available"
    lines = [
        f"{r.get('month')} | invoiced {_money(r.get('invoiced')):.0f} | "
        f"collected {_money(r.get('collected')):.0f}"
        for r in rows
        if _money(r.get("invoiced")) or _money(r.get("collected"))
    ]
    return "\n".join(lines) or "not available"


def year_prompt(evidence: dict[str, Any], *, year: int) -> str:
    months = evidence.get("months") or []
    current = [m for m in months if str(year) in str(m["month"])]
    ytd = sum(m["income"] for m in current)
    return (
        f"Calendar year {year}. Actuals are complete through the last month listed "
        f"below ({len(current)} of 12 months booked).\n\n"
        f"Monthly income and cost of services:\n{_month_table(months)}\n\n"
        f"Monthly invoiced against collected:\n"
        f"{_billing_table(evidence.get('billing_vs_cash'))}\n\n"
        f"Year-to-date {year} total income: {ytd:,.0f}\n"
        f"Months remaining to forecast: {12 - len(current)}\n\n"
        f"Forecast TOTAL INCOME for all of calendar year {year}."
    )


# ── generation ───────────────────────────────────────────────────────────────

async def _ask(
    system: str, user: str, node: str, max_tokens: int = _MAX_TOKENS
) -> tuple[dict[str, Any], str]:
    payload, provider = await chat_json_soft(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens,
        temperature=_TEMPERATURE,
        tier="light",
        node_name=node,
    )
    return payload or {}, provider


_NARRATIVE_SYSTEM = (
    "You explain business finances to the owner of a marketing agency. He is in "
    "his sixties, runs the company well, and is not an accountant. He wants to "
    "know what is going to happen and what he should do about it.\n\n"
    "Write the way you would speak to him across a desk.\n\n"
    "Rules:\n"
    "- Short sentences. No sentence longer than about 20 words.\n"
    "- No jargon at all. Never write: receivables, AR, run-rate, baseline, "
    "annualised, volatility, YTD, aggregate, conversion, profile, methodology, "
    "80% interval, variance, trajectory. Say 'money customers owe you', "
    "'money coming in', 'so far this year', 'ups and downs' instead.\n"
    "- Round money the way a person says it: $1.55 million, about $30,000 a "
    "week, roughly $11,000. Write a negative as 'minus $25,000', never '$-25,000'.\n"
    "- At most three figures per answer. He can read the numbers off the chart; "
    "what he needs from you is what they add up to.\n"
    "- Use only the figures given to you. Never calculate a new one.\n"
    "- Say what it means for him, not how it was worked out.\n\n"
    "Respond with ONLY a JSON object, no markdown fence:\n"
    '{"cash":"<2-3 sentences about the next three months of cash>",'
    '"year":"<2-3 sentences about where the year is heading>",'
    '"watch":"<one sentence: the single thing to keep an eye on>"}'
)


def narrative_prompt(payload: dict[str, Any], year: int) -> str:
    """Hand over finished figures, pre-rounded, and ask only for words.

    Every number is formatted here so the writer never has to compute one — the
    same arrangement `qb_insights` uses, and the reason its briefs can be checked
    against the evidence they were given.
    """
    cash = payload.get("cash_13w") or {}
    year_out = payload.get("year") or {}
    weeks = cash.get("weeks") or []
    trough = cash.get("trough") or {}
    lines = [f"Money in the bank at the end of each of the next {len(weeks)} weeks:"]
    for week in weeks:
        lines.append(
            f"  week {week.get('week')} ending {week.get('ending')}: "
            f"${_money(week.get('closing_balance')):,.0f}"
        )
    if trough:
        lines.append(
            f"Lowest point: ${_money(trough.get('amount')):,.0f} in week "
            f"{trough.get('week')}."
        )
    if cash.get("low") is not None and cash.get("high") is not None:
        lines.append(
            f"Cash at the end could realistically be anywhere from "
            f"${_money(cash.get('low')):,.0f} to ${_money(cash.get('high')):,.0f}."
        )
    committed = sum(_money(w.get("from_open_invoices")) for w in weeks[:4])
    later = sum(_money(w.get("from_new_billing")) for w in weeks[8:])
    lines.append(
        f"In the first four weeks, ${committed:,.0f} comes from invoices already "
        f"sent. From week nine onward, ${later:,.0f} depends on work not yet "
        f"billed."
    )
    if year_out.get("point"):
        lines.append(
            f"\nFull year {year}: expected ${_money(year_out.get('point')):,.0f}."
        )
        if year_out.get("low") is not None and year_out.get("high") is not None:
            lines.append(
                f"  Could reasonably land between ${_money(year_out.get('low')):,.0f} "
                f"and ${_money(year_out.get('high')):,.0f}."
            )
    return "\n".join(lines) + "\n\nExplain this to him."


async def _narrate(payload: dict[str, Any], year: int) -> dict[str, Any] | None:
    if not (payload.get("cash_13w") or payload.get("year")):
        return None
    try:
        prose, _provider = await _ask(
            _NARRATIVE_SYSTEM, narrative_prompt(payload, year), NARRATIVE_NODE
        )
    except Exception:  # noqa: BLE001 — prose is the optional half
        return None
    return prose or None


async def _generate(evidence: dict[str, Any], year: int) -> dict[str, Any]:
    """Both forecasts concurrently. One failing must not take the other with it."""
    cash, year_out = await asyncio.gather(
        _ask(_CASH_SYSTEM, cash_prompt(evidence), CASH_NODE, _CASH_MAX_TOKENS),
        _ask(_YEAR_SYSTEM, year_prompt(evidence, year=year), YEAR_NODE),
        return_exceptions=True,
    )
    def _unwrap(result: Any) -> dict[str, Any] | None:
        if isinstance(result, BaseException) or not isinstance(result, tuple):
            return None
        payload, _provider = result
        return payload or None

    payload = {"cash_13w": _unwrap(cash), "year": _unwrap(year_out)}
    # Second pass, different model: the numbers are settled, this only turns
    # them into sentences an owner can read. Failure leaves `plain` empty and
    # the tab falls back to showing figures without commentary.
    payload["plain"] = await _narrate(payload, year)
    return payload


def generate_and_store(
    realm_id: str,
    overview: dict[str, Any],
    year: int,
    as_of: str,
) -> str:
    """Tonight's forecasts, persisted. Returns "ok", "empty" or "failed".

    Never raises. Called from a sync worker thread with no running event loop,
    so `asyncio.run` is safe — the same arrangement `qb_insights` uses.
    """
    model = resolve_llm_model("light", node_name=CASH_NODE)
    try:
        evidence = build_evidence(
            realm_id, overview, year=year, as_of=_as_date(as_of) or date.today()
        )
        payload = asyncio.run(_generate(evidence, year))
    except Exception as exc:  # noqa: BLE001 — a bad forecast must not fail the sync
        logger.warning(
            "operation=qb_forecast_llm realm_id=%s as_of=%s status=failed reason=%s",
            realm_id, as_of, str(exc)[:200],
        )
        return "failed"

    status = "ok" if (payload.get("cash_13w") or payload.get("year")) else "empty"
    if status == "empty":
        # Stored as a failure so `get_latest_insight` skips it and the tab keeps
        # showing yesterday's forecast rather than blanking.
        logger.warning(
            "operation=qb_forecast_llm realm_id=%s as_of=%s status=empty model=%s",
            realm_id, as_of, model,
        )
    try:
        upsert_insight(
            source=SOURCE,
            scope_key=realm_id,
            as_of=as_of,
            payload=payload,
            evidence={"year": year, "months": len(evidence.get("months") or [])},
            provider="openrouter",
            model=model,
            status="ok" if status == "ok" else "failed",
        )
    except Exception as exc:  # noqa: BLE001 — storage failure must not fail the sync
        logger.warning(
            "operation=qb_forecast_llm realm_id=%s status=store_failed reason=%s",
            realm_id, str(exc)[:200],
        )
        return "failed"
    logger.info(
        "operation=qb_forecast_llm realm_id=%s as_of=%s status=%s model=%s "
        "cash=%s year=%s",
        realm_id, as_of, status, model,
        bool(payload.get("cash_13w")), bool(payload.get("year")),
    )
    return status
