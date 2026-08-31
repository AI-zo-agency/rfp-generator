"""Deterministic Agency weekly insight cards."""

from __future__ import annotations

from typing import Any

from datetime import date

from app.financial.agency_week import iso, period_label


def _usd(value: float) -> str:
    return f"${value:,.0f}"


def _signal(
    signal_id: str,
    severity: str,
    headline: str,
    figure: str,
    detail: str,
    go_to: str,
) -> dict[str, str]:
    return {
        "id": signal_id,
        "severity": severity,
        "headline": headline,
        "figure": figure,
        "detail": detail,
        "go_to": go_to,
    }


def build_signals(
    *,
    overview: dict[str, Any],
    open_items: list[dict[str, Any]],
    carryover: list[dict[str, Any]],
    resolved: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    kpis: dict[str, Any],
    prior_kpis: dict[str, Any] | None,
    brief_week_start: str,
    brief_week_end: str,
    has_prior_snapshot: bool = False,
) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    period = period_label(
        date.fromisoformat(brief_week_start),
        date.fromisoformat(brief_week_end),
    )

    carryover_amount = sum(float(row.get("amount") or 0) for row in carryover)
    if has_prior_snapshot and carryover:
        top = sorted(carryover, key=lambda r: (int(r.get("weeks_open") or 1), float(r.get("amount") or 0)), reverse=True)[:3]
        detail_parts = [
            f"{row.get('title')} · open {row.get('weeks_open')} wk{'s' if int(row.get('weeks_open') or 1) != 1 else ''}"
            for row in top
        ]
        signals.append(
            _signal(
                "carryover:week",
                "critical" if any(int(r.get("weeks_open") or 0) >= 4 for r in carryover) else "warn",
                f"{len(carryover)} items carried over from {period}",
                _usd(carryover_amount) if carryover_amount else str(len(carryover)),
                " · ".join(detail_parts),
                "jobs",
            )
        )

    aging = [row for row in open_items if int(row.get("weeks_open") or 0) >= 3]
    if has_prior_snapshot and aging:
        oldest = max(aging, key=lambda r: int(r.get("weeks_open") or 0))
        signals.append(
            _signal(
                "aging:queue",
                "critical" if int(oldest.get("weeks_open") or 0) >= 4 else "warn",
                f"{len(aging)} items open 3+ weeks",
                str(int(oldest.get("weeks_open") or 0)),
                f"Longest: {oldest.get('title')}",
                "jobs",
            )
        )

    if has_prior_snapshot and resolved:
        examples = ", ".join(str(row.get("title") or row.get("id")) for row in resolved[:3])
        signals.append(
            _signal(
                "resolved:week",
                "info",
                f"{len(resolved)} items cleared since last week",
                str(len(resolved)),
                examples,
                "jobs",
            )
        )

    if has_prior_snapshot and new_items:
        examples = ", ".join(str(row.get("title") or row.get("id")) for row in new_items[:3])
        signals.append(
            _signal(
                "new:week",
                "warn",
                f"{len(new_items)} new items this week",
                str(len(new_items)),
                examples,
                "jobs",
            )
        )

    if not has_prior_snapshot:
        queue_count = int(kpis.get("queue_count") or 0)
        if queue_count:
            top = [
                row for row in open_items if row.get("kind") in {"delivery", "mapping", "receivable"}
            ][:3]
            examples = ", ".join(str(row.get("title") or row.get("id")) for row in top)
            signals.append(
                _signal(
                    "queue:baseline",
                    "info",
                    f"{queue_count} items on the owner queue",
                    str(queue_count),
                    examples or "First weekly snapshot pending — carryover starts after Friday.",
                    "jobs",
                )
            )

    unlinked = int(kpis.get("unlinked_invoice_count") or 0)
    if unlinked:
        signals.append(
            _signal(
                "invoices:unlinked",
                "warn" if unlinked < 10 else "critical",
                f"{unlinked} unlinked invoices",
                str(unlinked),
                "QuickBooks invoices without a confirmed Agency job link",
                "invoices",
            )
        )

    orphan_count = int(kpis.get("orphan_count") or 0)
    orphan_billed = float(kpis.get("orphan_billed_sum") or 0)
    if orphan_count:
        signals.append(
            _signal(
                "orphans:billed",
                "critical" if orphan_count >= 10 else "warn",
                f"{orphan_count} customers billed without a live project",
                _usd(orphan_billed) if orphan_billed else str(orphan_count),
                "Assign owners to link billing to Teamwork delivery",
                "orphans",
            )
        )

    open_ar = float(kpis.get("open_ar") or 0)
    if open_ar >= 1000:
        signals.append(
            _signal(
                "ar:open",
                "info",
                "Open AR on mapped jobs",
                _usd(open_ar),
                "Confirmed join-layer receivables only",
                "jobs",
            )
        )

    if has_prior_snapshot and prior_kpis:
        mapped = int(kpis.get("join_mapped") or 0)
        total = int(kpis.get("join_total") or 0)
        prior_mapped = int(prior_kpis.get("join_mapped") or 0)
        prior_total = int(prior_kpis.get("join_total") or 0)
        queue = int(kpis.get("queue_count") or 0)
        prior_queue = int(prior_kpis.get("queue_count") or 0)
        invoices = int(kpis.get("unlinked_invoice_count") or 0)
        prior_invoices = int(prior_kpis.get("unlinked_invoice_count") or 0)
        signals.append(
            _signal(
                "kpi:week",
                "info",
                "Prior week vs now",
                f"{mapped}/{total} join",
                (
                    f"Queue {prior_queue}→{queue} · "
                    f"Join {prior_mapped}/{prior_total}→{mapped}/{total} · "
                    f"Unlinked invoices {prior_invoices}→{invoices}"
                ),
                "jobs",
            )
        )

    join_mapped = int(kpis.get("join_mapped") or 0)
    join_total = int(kpis.get("join_total") or 0)
    if join_total and join_mapped < join_total:
        gap = join_total - join_mapped
        signals.append(
            _signal(
                "join:health",
                "warn" if gap <= 3 else "critical",
                f"Join health {join_mapped}/{join_total}",
                str(gap),
                f"{gap} live jobs lack a confirmed QuickBooks link",
                "portfolio",
            )
        )

    queue_items = [
        row for row in open_items if row.get("kind") in {"delivery", "mapping", "receivable"}
    ][:3]
    for row in queue_items:
        signals.append(
            _signal(
                f"priority:{row['id']}",
                "critical" if row.get("kind") == "delivery" else "warn",
                str(row.get("title") or row.get("id")),
                _usd(float(row.get("amount") or 0)) if float(row.get("amount") or 0) else "—",
                str(row.get("detail") or ""),
                str(row.get("go_to") or "jobs"),
            )
        )

    return signals
