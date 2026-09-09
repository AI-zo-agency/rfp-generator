"""One-off: backtest next-month revenue with Gemini Flash.

Each origin gets exactly the prior 10 closed months. Example: forecasting
Feb 2024 sees Apr 2023 … Jan 2024. Prior years are pulled live from QuickBooks
because the panel cache only backfills from 2024-01-01.

Usage:

    cd backend && python scripts/backtest_monthly_revenue_2024.py
    cd backend && YEAR=2025 python scripts/backtest_monthly_revenue_2024.py

Optional: MODEL=google/gemini-2.5-flash YEAR=2025 python scripts/…
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from statistics import mean, median
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.financial import quickbooks as qbo  # noqa: E402
from app.services.llm import _financial_openrouter_key, _openrouter_key  # noqa: E402

_DEFAULT_MODEL = "google/gemini-3.6-flash"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_LOOKBACK = 10
_MONTH_RE = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})$")
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


def _parse_month(label: str) -> tuple[int, int]:
    m = _MONTH_RE.match((label or "").strip())
    if not m:
        raise ValueError(f"bad month label: {label!r}")
    return int(m.group(2)), _MONTH_NUM[m.group(1)]


def _normalize_trend(trend: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in trend.get("months") or []:
        amount = float(row.get("amount") or 0)
        # Keep zero months in the timeline so the lookback index stays aligned;
        # drop them only when scoring targets.
        label = str(row.get("month") or "").strip()
        if not label:
            continue
        year, month = _parse_month(label)
        out.append(
            {
                "label": label,
                "year": year,
                "month": month,
                "income": amount,
            }
        )
    out.sort(key=lambda r: (r["year"], r["month"]))
    return out


def _load_series(target_year: int) -> list[dict[str, Any]]:
    """Live QBO months for target_year and the prior year (10-month lookback)."""
    years = (target_year - 1, target_year)
    print(f"loading ProfitAndLoss months from QuickBooks {years}…", flush=True)
    rows: list[dict[str, Any]] = []
    for year in years:
        rows.extend(_normalize_trend(qbo.monthly_trend(year)))
    for year in years:
        print(
            f"  {year}: {sum(1 for r in rows if r['year'] == year)} months",
            flush=True,
        )
    return rows


def _ape(actual: float, forecast: float) -> float:
    return abs(forecast - actual) / actual * 100.0 if actual else float("nan")


def _baselines(history: list[float], actual: float) -> dict[str, float]:
    last = history[-1]
    trail3 = mean(history[-3:]) if len(history) >= 3 else mean(history)
    med = median(history)
    return {
        "last_month": _ape(actual, last),
        "trail3_mean": _ape(actual, trail3),
        "median_history": _ape(actual, med),
    }


def _month_table(rows: list[dict[str, Any]]) -> str:
    lines = ["month | income"]
    lines.extend(f"{r['label']} | {r['income']:.0f}" for r in rows)
    return "\n".join(lines)


def _user_prompt(history: list[dict[str, Any]], target: dict[str, Any]) -> str:
    incomes = [r["income"] for r in history]
    return (
        f"As of the last closed month ({history[-1]['label']}), forecast "
        f"TOTAL INCOME for {target['label']} only.\n\n"
        f"Prior {_LOOKBACK} closed months (oldest first):\n"
        f"{_month_table(history)}\n\n"
        f"Trailing mean (last 3 closed): "
        f"{mean(incomes[-3:]) if len(incomes) >= 3 else mean(incomes):,.0f}\n"
        f"Median of the {_LOOKBACK} months shown: {median(incomes):,.0f}\n"
        f"Last closed month: {incomes[-1]:,.0f}\n\n"
        f"Forecast income for {target['label']}."
    )


def _extract_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Smart quotes / truncated reasoning strings are common with flash models.
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
        # If reasoning was cut mid-string, keep numeric fields via a loose pull.
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            point = re.search(r'"point"\s*:\s*([0-9]+(?:\.[0-9]+)?)', blob)
            if not point:
                raise
            low = re.search(r'"low"\s*:\s*([0-9]+(?:\.[0-9]+)?)', blob)
            high = re.search(r'"high"\s*:\s*([0-9]+(?:\.[0-9]+)?)', blob)
            conf = re.search(r'"confidence"\s*:\s*"(low|medium|high)"', blob)
            data = {
                "point": float(point.group(1)),
                "low": float(low.group(1)) if low else None,
                "high": float(high.group(1)) if high else None,
                "confidence": conf.group(1) if conf else None,
                "reasoning": "truncated_response_partial_parse",
            }
    if not isinstance(data, dict):
        raise ValueError("model did not return a JSON object")
    return data


async def _ask_openrouter(model: str, user: str) -> dict[str, Any]:
    key = (_financial_openrouter_key() or _openrouter_key() or "").strip()
    if not key:
        raise RuntimeError("No OpenRouter API key configured")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://zo.agency",
        "X-Title": "zo-agency-monthly-revenue-backtest",
    }
    body = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 2048,
        "messages": [
            {"role": "system", "content": _SYSTEM},
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
            payload = response.json()
            content = (
                ((payload.get("choices") or [{}])[0].get("message") or {}).get(
                    "content"
                )
                or ""
            )
            try:
                return _extract_json(content)
            except Exception as exc:  # noqa: BLE001 — retry once on parse fail
                last_err = exc
                if attempt == 0:
                    continue
    raise RuntimeError(f"parse failed after retry: {last_err}")


def _history_window(
    series: list[dict[str, Any]], target_index: int
) -> list[dict[str, Any]]:
    start = target_index - _LOOKBACK
    if start < 0:
        raise ValueError(
            f"need {_LOOKBACK} prior months before {series[target_index]['label']}, "
            f"only have {target_index}"
        )
    return series[start:target_index]


async def run(
    *, model: str, series: list[dict[str, Any]], target_year: int
) -> dict[str, Any]:
    targets = [
        (i, row)
        for i, row in enumerate(series)
        if row["year"] == target_year and row["income"] > 0
    ]
    if not targets:
        raise RuntimeError(f"no booked {target_year} months to score")

    results: list[dict[str, Any]] = []
    for idx, target in targets:
        history = _history_window(series, idx)
        assert len(history) == _LOOKBACK
        prompt = _user_prompt(history, target)
        window = f"{history[0]['label']} … {history[-1]['label']}"
        print(
            f"window={window} → forecast {target['label']} …",
            flush=True,
        )
        try:
            pred = await _ask_openrouter(model, prompt)
            point = float(pred["point"])
            err = _ape(target["income"], point)
            hist_vals = [h["income"] for h in history]
            row = {
                "window_start": history[0]["label"],
                "window_end": history[-1]["label"],
                "lookback_months": _LOOKBACK,
                "target": target["label"],
                "actual": round(target["income"], 2),
                "forecast": round(point, 2),
                "ape_pct": round(err, 2),
                "accuracy_pct": round(100.0 - err, 2),
                "low": pred.get("low"),
                "high": pred.get("high"),
                "confidence": pred.get("confidence"),
                "reasoning": pred.get("reasoning"),
                "baselines": {
                    k: round(v, 2)
                    for k, v in _baselines(hist_vals, target["income"]).items()
                },
                "status": "ok",
            }
        except Exception as exc:  # noqa: BLE001 — one bad origin must not kill the run
            row = {
                "window_start": history[0]["label"],
                "window_end": history[-1]["label"],
                "lookback_months": _LOOKBACK,
                "target": target["label"],
                "actual": round(target["income"], 2),
                "status": "failed",
                "error": str(exc)[:240],
            }
        results.append(row)
        if row["status"] == "ok":
            print(
                f"  actual={row['actual']:,.0f} forecast={row['forecast']:,.0f} "
                f"ape={row['ape_pct']:.1f}% accuracy={row['accuracy_pct']:.1f}%",
                flush=True,
            )
        else:
            print(f"  FAILED {row['error']}", flush=True)

    ok = [r for r in results if r.get("status") == "ok"]
    return {
        "model": model,
        "year": target_year,
        "lookback_months": _LOOKBACK,
        "origins": len(results),
        "scored": len(ok),
        "mape_llm": round(mean(r["ape_pct"] for r in ok), 2) if ok else None,
        "mean_accuracy_pct": (
            round(mean(r["accuracy_pct"] for r in ok), 2) if ok else None
        ),
        "mape_last_month": (
            round(mean(r["baselines"]["last_month"] for r in ok), 2) if ok else None
        ),
        "mape_trail3_mean": (
            round(mean(r["baselines"]["trail3_mean"] for r in ok), 2) if ok else None
        ),
        "mape_median_history": (
            round(mean(r["baselines"]["median_history"] for r in ok), 2) if ok else None
        ),
        "prior_claimed_best": 19.8,
        "results": results,
    }


def main() -> int:
    model = (os.environ.get("MODEL") or "").strip() or (
        getattr(settings, "openrouter_model_forecast", "") or ""
    ).strip() or _DEFAULT_MODEL
    target_year = int((os.environ.get("YEAR") or "2024").strip())

    series = _load_series(target_year)
    print(f"model={model} year={target_year} lookback={_LOOKBACK}")
    feb_label = f"Feb {target_year}"
    feb_i = next(i for i, r in enumerate(series) if r["label"] == feb_label)
    feb_window = _history_window(series, feb_i)
    print(
        f"check {feb_label} window: {feb_window[0]['label']} … {feb_window[-1]['label']}",
        flush=True,
    )
    summary = asyncio.run(run(model=model, series=series, target_year=target_year))

    out = Path(f"/tmp/monthly_revenue_backtest_{target_year}.json")
    out.write_text(json.dumps(summary, indent=2))
    print("\n=== MONTH-WISE ===")
    for r in summary["results"]:
        if r.get("status") != "ok":
            print(f"{r['target']}: FAILED — {r.get('error')}")
            continue
        print(
            f"{r['target']}: actual=${r['actual']:,.0f}  forecast=${r['forecast']:,.0f}  "
            f"error={r['ape_pct']:.1f}%  accuracy={r['accuracy_pct']:.1f}%  "
            f"({r['window_start']} … {r['window_end']})"
        )
    print("\n=== SUMMARY ===")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
