"""Monthly revenue forecasts: past-year scorecards + current-year recommendations.

Weekly cash stays in `qb_forecast_llm`. This module only forecasts next-month
TOTAL INCOME with a fixed 10 closed-month lookback (the window that matched the
2024/2025 backtests). Stored under `quickbooks_forecast_monthly` so it never
collides with the weekly payload.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import re
from datetime import date, datetime
from statistics import mean
from typing import Any

from app.financial import qb_repository as repo
from app.financial import quickbooks as qbo
from app.financial.ai_insights_repository import get_latest_insight, upsert_insight
from app.financial.qb_panels_from_db import monthly_trend as monthly_trend_from_db
from app.services.llm import chat_json_soft, resolve_llm_model

logger = logging.getLogger(__name__)

SOURCE = "quickbooks_forecast_monthly"
MONTH_NODE = "qb_forecast_month"
LOOKBACK = 10

_MONTH_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})$"
)
_MONTH_NUM = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}
_MONTH_LABEL = {v: k for k, v in _MONTH_NUM.items()}

_SYSTEM = (
    "You are a senior FP&A analyst forecasting next-month revenue for a "
    "marketing agency.\n\n"
    "The agency sells retainers and project work. Monthly income swings hard — "
    "median month-to-month change is large, and there is no stable seasonality. "
    "Do not assume last year's shape repeats.\n\n"
    "Forecast TOTAL INCOME for the single target month. Respond with ONLY a "
    "JSON object, no markdown fence:\n"
    '{"reasoning":"<= 20 words","point":<n>,"low":<n>,"high":<n>,'
    '"confidence":"low|medium|high"}\n'
    "`point` is your best estimate for that month's total income. `low` and "
    "`high` bound an 80% interval."
)


def scope_key(realm_id: str, year: int) -> str:
    return f"{realm_id}:{year}"


def _parse_month(label: str) -> tuple[int, int]:
    m = _MONTH_RE.match((label or "").strip())
    if not m:
        raise ValueError(f"bad month label: {label!r}")
    return int(m.group(2)), _MONTH_NUM[m.group(1)]


def _label(year: int, month: int) -> str:
    return f"{_MONTH_LABEL[month]} {year}"


def _ym(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _as_of_for_target(year: int, month: int) -> str:
    """Last day of the month before the target — the closed-books origin."""
    if month == 1:
        return date(year - 1, 12, 31).isoformat()
    last = calendar.monthrange(year, month - 1)[1]
    return date(year, month - 1, last).isoformat()


def _normalize_trend(trend: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in (trend or {}).get("months") or []:
        label = str(row.get("month") or "").strip()
        if not label:
            continue
        year, month = _parse_month(label)
        out.append(
            {
                "label": label,
                "year": year,
                "month": month,
                "income": float(row.get("amount") or 0),
            }
        )
    out.sort(key=lambda r: (r["year"], r["month"]))
    return out


def load_income_series(realm_id: str, target_year: int) -> list[dict[str, Any]]:
    """Prior year + target year months. Prefer panel cache; fall back to live QBO."""
    rows: list[dict[str, Any]] = []
    for year in (target_year - 1, target_year):
        cached = repo.get_panel_cache(realm_id, year) or {}
        trend = (cached.get("payload") or {}).get("monthly_trend")
        if not trend or not (trend.get("months") or []):
            try:
                trend = monthly_trend_from_db(realm_id, year)
            except LookupError:
                logger.info(
                    "operation=monthly_forecast_series year=%s status=live_qbo",
                    year,
                )
                trend = qbo.monthly_trend(year)
        rows.extend(_normalize_trend(trend))
    by_key = {(r["year"], r["month"]): r for r in rows}
    # Guarantee a slot for every calendar month in the target year so open
    # months still have a lookback index even before income posts.
    for month in range(1, 13):
        key = (target_year, month)
        if key not in by_key:
            by_key[key] = {
                "label": _label(target_year, month),
                "year": target_year,
                "month": month,
                "income": 0.0,
            }
    return [by_key[k] for k in sorted(by_key)]


def history_window(
    series: list[dict[str, Any]], target_index: int
) -> list[dict[str, Any]]:
    start = target_index - LOOKBACK
    if start < 0:
        raise ValueError(
            f"need {LOOKBACK} prior months before {series[target_index]['label']}, "
            f"only have {target_index}"
        )
    return series[start:target_index]


def trail3_mean(history: list[dict[str, Any]]) -> float:
    incomes = [h["income"] for h in history]
    window = incomes[-3:] if len(incomes) >= 3 else incomes
    return round(mean(window), 2) if window else 0.0


def _month_table(rows: list[dict[str, Any]]) -> str:
    lines = ["month | income"]
    lines.extend(f"{r['label']} | {r['income']:.0f}" for r in rows)
    return "\n".join(lines)


def _user_prompt(history: list[dict[str, Any]], target_label: str) -> str:
    incomes = [r["income"] for r in history]
    return (
        f"As of the last closed month ({history[-1]['label']}), forecast "
        f"TOTAL INCOME for {target_label} only.\n\n"
        f"Prior {LOOKBACK} closed months (oldest first):\n"
        f"{_month_table(history)}\n\n"
        f"Trailing mean (last 3 closed): {mean(incomes[-3:]):,.0f}\n"
        f"Median of the {LOOKBACK} months shown: "
        f"{sorted(incomes)[len(incomes) // 2]:,.0f}\n"
        f"Last closed month: {incomes[-1]:,.0f}\n\n"
        f"Forecast income for {target_label}."
    )


async def forecast_one_month(
    history: list[dict[str, Any]], target_label: str
) -> dict[str, Any]:
    if len(history) != LOOKBACK:
        raise ValueError(f"history must be {LOOKBACK} months, got {len(history)}")
    payload, provider = await chat_json_soft(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _user_prompt(history, target_label)},
        ],
        max_tokens=1024,
        temperature=0.2,
        tier="light",
        node_name=MONTH_NODE,
    )
    if not payload or payload.get("point") is None:
        raise RuntimeError(f"empty monthly forecast for {target_label}")
    return {
        "point": float(payload["point"]),
        "low": float(payload["low"]) if payload.get("low") is not None else None,
        "high": float(payload["high"]) if payload.get("high") is not None else None,
        "confidence": payload.get("confidence") or "low",
        "reasoning": payload.get("reasoning"),
        "provider": provider,
        "baseline_trail3": trail3_mean(history),
    }


def _error_pct(actual: float | None, forecast: float | None) -> float | None:
    if actual is None or forecast is None or actual == 0:
        return None
    return round(abs(forecast - actual) / abs(actual) * 100.0, 2)


def _row_from_prediction(
    *,
    year: int,
    month: int,
    pred: dict[str, Any],
    actual: float | None,
) -> dict[str, Any]:
    forecast = round(pred["point"], 2)
    return {
        "month": _ym(year, month),
        "label": _label(year, month),
        "forecast": forecast,
        "low": round(pred["low"], 2) if pred.get("low") is not None else None,
        "high": round(pred["high"], 2) if pred.get("high") is not None else None,
        "actual": round(actual, 2) if actual is not None else None,
        "error_pct": _error_pct(actual, forecast),
        "as_of": _as_of_for_target(year, month),
        "method": "llm",
        "baseline_trail3": pred.get("baseline_trail3"),
        "confidence": pred.get("confidence") or "low",
        "reasoning": pred.get("reasoning"),
    }


def _index_for(series: list[dict[str, Any]], year: int, month: int) -> int:
    label = _label(year, month)
    for i, row in enumerate(series):
        if row["label"] == label:
            return i
    raise KeyError(f"month {label} missing from income series")


def _actual_for(series: list[dict[str, Any]], year: int, month: int) -> float | None:
    try:
        row = series[_index_for(series, year, month)]
    except KeyError:
        return None
    income = float(row["income"])
    return income if income > 0 else None


def _existing_months(realm_id: str, year: int) -> dict[str, dict[str, Any]]:
    row = get_latest_insight(SOURCE, scope_key(realm_id, year))
    payload = (row or {}).get("payload") or {}
    return {
        str(m["month"]): m
        for m in (payload.get("months") or [])
        if isinstance(m, dict) and m.get("month")
    }


def _store(realm_id: str, year: int, months: list[dict[str, Any]]) -> None:
    model = resolve_llm_model("light", node_name=MONTH_NODE)
    scored = [m for m in months if m.get("error_pct") is not None]
    mape = round(mean(m["error_pct"] for m in scored), 2) if scored else None
    upsert_insight(
        source=SOURCE,
        scope_key=scope_key(realm_id, year),
        as_of=date.today().isoformat(),
        payload={
            "year": year,
            "months": months,
            "mape": mape,
            "scored_months": len(scored),
            "lookback_months": LOOKBACK,
            "generated_at": datetime.now().isoformat(),
        },
        evidence={"year": year, "months": len(months)},
        provider="openrouter",
        model=model,
        status="ok" if months else "failed",
    )


async def _predict_month(
    series: list[dict[str, Any]], year: int, month: int
) -> dict[str, Any]:
    idx = _index_for(series, year, month)
    history = history_window(series, idx)
    pred = await forecast_one_month(history, _label(year, month))
    actual = _actual_for(series, year, month)
    return _row_from_prediction(year=year, month=month, pred=pred, actual=actual)


async def backfill_year(realm_id: str, year: int) -> dict[str, Any]:
    """Forecast every month of `year` with a 10-month lookback; attach actuals."""
    series = load_income_series(realm_id, year)
    months: list[dict[str, Any]] = []
    errors = 0
    for month in range(1, 13):
        try:
            row = await _predict_month(series, year, month)
            months.append(row)
            logger.info(
                "operation=monthly_forecast_backfill realm_id=%s month=%s "
                "forecast=%s actual=%s error_pct=%s",
                realm_id,
                row["month"],
                row["forecast"],
                row["actual"],
                row["error_pct"],
            )
        except Exception as exc:  # noqa: BLE001 — one month must not kill the year
            errors += 1
            logger.warning(
                "operation=monthly_forecast_backfill realm_id=%s year=%s "
                "month=%s status=failed error=%s",
                realm_id,
                year,
                month,
                str(exc)[:200],
            )
    months.sort(key=lambda m: m["month"])
    _store(realm_id, year, months)
    return {
        "status": "ok" if months else "failed",
        "year": year,
        "months": len(months),
        "errors": errors,
        "mape": (get_latest_insight(SOURCE, scope_key(realm_id, year)) or {})
        .get("payload", {})
        .get("mape"),
    }


async def refresh_current_year(
    realm_id: str, year: int, as_of: date | str
) -> dict[str, Any]:
    """Lock closed months; reforecast open months (month > as_of's month)."""
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of[:10])
    series = load_income_series(realm_id, year)
    existing = _existing_months(realm_id, year)
    months: list[dict[str, Any]] = []
    refreshed = 0
    locked = 0

    for month in range(1, 13):
        key = _ym(year, month)
        target = date(year, month, 1)
        # Fully prior months are locked; current + future months are refreshed.
        closed = (year, month) < (as_of.year, as_of.month)
        actual = _actual_for(series, year, month)

        if closed:
            prev = existing.get(key)
            if prev and prev.get("forecast") is not None:
                row = {
                    **prev,
                    "actual": round(actual, 2) if actual is not None else prev.get("actual"),
                }
                row["error_pct"] = _error_pct(row.get("actual"), row.get("forecast"))
                months.append(row)
                locked += 1
                continue
            # Missing historical forecast for a closed month — generate once.
            try:
                months.append(await _predict_month(series, year, month))
                refreshed += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "operation=monthly_forecast_refresh realm_id=%s month=%s "
                    "status=failed error=%s",
                    realm_id,
                    key,
                    str(exc)[:200],
                )
            continue

        # Open / future month relative to as_of — always reforecast.
        try:
            months.append(await _predict_month(series, year, month))
            refreshed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "operation=monthly_forecast_refresh realm_id=%s month=%s "
                "status=failed error=%s",
                realm_id,
                key,
                str(exc)[:200],
            )
            if key in existing:
                months.append(existing[key])

    months.sort(key=lambda m: m["month"])
    if months:
        _store(realm_id, year, months)
    logger.info(
        "operation=monthly_forecast_refresh realm_id=%s year=%s as_of=%s "
        "locked=%s refreshed=%s total=%s",
        realm_id,
        year,
        as_of.isoformat(),
        locked,
        refreshed,
        len(months),
    )
    return {
        "status": "ok" if months else "empty",
        "year": year,
        "locked": locked,
        "refreshed": refreshed,
        "months": len(months),
    }


def generate_and_store_current(realm_id: str, year: int, as_of: str) -> str:
    """Sync-worker entry: never raises."""
    try:
        result = asyncio.run(refresh_current_year(realm_id, year, as_of))
        return str(result.get("status") or "empty")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "operation=monthly_forecast_generate realm_id=%s year=%s "
            "status=failed error=%s",
            realm_id,
            year,
            str(exc)[:200],
        )
        return "failed"


def run_backfill(realm_id: str, year: int) -> dict[str, Any]:
    """Sync/cron entry for a full past year."""
    try:
        return asyncio.run(backfill_year(realm_id, year))
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "operation=monthly_forecast_backfill realm_id=%s year=%s status=failed",
            realm_id,
            year,
        )
        return {"status": "failed", "year": year, "error": str(exc)[:200]}
