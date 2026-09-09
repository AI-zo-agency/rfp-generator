"""Weekly backtests for 2025 — cash (1) then revenue (2).

Cash: next-week collections / outflow / net from the ledger, 13-week lookback.
Revenue: next-week invoiced total income, 10-week lookback (parity with monthly).

Usage:

    cd backend && MODE=cash YEAR=2025 python scripts/backtest_weekly_2025.py
    cd backend && MODE=revenue YEAR=2025 python scripts/backtest_weekly_2025.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any, Literal

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.financial import qb_repository as repo  # noqa: E402
from app.services.llm import _financial_openrouter_key, _openrouter_key  # noqa: E402

_DEFAULT_MODEL = "google/gemini-3.6-flash"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_CASH_LOOKBACK = 13
_REVENUE_LOOKBACK = 10

Mode = Literal["cash", "revenue"]


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _money(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _week_ending_sunday(day: date) -> date:
    return day + timedelta(days=(6 - day.weekday()))


def _ape(actual: float, forecast: float) -> float | None:
    if abs(actual) < 1.0:
        return None  # near-zero weeks: skip absolute % (undefined / explosive)
    return abs(forecast - actual) / abs(actual) * 100.0


def _extract_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    text = (
        text.replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0:
            raise
        blob = text[start:] if end <= start else text[start : end + 1]
        blob = re.sub(r",\s*}", "}", blob)
        blob = re.sub(r",\s*]", "]", blob)
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            nums = {
                k: float(m.group(1))
                for k in ("inflow", "outflow", "net", "point", "revenue")
                if (m := re.search(rf'"{k}"\s*:\s*(-?[0-9]+(?:\.[0-9]+)?)', blob))
            }
            if not nums:
                raise
            data = {**nums, "reasoning": "truncated_response_partial_parse"}
    if not isinstance(data, dict):
        raise ValueError("model did not return a JSON object")
    return data


async def _ask_openrouter(model: str, system: str, user: str) -> dict[str, Any]:
    key = (_financial_openrouter_key() or _openrouter_key() or "").strip()
    if not key:
        raise RuntimeError("No OpenRouter API key configured")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://zo.agency",
        "X-Title": "zo-agency-weekly-backtest",
    }
    body = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 2048,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    last_err: Exception | None = None
    async with httpx.AsyncClient(timeout=120.0) as client:
        for attempt in range(2):
            response = await client.post(_OPENROUTER_URL, headers=headers, json=body)
            if response.status_code >= 400:
                raise RuntimeError(
                    f"OpenRouter {response.status_code}: {response.text[:300]}"
                )
            content = (
                ((response.json().get("choices") or [{}])[0].get("message") or {}).get(
                    "content"
                )
                or ""
            )
            try:
                return _extract_json(content)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if attempt == 0:
                    continue
    raise RuntimeError(f"parse failed after retry: {last_err}")


# ── series builders ──────────────────────────────────────────────────────────


def build_cash_weeks(realm_id: str, *, since: date) -> list[dict[str, Any]]:
    """Sunday-ending weeks: customer payments in, bill payments + purchases out."""
    buckets: dict[date, dict[str, float]] = defaultdict(
        lambda: {"inflow": 0.0, "outflow": 0.0}
    )
    for row in repo.list_payments(realm_id):
        if row.get("is_deleted"):
            continue
        when = _as_date(row.get("txn_date"))
        if not when or when < since:
            continue
        buckets[_week_ending_sunday(when)]["inflow"] += _money(row.get("total_amt"))
    for lister in (repo.list_bill_payments, repo.list_purchases):
        for row in lister(realm_id):
            if row.get("is_deleted"):
                continue
            when = _as_date(row.get("txn_date"))
            if not when or when < since:
                continue
            buckets[_week_ending_sunday(when)]["outflow"] += _money(row.get("total_amt"))

    weeks: list[dict[str, Any]] = []
    closing = 0.0  # relative running balance; model sees the path, not bank truth
    for ending in sorted(buckets):
        inflow = round(buckets[ending]["inflow"], 2)
        outflow = round(buckets[ending]["outflow"], 2)
        net = round(inflow - outflow, 2)
        closing = round(closing + net, 2)
        weeks.append(
            {
                "ending": ending.isoformat(),
                "inflow": inflow,
                "outflow": outflow,
                "net": net,
                "closing": closing,
            }
        )
    return weeks


def build_revenue_weeks(realm_id: str, *, since: date) -> list[dict[str, Any]]:
    """Sunday-ending weeks of invoiced TotalAmt (booked revenue proxy)."""
    buckets: dict[date, float] = defaultdict(float)
    for row in repo.list_invoices(realm_id):
        if row.get("is_deleted"):
            continue
        when = _as_date(row.get("txn_date"))
        if not when or when < since:
            continue
        buckets[_week_ending_sunday(when)] += _money(row.get("total_amt"))
    return [
        {"ending": ending.isoformat(), "revenue": round(amount, 2)}
        for ending, amount in sorted(buckets.items())
    ]


# ── prompts ──────────────────────────────────────────────────────────────────

_CASH_SYSTEM = (
    "You are a treasury analyst forecasting next-week cash for a marketing agency.\n\n"
    "Inflow = customer payments received that week. Outflow = supplier bill payments "
    "plus purchases recorded that week (ledger outflow often overstates bank burn). "
    "Net = inflow - outflow.\n\n"
    "Forecast the SINGLE next week. Respond with ONLY JSON, no markdown:\n"
    '{"reasoning":"<= 20 words","inflow":<n>,"outflow":<n>,"net":<n>}\n'
    "`net` must equal inflow - outflow."
)

_REVENUE_SYSTEM = (
    "You are an FP&A analyst forecasting next-week booked revenue (invoices raised) "
    "for a marketing agency. Weekly invoicing is lumpy — projects and retainers land "
    "unevenly. Do not assume a smooth weekly average.\n\n"
    "Forecast TOTAL invoiced amount for the single target week. ONLY JSON:\n"
    '{"reasoning":"<= 20 words","point":<n>,"low":<n>,"high":<n>}'
)


def _cash_table(rows: list[dict[str, Any]]) -> str:
    lines = ["week_ending | inflow | outflow | net | closing"]
    for r in rows:
        lines.append(
            f"{r['ending']} | {r['inflow']:.0f} | {r['outflow']:.0f} | "
            f"{r['net']:.0f} | {r['closing']:.0f}"
        )
    return "\n".join(lines)


def _revenue_table(rows: list[dict[str, Any]]) -> str:
    lines = ["week_ending | invoiced"]
    lines.extend(f"{r['ending']} | {r['revenue']:.0f}" for r in rows)
    return "\n".join(lines)


def _cash_prompt(history: list[dict[str, Any]], target: dict[str, Any]) -> str:
    nets = [r["net"] for r in history]
    return (
        f"As of week ending {history[-1]['ending']}, forecast the week ending "
        f"{target['ending']}.\n\n"
        f"Prior {len(history)} weeks:\n{_cash_table(history)}\n\n"
        f"Trail-3 mean net: {mean(nets[-3:]):,.0f}\n"
        f"Median net: {median(nets):,.0f}\n"
        f"Last week net: {nets[-1]:,.0f}\n\n"
        f"Forecast inflow, outflow, and net for week ending {target['ending']}."
    )


def _revenue_prompt(history: list[dict[str, Any]], target: dict[str, Any]) -> str:
    vals = [r["revenue"] for r in history]
    return (
        f"As of week ending {history[-1]['ending']}, forecast invoiced revenue for "
        f"week ending {target['ending']}.\n\n"
        f"Prior {len(history)} weeks:\n{_revenue_table(history)}\n\n"
        f"Trail-3 mean: {mean(vals[-3:]):,.0f}\n"
        f"Median: {median(vals):,.0f}\n"
        f"Last week: {vals[-1]:,.0f}\n\n"
        f"Forecast invoiced total for week ending {target['ending']}."
    )


# ── runners ──────────────────────────────────────────────────────────────────


async def run_cash(*, model: str, weeks: list[dict[str, Any]], year: int) -> dict[str, Any]:
    lookback = _CASH_LOOKBACK
    targets = [
        (i, w)
        for i, w in enumerate(weeks)
        if date.fromisoformat(w["ending"]).year == year
    ]
    results: list[dict[str, Any]] = []
    for idx, target in targets:
        if idx < lookback:
            continue
        history = weeks[idx - lookback : idx]
        print(
            f"cash window={history[0]['ending']} … {history[-1]['ending']} "
            f"→ {target['ending']} …",
            flush=True,
        )
        try:
            pred = await _ask_openrouter(
                model, _CASH_SYSTEM, _cash_prompt(history, target)
            )
            fin = float(pred["inflow"])
            fout = float(pred.get("outflow", fin - float(pred.get("net", 0))))
            fnet = float(pred["net"]) if "net" in pred else fin - fout
            ain, aout, anet = target["inflow"], target["outflow"], target["net"]
            hist_nets = [h["net"] for h in history]
            row = {
                "ending": target["ending"],
                "window_start": history[0]["ending"],
                "window_end": history[-1]["ending"],
                "actual_inflow": ain,
                "actual_outflow": aout,
                "actual_net": anet,
                "forecast_inflow": round(fin, 2),
                "forecast_outflow": round(fout, 2),
                "forecast_net": round(fnet, 2),
                "ape_inflow": _round_ape(_ape(ain, fin)),
                "ape_outflow": _round_ape(_ape(aout, fout)),
                "ape_net": _round_ape(_ape(anet, fnet)),
                "accuracy_inflow": _acc(_ape(ain, fin)),
                "accuracy_outflow": _acc(_ape(aout, fout)),
                "accuracy_net": _acc(_ape(anet, fnet)),
                "baselines_net": {
                    "last_week": _round_ape(_ape(anet, hist_nets[-1])),
                    "trail3_mean": _round_ape(_ape(anet, mean(hist_nets[-3:]))),
                },
                "reasoning": pred.get("reasoning"),
                "status": "ok",
            }
        except Exception as exc:  # noqa: BLE001
            row = {
                "ending": target["ending"],
                "status": "failed",
                "error": str(exc)[:240],
            }
        results.append(row)
        _print_cash_row(row)
    return _summarize_cash(model, year, lookback, results)


async def run_revenue(
    *, model: str, weeks: list[dict[str, Any]], year: int
) -> dict[str, Any]:
    lookback = _REVENUE_LOOKBACK
    targets = [
        (i, w)
        for i, w in enumerate(weeks)
        if date.fromisoformat(w["ending"]).year == year
    ]
    results: list[dict[str, Any]] = []
    for idx, target in targets:
        if idx < lookback:
            continue
        history = weeks[idx - lookback : idx]
        print(
            f"rev window={history[0]['ending']} … {history[-1]['ending']} "
            f"→ {target['ending']} …",
            flush=True,
        )
        try:
            pred = await _ask_openrouter(
                model, _REVENUE_SYSTEM, _revenue_prompt(history, target)
            )
            point = float(pred["point"])
            actual = target["revenue"]
            err = _ape(actual, point)
            hist = [h["revenue"] for h in history]
            row = {
                "ending": target["ending"],
                "window_start": history[0]["ending"],
                "window_end": history[-1]["ending"],
                "actual": actual,
                "forecast": round(point, 2),
                "ape_pct": _round_ape(err),
                "accuracy_pct": _acc(err),
                "baselines": {
                    "last_week": _round_ape(_ape(actual, hist[-1])),
                    "trail3_mean": _round_ape(_ape(actual, mean(hist[-3:]))),
                },
                "reasoning": pred.get("reasoning"),
                "status": "ok",
            }
        except Exception as exc:  # noqa: BLE001
            row = {
                "ending": target["ending"],
                "status": "failed",
                "error": str(exc)[:240],
            }
        results.append(row)
        _print_rev_row(row)
    return _summarize_revenue(model, year, lookback, results)


def _round_ape(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _acc(ape: float | None) -> float | None:
    return None if ape is None else round(max(0.0, 100.0 - ape), 2)


def _print_cash_row(row: dict[str, Any]) -> None:
    if row["status"] != "ok":
        print(f"  FAILED {row.get('error')}", flush=True)
        return
    print(
        f"  net actual={row['actual_net']:,.0f} forecast={row['forecast_net']:,.0f} "
        f"ape_net={row['ape_net']} acc_net={row['accuracy_net']} | "
        f"in ape={row['ape_inflow']} out ape={row['ape_outflow']}",
        flush=True,
    )


def _print_rev_row(row: dict[str, Any]) -> None:
    if row["status"] != "ok":
        print(f"  FAILED {row.get('error')}", flush=True)
        return
    print(
        f"  actual={row['actual']:,.0f} forecast={row['forecast']:,.0f} "
        f"ape={row['ape_pct']} accuracy={row['accuracy_pct']}",
        flush=True,
    )


def _mean_ok(rows: list[dict[str, Any]], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get("status") == "ok" and r.get(key) is not None]
    return round(mean(vals), 2) if vals else None


def _summarize_cash(
    model: str, year: int, lookback: int, results: list[dict[str, Any]]
) -> dict[str, Any]:
    ok = [r for r in results if r.get("status") == "ok"]
    return {
        "mode": "cash",
        "model": model,
        "year": year,
        "lookback_weeks": lookback,
        "origins": len(results),
        "scored": len(ok),
        "mape_net": _mean_ok(ok, "ape_net"),
        "mape_inflow": _mean_ok(ok, "ape_inflow"),
        "mape_outflow": _mean_ok(ok, "ape_outflow"),
        "mean_accuracy_net": _mean_ok(ok, "accuracy_net"),
        "mean_accuracy_inflow": _mean_ok(ok, "accuracy_inflow"),
        "mape_baseline_last_week_net": _mean_ok(
            [
                {**r, "ape_net": (r.get("baselines_net") or {}).get("last_week")}
                for r in ok
            ],
            "ape_net",
        ),
        "mape_baseline_trail3_net": _mean_ok(
            [
                {**r, "ape_net": (r.get("baselines_net") or {}).get("trail3_mean")}
                for r in ok
            ],
            "ape_net",
        ),
        "results": results,
    }


def _summarize_revenue(
    model: str, year: int, lookback: int, results: list[dict[str, Any]]
) -> dict[str, Any]:
    ok = [r for r in results if r.get("status") == "ok"]
    return {
        "mode": "revenue",
        "model": model,
        "year": year,
        "lookback_weeks": lookback,
        "origins": len(results),
        "scored": len(ok),
        "mape_llm": _mean_ok(ok, "ape_pct"),
        "mean_accuracy_pct": _mean_ok(ok, "accuracy_pct"),
        "mape_last_week": _mean_ok(
            [{**r, "ape_pct": (r.get("baselines") or {}).get("last_week")} for r in ok],
            "ape_pct",
        ),
        "mape_trail3_mean": _mean_ok(
            [
                {**r, "ape_pct": (r.get("baselines") or {}).get("trail3_mean")}
                for r in ok
            ],
            "ape_pct",
        ),
        "results": results,
    }


def main() -> int:
    mode: Mode = (os.environ.get("MODE") or "cash").strip().lower()  # type: ignore[assignment]
    if mode not in ("cash", "revenue"):
        raise SystemExit("MODE must be cash or revenue")
    year = int((os.environ.get("YEAR") or "2025").strip())
    model = (os.environ.get("MODEL") or "").strip() or (
        getattr(settings, "openrouter_model_forecast", "") or ""
    ).strip() or _DEFAULT_MODEL
    realm = settings.quickbooks_realm_id
    since = date(year - 1, 1, 1)

    print(f"mode={mode} year={year} model={model} realm={realm}")
    if mode == "cash":
        weeks = build_cash_weeks(realm, since=since)
        print(f"cash weeks loaded={len(weeks)} lookback={_CASH_LOOKBACK}")
        summary = asyncio.run(run_cash(model=model, weeks=weeks, year=year))
    else:
        weeks = build_revenue_weeks(realm, since=since)
        print(f"revenue weeks loaded={len(weeks)} lookback={_REVENUE_LOOKBACK}")
        summary = asyncio.run(run_revenue(model=model, weeks=weeks, year=year))

    out = Path(f"/tmp/weekly_{mode}_backtest_{year}.json")
    out.write_text(json.dumps(summary, indent=2))

    print("\n=== WEEK-WISE ===")
    for r in summary["results"]:
        if r.get("status") != "ok":
            print(f"{r.get('ending')}: FAILED — {r.get('error')}")
            continue
        if mode == "cash":
            print(
                f"{r['ending']}: net act={r['actual_net']:,.0f} fc={r['forecast_net']:,.0f} "
                f"err={r['ape_net']}% acc={r['accuracy_net']}% | "
                f"in err={r['ape_inflow']}% out err={r['ape_outflow']}%"
            )
        else:
            print(
                f"{r['ending']}: act=${r['actual']:,.0f} fc=${r['forecast']:,.0f} "
                f"err={r['ape_pct']}% acc={r['accuracy_pct']}%"
            )
    print("\n=== SUMMARY ===")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
