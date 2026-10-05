"""Match HubSpot Closed Won deals to QuickBooks invoices (drop once booked)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from app.financial.client_map_normalize import normalize_name

logger = logging.getLogger(__name__)

WON_MATCH_AMOUNT_RATIO = 0.80
WON_MATCH_DATE_SLACK_DAYS = 14


@dataclass
class MatchResult:
    matched_ids: set[int] = field(default_factory=set)
    unmatched: list[dict[str, Any]] = field(default_factory=list)


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _money(value: Any) -> float:
    try:
        return float(value) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _invoice_total(inv: dict[str, Any]) -> float:
    if inv.get("total_amt") is not None:
        return _money(inv.get("total_amt"))
    if inv.get("total") is not None:
        return _money(inv.get("total"))
    if inv.get("amount") is not None:
        return _money(inv.get("amount"))
    return 0.0


def match_won_deals(
    deals: list[dict[str, Any]],
    *,
    companies: dict[int, dict[str, Any]],
    invoices: list[dict[str, Any]],
    as_of: date,
) -> MatchResult:
    """Return matched Closed Won hs_ids and unmatched won deal snippets."""
    won = [d for d in deals if d.get("hs_is_closed_won") and not d.get("archived")]
    by_name: dict[str, list[dict[str, Any]]] = {}
    for inv in invoices:
        if inv.get("is_deleted"):
            continue
        name = normalize_name(str(inv.get("customer_name") or ""))
        if not name:
            continue
        by_name.setdefault(name, []).append(inv)

    result = MatchResult()
    for deal in won:
        hs_id = int(deal["hs_id"])
        amount = _money(deal.get("amount"))
        close = _as_date(deal.get("closedate")) or as_of
        window_start = close - timedelta(days=WON_MATCH_DATE_SLACK_DAYS)

        company_id = deal.get("company_hs_id")
        company = companies.get(int(company_id)) if company_id else None
        candidates: list[dict[str, Any]] = []
        if company:
            cname = normalize_name(str(company.get("name") or ""))
            if cname:
                candidates.extend(by_name.get(cname) or [])

        if not company:
            result.unmatched.append({
                "hs_id": hs_id,
                "dealname": deal.get("dealname"),
                "amount": amount,
            })
            continue

        covered = 0.0
        for inv in candidates:
            txn = _as_date(inv.get("txn_date"))
            if txn and txn < window_start:
                continue
            covered += _invoice_total(inv)

        if amount > 0 and covered >= amount * WON_MATCH_AMOUNT_RATIO:
            result.matched_ids.add(hs_id)
            logger.info(
                "operation=hs_won_match hs_id=%s status=matched covered=%.0f amount=%.0f",
                hs_id, covered, amount,
            )
        else:
            result.unmatched.append({
                "hs_id": hs_id,
                "dealname": deal.get("dealname"),
                "amount": amount,
            })
            logger.info(
                "operation=hs_won_match hs_id=%s status=unmatched covered=%.0f amount=%.0f",
                hs_id, covered, amount,
            )
    return result
