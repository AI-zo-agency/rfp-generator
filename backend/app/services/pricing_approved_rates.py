"""Hourly rates allowed for official Pricing Forms / personnel loading.

Sources (merged, no RFP-specific hardcoding):
  1. Human file: data/pricing_approved_hourly_rates.json (wins on rateId)
  2. KB Agency Role Rates–style tables: Billable Rate column only
     (parsed via pricing_rate_card_builder; cached after Phase 3.5 fetch)

Never from: 07_FIN_* lost bids, Guide menu SKUs (4.1 etc.), Internal/Raw floor.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from app.models.pricing_rate_card import PricingRate, PricingRateCard
from app.models.proposal import BudgetLineItem, ProposalBudget

logger = logging.getLogger(__name__)

BLOCKED_SOURCE_PREFIXES = ("07_FIN_",)
KB_ROLE_APPROVER = "KB role billable column"

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_REGISTRY_PATH = _DATA_DIR / "pricing_approved_hourly_rates.json"
_KB_CACHE_PATH = _DATA_DIR / "pricing_role_billable_cache.json"

_ROLE_SOURCE_RE = re.compile(
    r"(?i)role\s+rates?|labor\s+cost|billable\s+rate|rate\s+card|cost\s+table|"
    r"role-hourly-|KB labor/role"
)


class ApprovedHourlyRate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rate_id: str = Field(alias="rateId")
    label: str = ""
    amount: float
    approved_by: str = Field(alias="approvedBy")
    source_file: str = Field(alias="sourceFile")
    effective_from: str = Field(default="", alias="effectiveFrom")
    effective_to: str | None = Field(default=None, alias="effectiveTo")
    notes: str = ""


def source_is_blocked(source: str | None) -> bool:
    text = (source or "").strip()
    if not text:
        return False
    name = Path(text).name
    return any(name.startswith(p) or text.startswith(p) for p in BLOCKED_SOURCE_PREFIXES)


def _is_guide_menu_sku(rate: PricingRate) -> bool:
    """True for 00_Guide_Pricing deliverable SKUs (not role hourly)."""
    mid = (rate.menu_id or "").strip()
    if re.fullmatch(r"\d+\.\d+", mid):
        return True
    src = (rate.source_doc or "").casefold()
    notes = (rate.notes or "").casefold()
    if "00_guide_pricing" in src and "role" not in notes and "billable" not in notes:
        return True
    return False


def _looks_like_role_billable(rate: PricingRate) -> bool:
    if (rate.unit or "") != "hour":
        return False
    if _is_guide_menu_sku(rate):
        return False
    rid = (rate.rate_id or "").casefold()
    notes = (rate.notes or "").casefold()
    src = (rate.source_doc or "")
    if rid.startswith("role-hourly-"):
        return True
    if "billable rate from kb role" in notes:
        return True
    if _ROLE_SOURCE_RE.search(src) or _ROLE_SOURCE_RE.search(notes):
        return True
    return False


def approved_from_pricing_rates(
    rates: Iterable[PricingRate],
) -> list[ApprovedHourlyRate]:
    """Promote KB role billable PricingRate rows into the approved hourly registry."""
    out: list[ApprovedHourlyRate] = []
    for rate in rates:
        if not _looks_like_role_billable(rate):
            continue
        if source_is_blocked(rate.source_doc):
            continue
        try:
            amt = float(rate.amount) if rate.amount is not None else 0.0
        except (TypeError, ValueError):
            continue
        if amt < 75 or amt > 750:
            continue
        out.append(
            ApprovedHourlyRate(
                rateId=rate.rate_id,
                label=(rate.service or "").strip(),
                amount=amt,
                approvedBy=KB_ROLE_APPROVER,
                sourceFile=(rate.source_doc or "KB labor/role billable rates").strip(),
                notes=(rate.notes or "billable column from KB role/labor rate card").strip(),
            )
        )
    return out


def approved_from_role_billable_text(
    text: str,
    *,
    source_file: str,
) -> list[ApprovedHourlyRate]:
    """Parse a role/labor markdown table (Billable column) into approved hourlies."""
    from app.services.pricing_rate_card_builder import build_pricing_rate_card_from_guide_text

    if not (text or "").strip():
        return []
    if source_is_blocked(source_file):
        logger.warning("role_billable_text skipped blocked source=%s", source_file)
        return []
    # Skip pure guide menu docs — role extract needs the billable table shape.
    if re.search(r"(?i)00_guide_pricing", source_file or "") and not re.search(
        r"(?i)role\s+rates?|labor|billable", source_file or ""
    ):
        return []
    card = build_pricing_rate_card_from_guide_text(
        text, source_doc=source_file or "KB labor/role billable rates"
    )
    return approved_from_pricing_rates(card.rates or [])


def _read_json(path: Path) -> Any:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("approved_hourly_rates unreadable path=%s err=%s", path, exc)
        return None


def _load_json_registry() -> list[ApprovedHourlyRate]:
    raw = _read_json(_REGISTRY_PATH)
    if not raw:
        return []
    rows = raw.get("rates") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return []
    out: list[ApprovedHourlyRate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            rate = ApprovedHourlyRate.model_validate(row)
        except Exception as exc:  # noqa: BLE001
            logger.warning("approved_hourly_rates skip row=%s err=%s", row, exc)
            continue
        if source_is_blocked(rate.source_file):
            continue
        if not (rate.approved_by or "").strip():
            continue
        if float(rate.amount or 0) <= 0:
            continue
        out.append(rate)
    return out


def _load_kb_cache() -> list[ApprovedHourlyRate]:
    raw = _read_json(_KB_CACHE_PATH)
    if not raw:
        return []
    rows = raw.get("rates") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return []
    parsed: list[ApprovedHourlyRate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            rate = ApprovedHourlyRate.model_validate(row)
        except Exception:  # noqa: BLE001
            continue
        if source_is_blocked(rate.source_file):
            continue
        if not _ROLE_SOURCE_RE.search(rate.source_file) and not _ROLE_SOURCE_RE.search(
            rate.notes or ""
        ):
            # Cache must stay role/labor — never guide menu bleed.
            if not (rate.rate_id or "").startswith("role-hourly-"):
                continue
        parsed.append(rate)
    return parsed


def persist_kb_role_billable_cache(rates: list[ApprovedHourlyRate]) -> None:
    """Write KB role billables so sync prepare/render can resolve without re-fetch."""
    payload = {
        "rates": [r.model_dump(by_alias=True) for r in rates],
        "updatedFrom": "kb_role_billable_extract",
    }
    try:
        _KB_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _KB_CACHE_PATH.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        logger.info(
            "role_billable_cache_written path=%s count=%s",
            _KB_CACHE_PATH,
            len(rates),
        )
    except OSError as exc:
        logger.warning("role_billable_cache_write_failed: %s", exc)


def merge_approved_hourly_rates(
    *groups: list[ApprovedHourlyRate],
) -> list[ApprovedHourlyRate]:
    """Merge registries. Earlier groups win on rateId; amounts may repeat (roles)."""
    by_id: dict[str, ApprovedHourlyRate] = {}
    for group in groups:
        for rate in group:
            if source_is_blocked(rate.source_file):
                continue
            rid = (rate.rate_id or "").strip()
            if not rid:
                continue
            if rid not in by_id:
                by_id[rid] = rate
    return list(by_id.values())


def load_approved_hourly_rates(
    *,
    rate_card: PricingRateCard | None = None,
) -> list[ApprovedHourlyRate]:
    """JSON overrides + KB role cache (+ optional in-memory rate card hourlies)."""
    human = _load_json_registry()
    kb_cache = _load_kb_cache()
    from_card = (
        approved_from_pricing_rates(rate_card.rates or []) if rate_card is not None else []
    )
    merged = merge_approved_hourly_rates(human, from_card, kb_cache)
    logger.info(
        "approved_hourly_rates loaded human=%s card=%s cache=%s merged=%s",
        len(human),
        len(from_card),
        len(kb_cache),
        len(merged),
    )
    return merged


def ingest_role_billable_from_guide_bundle(
    guide_text: str,
    *,
    kb_sources: list[str] | None = None,
) -> list[ApprovedHourlyRate]:
    """Extract + cache role billables from the Phase 3.5 guide+labor text bundle."""
    text = guide_text or ""
    if not text.strip():
        return []
    # Prefer the labor section when present (avoids menu SKU noise).
    labor_marker = "=== KB labor / role billable rates"
    if labor_marker in text:
        # Drop the rest of the section header line, keep table body.
        after = text.split(labor_marker, 1)[-1]
        nl = after.find("\n")
        text = after[nl + 1 :] if nl >= 0 else after
    source = "KB labor/role billable rates"
    for src in kb_sources or []:
        if _ROLE_SOURCE_RE.search(src or "") and not re.search(
            r"(?i)00_guide_pricing", src or ""
        ):
            source = src
            break
        if re.search(r"(?i)role\s+rates?|cost\s+table", src or ""):
            source = src
            break
    rates = approved_from_role_billable_text(text, source_file=source)
    if rates:
        persist_kb_role_billable_cache(rates)
    return rates


def primary_approved_hourly(
    registry: list[ApprovedHourlyRate] | None = None,
) -> ApprovedHourlyRate | None:
    """Working form $/hr: mode of approved role billables (not Guide menu SKUs).

    When a buyer form asks for one hourly rate, use the most common billable
    amount on the role card (typically the production-team band). Prefer a
    named production role at that amount when several labels share it.
    """
    from collections import Counter

    rates = registry if registry is not None else load_approved_hourly_rates()
    if not rates:
        return None
    amounts = [round(float(r.amount), 2) for r in rates]
    mode_amt, _ = Counter(amounts).most_common(1)[0]
    preferred = (
        "digital team",
        "account manager",
        "art director",
        "copywriter",
        "creative director",
    )
    at_mode = [r for r in rates if abs(float(r.amount) - mode_amt) <= 0.01]
    for label in preferred:
        for r in at_mode:
            if label in (r.label or "").casefold():
                return r
    return at_mode[0] if at_mode else None


def resolve_approved_hourly(
    amount: float | None,
    *,
    rate_id: str | None = None,
    source: str | None = None,
    registry: list[ApprovedHourlyRate] | None = None,
    tolerance: float = 0.01,
) -> ApprovedHourlyRate | None:
    """Match by rate_id or exact amount. Blocked sources never resolve."""
    if source_is_blocked(source):
        return None
    rates = registry if registry is not None else load_approved_hourly_rates()
    if not rates:
        return None
    rid = (rate_id or "").strip()
    if rid:
        for r in rates:
            if r.rate_id == rid:
                return r
        return None
    if amount is None:
        return None
    try:
        target = float(amount)
    except (TypeError, ValueError):
        return None
    for r in rates:
        if abs(float(r.amount) - target) <= tolerance:
            return r
    return None


def budget_needs_approved_hourly(budget: ProposalBudget) -> bool:
    fmt = (budget.budget_format or "").casefold().replace("-", "_")
    return fmt in {"blended_rate_form", "personnel_loading", "hourly_rate_form"}


def scrub_unapproved_form_rates(
    budget: ProposalBudget,
    *,
    registry: list[ApprovedHourlyRate] | None = None,
    rate_card: PricingRateCard | None = None,
    pricing_instrument: Any | None = None,
) -> ProposalBudget:
    """Clear invented form $/hr (and hourly line rates) not in the approved registry.

    Guide-backed phased fixed fees are untouched. Form / personnel instruments
    may not ship a dollar rate the registry does not authorize.

    When ``pricing_instrument.kind == buyer_pricing_form`` (confidence ≥ 0.55),
    do **not** seed ``formHourlyRate`` from the role card — instrument track
    rows stay MANUAL FILL (rate gate out of scope for buyer forms).
    """
    if not budget_needs_approved_hourly(budget):
        return budget

    rates = (
        registry
        if registry is not None
        else load_approved_hourly_rates(rate_card=rate_card)
    )
    flags = list(budget.pricing_flags or [])
    updates: dict[str, Any] = {}

    def _flag(msg: str) -> None:
        if msg not in flags:
            flags.append(msg)

    buyer_form = False
    try:
        from app.services.pricing_delivery_context import is_buyer_pricing_form_instrument

        buyer_form = is_buyer_pricing_form_instrument(instrument=pricing_instrument)
    except Exception:  # noqa: BLE001
        buyer_form = False

    hourly = budget.form_hourly_rate
    if buyer_form:
        # Instrument path wins — clear any seeded/legacy form $/hr; tracks stay MANUAL FILL.
        if hourly is not None:
            logger.info(
                "scrub_form_rates cleared formHourlyRate=%s — buyer_pricing_form "
                "(instrument MANUAL FILL; rate gate out of scope)",
                hourly,
            )
            _flag(
                "[PRICING FLAG: formHourlyRate cleared — buyer Pricing Form uses "
                "instrument track MANUAL FILL (rate gate out of scope)]"
            )
            updates["form_hourly_rate"] = None
            hourly = None
        # Skip role-card seeding for buyer forms.
    else:
        hit = resolve_approved_hourly(hourly, registry=rates)
        if hourly is not None and hit is None:
            logger.info(
                "scrub_form_rates cleared formHourlyRate=%s (not in approved registry)",
                hourly,
            )
            _flag(
                "[PRICING FLAG: formHourlyRate cleared — not in approved hourly registry "
                "(KB role billable table or pricing_approved_hourly_rates.json)]"
            )
            updates["form_hourly_rate"] = None
            hourly = None
        elif hourly is not None and hit is not None:
            notes = (budget.form_rate_notes or "").strip()
            if not notes:
                updates["form_rate_notes"] = (
                    f"Hourly from {hit.source_file}: {hit.label or hit.rate_id} "
                    f"(${hit.amount:.2f} billable)."
                )

        # When the form needs a $/hr and scrub left it empty, fill from the role
        # billable mode — never invent outside the approved registry.
        if hourly is None and rates:
            primary = primary_approved_hourly(rates)
            if primary is not None:
                logger.info(
                    "scrub_form_rates seeded formHourlyRate=%s from %s (%s)",
                    primary.amount,
                    primary.label or primary.rate_id,
                    primary.source_file,
                )
                updates["form_hourly_rate"] = float(primary.amount)
                notes = (budget.form_rate_notes or "").strip()
                if not notes:
                    updates["form_rate_notes"] = (
                        f"Hourly from {primary.source_file}: "
                        f"{primary.label or primary.rate_id} "
                        f"(${primary.amount:.2f} billable)."
                    )

    if budget.form_monthly_rate is not None or budget.form_annual_rate is not None:
        _flag(
            "[PRICING FLAG: form monthly/annual cleared — only emit when THIS RFP's "
            "form schema requires those fields]"
        )
        updates["form_monthly_rate"] = None
        updates["form_annual_rate"] = None

    new_items: list[BudgetLineItem] = []
    changed_items = False
    for item in budget.line_items or []:
        unit = (item.unit or "").casefold()
        is_hour = unit in {"hour", "hours", "hr", "hrs"}
        src = (item.rate_source or item.notes or "")[:200]
        if not is_hour or item.rate is None:
            new_items.append(item)
            continue
        if source_is_blocked(src) or source_is_blocked(item.rate_source):
            logger.info(
                "scrub_form_rates blocked 07_FIN source line=%s src=%s",
                item.id,
                src[:80],
            )
            _flag(
                f"[PRICING FLAG: line {item.id} rate cleared — blocked source "
                f"(07_FIN_ / lost-bid files cannot price a live bid)]"
            )
            new_items.append(
                item.model_copy(
                    update={
                        "rate": None,
                        "extended": None,
                        "is_manual_fill": True,
                        "notes": (
                            (item.notes or "")
                            + " [MANUAL FILL: SONJA — approved hourly rate required]"
                        ).strip(),
                    }
                )
            )
            changed_items = True
            continue
        if resolve_approved_hourly(float(item.rate), source=src, registry=rates) is None:
            logger.info(
                "scrub_form_rates cleared unapproved hourly line=%s rate=%s",
                item.id,
                item.rate,
            )
            _flag(
                f"[PRICING FLAG: line {item.id} hourly rate ${item.rate} cleared — "
                "not in approved hourly registry (KB role billable / JSON)]"
            )
            new_items.append(
                item.model_copy(
                    update={
                        "rate": None,
                        "extended": None,
                        "is_manual_fill": True,
                        "notes": (
                            (item.notes or "")
                            + " [MANUAL FILL: SONJA — approved hourly rate required]"
                        ).strip(),
                    }
                )
            )
            changed_items = True
            continue
        new_items.append(item)

    if changed_items:
        updates["line_items"] = new_items
    if flags != list(budget.pricing_flags or []):
        updates["pricing_flags"] = flags
    if not updates:
        return budget
    return budget.model_copy(update=updates)


_HOURLY_RANGE_RE = re.compile(
    r"\$\s*\d[\d,]*(?:\.\d+)?\s*[-–—]\s*\$?\s*\d[\d,]*(?:\.\d+)?\s*/\s*h(?:r|our)\b",
    re.I,
)
_HOURLY_SINGLE_RE = re.compile(
    r"\$\s*(?P<amt>\d[\d,]*(?:\.\d+)?)\s*/\s*h(?:r|our)\b",
    re.I,
)
_MANUAL_HOURLY_FILL = "[MANUAL FILL: SONJA — approved hourly rate required]"


def scrub_unapproved_manuscript_hourly_claims(
    content: str,
    *,
    registry: list[ApprovedHourlyRate] | None = None,
) -> tuple[str, list[str]]:
    """Replace invented $/hr ranges and unregistered singles with MANUAL FILL.

    Fee narrative must not ship Guide-SKU blends ($150–$250/hr) or lone rates
    the Labor Cost / approved registry does not authorize.
    """
    logs: list[str] = []
    body = content or ""
    if not body:
        return body, logs
    rates = registry if registry is not None else load_approved_hourly_rates()

    def _range_repl(_m: re.Match[str]) -> str:
        logs.append("scrubbed unapproved hourly range → MANUAL FILL")
        return _MANUAL_HOURLY_FILL

    out = _HOURLY_RANGE_RE.sub(_range_repl, body)

    def _single_repl(match: re.Match[str]) -> str:
        raw = (match.group("amt") or "").replace(",", "")
        try:
            amount = float(raw)
        except ValueError:
            logs.append("scrubbed unparseable hourly → MANUAL FILL")
            return _MANUAL_HOURLY_FILL
        if resolve_approved_hourly(amount, registry=rates) is not None:
            return match.group(0)
        logs.append(f"scrubbed unapproved ${amount:g}/hr → MANUAL FILL")
        return _MANUAL_HOURLY_FILL

    out = _HOURLY_SINGLE_RE.sub(_single_repl, out)
    if logs:
        logger.info(
            "scrub_unapproved_manuscript_hourly_claims count=%s",
            len(logs),
        )
    return out, logs
