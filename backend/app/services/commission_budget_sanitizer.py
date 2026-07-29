"""Option B — sanitize commission budgets before the hard budget editor gate."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.models.pricing_contract import PricingContract
from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_validation import (
    collect_orphan_commission_violations,
    infer_line_item_type,
    reconcile_proposal_budget,
)

logger = logging.getLogger(__name__)

_COMMISSION_RE = re.compile(r"\bcommission\b", re.I)
_MANUAL_FILL_MEDIA = (
    "[MANUAL FILL: annual client media pass-through / media base — confirm before submission]"
)
_MANUAL_FILL_FEE = (
    "[MANUAL FILL: agency commission fee — derive from confirmed media base × rate; "
    "do not invent dollars]"
)
_FLAG_UNEVIDENCED = (
    "[PRICING FLAG: commission shape retained — media spend unevidenced; MANUAL FILL required]"
)


def _is_commission_fee_line(item: BudgetLineItem) -> bool:
    if infer_line_item_type(item) == "client_passthrough":
        return False
    blob = f"{item.description or ''} {item.notes or ''}"
    if re.search(r"\b(?:no|without)\s+(?:media\s+)?commission\b", blob, re.I):
        # Negation in notes/description is not a commission fee line.
        if not re.search(r"\bagency\s+commission\b", blob, re.I):
            return False
    return bool(_COMMISSION_RE.search(blob))


def _is_passthrough_line(item: BudgetLineItem) -> bool:
    return infer_line_item_type(item) == "client_passthrough"


def _ensure_manual_fill_text(text: str, tag: str) -> str:
    base = (text or "").strip()
    if tag.casefold() in base.casefold():
        return base
    if not base:
        return tag
    return f"{base}\n{tag}"


def _clear_money(item: BudgetLineItem, *, description_tag: str) -> BudgetLineItem:
    return item.model_copy(
        update={
            "rate": None,
            "quantity": None,
            "extended": None,
            "is_manual_fill": True,
            "source_rate_id": None,
            "description": _ensure_manual_fill_text(item.description or "", description_tag),
            "notes": _ensure_manual_fill_text(item.notes or "", description_tag),
        }
    )


def _append_flag(flags: list[str], flag: str) -> list[str]:
    if flag not in flags:
        flags.append(flag)
    return flags


def sanitize_commission_budget(
    budget: ProposalBudget,
    contract: PricingContract,
) -> ProposalBudget:
    """Apply Option B recovery; never invent media spend.

    - Commission-ish + no evidenced base: clear invented commission $, keep shape + MANUAL FILL.
    - Evidenced base: lock pass-through and recompute fee from rate × base when rate known.
    - Non-commission: drop fabricated positive commission fee lines.
    """
    working = budget
    flags = list(working.pricing_flags or [])
    fee_model = contract.fee_model
    media = contract.media_spend_annual
    rate = contract.commission_rate
    if rate is None and working.commission_rate is not None:
        rate = float(working.commission_rate)
        if rate > 1:
            rate = rate / 100.0

    logger.info(
        "commission_sanitize start rfp_id=%s fee_model=%s media=%s rate=%s orphans_before=%s",
        working.rfp_id,
        fee_model,
        media,
        rate,
        len(collect_orphan_commission_violations(working)),
    )

    if fee_model not in {"commission", "hybrid"}:
        kept: list[BudgetLineItem] = []
        dropped = 0
        for item in working.line_items:
            if _is_commission_fee_line(item) and float(item.extended or 0) > 0:
                dropped += 1
                continue
            kept.append(item)
        if dropped:
            flags = _append_flag(
                flags,
                "[PRICING FLAG: dropped fabricated commission line(s) — fee model is not commission]",
            )
            working = working.model_copy(
                update={
                    "line_items": kept,
                    "commission_model": None,
                    "commission_rate": None,
                    "client_media_passthrough": None,
                    "pricing_flags": flags,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            working = reconcile_proposal_budget(working)
            logger.info(
                "commission_sanitize non_commission_drop rfp_id=%s dropped=%s",
                working.rfp_id,
                dropped,
            )
            return working

    # Commission / hybrid path
    if media is not None and media > 0:
        items = list(working.line_items)
        # Ensure pass-through base line
        pass_idx = next((i for i, it in enumerate(items) if _is_passthrough_line(it)), None)
        if pass_idx is None:
            items.append(
                BudgetLineItem(
                    id=f"L-media-{len(items) + 1}",
                    category="Media",
                    description="Client media pass-through (at net, not agency revenue)",
                    lineItemType="client_passthrough",
                    rate=media,
                    quantity=1,
                    extended=media,
                    unit="flat",
                    isManualFill=False,
                )
            )
        else:
            items[pass_idx] = items[pass_idx].model_copy(
                update={
                    "line_item_type": "client_passthrough",
                    "rate": media,
                    "quantity": 1,
                    "extended": media,
                    "is_manual_fill": False,
                }
            )

        fee_amount = round(media * float(rate), 2) if rate and rate > 0 else None
        fee_idx = next((i for i, it in enumerate(items) if _is_commission_fee_line(it)), None)
        if fee_amount is not None:
            if fee_idx is None:
                items.append(
                    BudgetLineItem(
                        id=f"L-commission-{len(items) + 1}",
                        category="Media",
                        description="Agency commission on media placements",
                        lineItemType="agency_fee",
                        rate=fee_amount,
                        quantity=1,
                        extended=fee_amount,
                        unit="flat",
                        isManualFill=False,
                    )
                )
            else:
                items[fee_idx] = items[fee_idx].model_copy(
                    update={
                        "line_item_type": "agency_fee",
                        "rate": fee_amount,
                        "quantity": 1,
                        "extended": fee_amount,
                        "is_manual_fill": False,
                        "source_rate_id": None,
                    }
                )

        working = working.model_copy(
            update={
                "line_items": items,
                "client_media_passthrough": media,
                "commission_rate": rate if rate is not None else working.commission_rate,
                "commission_model": working.commission_model or "commission",
                "agency_revenue_estimate": fee_amount
                if fee_amount is not None
                else working.agency_revenue_estimate,
                "pricing_flags": flags,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        working = reconcile_proposal_budget(working)
        logger.info(
            "commission_sanitize evidenced_base rfp_id=%s media=%s fee=%s",
            working.rfp_id,
            media,
            fee_amount,
        )
        return working

    # Option B: commission shape, unevidenced media — clear invented $, MANUAL FILL
    orphans = collect_orphan_commission_violations(working)
    has_orphan_money = bool(orphans) or any(
        _is_commission_fee_line(item) and float(item.extended or 0) > 0
        for item in working.line_items
    )
    items = list(working.line_items)
    changed = False

    if has_orphan_money or fee_model in {"commission", "hybrid"}:
        for i, item in enumerate(items):
            if _is_commission_fee_line(item) and (
                float(item.extended or 0) > 0 or not item.is_manual_fill
            ):
                items[i] = _clear_money(item, description_tag=_MANUAL_FILL_FEE)
                changed = True

        pass_idx = next((i for i, it in enumerate(items) if _is_passthrough_line(it)), None)
        if pass_idx is None:
            items.append(
                BudgetLineItem(
                    id=f"L-media-mfill-{len(items) + 1}",
                    category="Media",
                    description=_MANUAL_FILL_MEDIA,
                    lineItemType="client_passthrough",
                    extended=None,
                    rate=None,
                    quantity=None,
                    unit="flat",
                    isManualFill=True,
                    notes=_MANUAL_FILL_MEDIA,
                )
            )
            changed = True
        else:
            pt = items[pass_idx]
            if float(pt.extended or 0) > 0 and media is None:
                # Unevidenced positive pass-through is also invention — clear it.
                items[pass_idx] = _clear_money(pt, description_tag=_MANUAL_FILL_MEDIA)
                changed = True
            elif not pt.is_manual_fill:
                items[pass_idx] = pt.model_copy(
                    update={
                        "is_manual_fill": True,
                        "description": _ensure_manual_fill_text(
                            pt.description or "", _MANUAL_FILL_MEDIA
                        ),
                    }
                )
                changed = True

        if not any(_is_commission_fee_line(it) for it in items):
            items.append(
                BudgetLineItem(
                    id=f"L-commission-mfill-{len(items) + 1}",
                    category="Media",
                    description=_MANUAL_FILL_FEE,
                    lineItemType="agency_fee",
                    extended=None,
                    rate=None,
                    unit="flat",
                    isManualFill=True,
                    notes=_MANUAL_FILL_FEE,
                )
            )
            changed = True

        flags = _append_flag(flags, _FLAG_UNEVIDENCED)
        working = working.model_copy(
            update={
                "line_items": items,
                "client_media_passthrough": None,
                # Clear invented aggregate dollars so reconcile cannot resurrect them
                # from lumpSumTotal / stale agency fee fields.
                "agency_revenue_estimate": None,
                "lump_sum_total": None,
                "agency_fee_subtotal": None,
                "line_item_sum": None,
                "total_client_invoicing": None,
                "commission_rate": rate if rate is not None else working.commission_rate,
                "commission_model": working.commission_model or "commission",
                "pricing_flags": flags,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        working = reconcile_proposal_budget(working)
        logger.info(
            "commission_sanitize option_b_manual_fill rfp_id=%s changed=%s orphans_after=%s",
            working.rfp_id,
            changed,
            len(collect_orphan_commission_violations(working)),
        )

    return working
