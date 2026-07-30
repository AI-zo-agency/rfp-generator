"""Build PricingRateCard from KB 00_Guide_Pricing text (T5.1).

Deterministic extract only — never invent rates. Thin/empty guide → empty card + warnings.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.models.pricing_rate_card import PricingRate, PricingRateCard

logger = logging.getLogger(__name__)

# e.g. "5.3 Monthly Social Media Management 3 platforms (Avg: $3,200–$4,800)"
_RANGE_RE = re.compile(
    r"(?P<menu>\d+\.\d+)\s+"
    r"(?P<label>[^\n(]{3,120}?)\s*"
    r"\((?P<tier>Avg|Average|Low|High)\s*:\s*"
    r"\$?(?P<low>[\d,]+(?:\.\d+)?)\s*[–\-]\s*\$?(?P<high>[\d,]+(?:\.\d+)?)\)",
    re.IGNORECASE,
)

# e.g. "4.4 Email Newsletter … Average $1,850" — require explicit $ to avoid years/page nos
_SINGLE_RE = re.compile(
    r"(?P<menu>\d+\.\d+)\s+"
    r"(?P<label>[^\n$]{3,100}?)"
    r"(?P<tier>Low|Average|Avg|High)?\s*"
    r"\$(?P<amt>[\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)

# Hourly labor category: "Senior Strategist $175/hr"
_HOURLY_RE = re.compile(
    r"(?P<label>(?:Senior|Junior|Associate|Lead|Principal)?\s*"
    r"(?:Strategist|Designer|Developer|Copywriter|Account\s+Manager|"
    r"Project\s+Manager|Media\s+Buyer|Producer)[^\n$]{0,40}?)"
    r"\$?(?P<amt>[\d,]+(?:\.\d+)?)\s*/\s*h(?:r|our)",
    re.IGNORECASE,
)

# Markdown table format from 00_Guide_Pricing.docx OCR/index:
#   **5.3 Monthly Social Media Management (3 Platforms)**
#   | **Average** | $3,200 to $4,800 | … |
_MENU_HEADER_RE = re.compile(
    r"(?m)^\s*\*{0,2}(?P<menu>\d+\.\d+)\s+(?P<label>[^*\n|]{3,120}?)\*{0,2}\s*$"
)
_TIER_ROW_RE = re.compile(
    r"\|\s*\*{0,2}(?P<tier>Low|Average|Avg|High)\*{0,2}\s*\|\s*"
    r"\$?(?P<low>[\d,]+(?:\.\d+)?)\s*(?:to|[–\-])\s*\$?(?P<high>[\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)

_MIN_CONFIDENCE_BINDABLE = 0.75

_MONTH_NAMES = frozenset(
    {
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
    }
)


def is_junk_service_label(label: str) -> bool:
    """True for version/date debris like '· April' that is not a service line."""
    cleaned = re.sub(r"^[\s·•\-–—\.]+", "", (label or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) < 4:
        return True
    letters = re.sub(r"[^a-zA-Z]+", "", cleaned)
    if len(letters) < 3:
        return True
    tokens = [t for t in re.split(r"[^a-zA-Z]+", cleaned.casefold()) if t]
    if tokens and all(t in _MONTH_NAMES for t in tokens):
        return True
    if cleaned.casefold() in _MONTH_NAMES:
        return True
    return False


def _parse_money(raw: str) -> float | None:
    try:
        return float(raw.replace(",", "").strip())
    except ValueError:
        return None


def _norm_tier(raw: str | None) -> str:
    t = (raw or "Average").strip().lower()
    if t in ("avg", "average"):
        return "Average"
    if t == "low":
        return "Low"
    if t == "high":
        return "High"
    return "Average"


def _infer_unit(label: str) -> str:
    low = label.lower()
    if "hour" in low or "/hr" in low:
        return "hour"
    if "month" in low or "monthly" in low:
        return "monthly"
    if "annual" in low or "year" in low:
        return "annual"
    if "%" in low or "percent" in low or "commission" in low:
        return "percent"
    return "fixed"


def _append_rate(
    rates: list[PricingRate],
    seen: set[str],
    *,
    menu: str,
    label: str,
    tier: str,
    low: float,
    high: float,
    source_doc: str,
    confidence: float,
    notes: str,
) -> None:
    rate_id = f"guide-{menu}-{tier.lower()}"
    if rate_id in seen:
        return
    if is_junk_service_label(label):
        return
    seen.add(rate_id)
    mid = round((low + high) / 2.0, 2)
    rates.append(
        PricingRate(
            rateId=rate_id,
            service=label,
            tier=tier,
            unit=_infer_unit(label),  # type: ignore[arg-type]
            amount=mid,
            amountLow=low,
            amountHigh=high,
            menuId=menu,
            sourceDoc=source_doc,
            confidence=confidence,
            notes=notes,
        )
    )


def _extract_markdown_table_rates(
    text: str,
    *,
    source_doc: str,
    rates: list[PricingRate],
    seen: set[str],
) -> int:
    """Parse **N.N Label** headers followed by Low/Average/High `$X to $Y` rows."""
    headers = list(_MENU_HEADER_RE.finditer(text))
    if not headers:
        return 0
    added = 0
    for index, header in enumerate(headers):
        menu = header.group("menu")
        label = re.sub(r"\s+", " ", header.group("label")).strip(" -–—·•*")
        if is_junk_service_label(label):
            continue
        block_end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        block = text[header.end() : block_end]
        before = len(rates)
        for row in _TIER_ROW_RE.finditer(block):
            tier = _norm_tier(row.group("tier"))
            low = _parse_money(row.group("low"))
            high = _parse_money(row.group("high"))
            if low is None or high is None or low <= 0 or high <= 0:
                continue
            _append_rate(
                rates,
                seen,
                menu=menu,
                label=label,
                tier=tier,
                low=low,
                high=high,
                source_doc=source_doc,
                confidence=0.95,
                notes="markdown table range midpoint from guide extract",
            )
        added += len(rates) - before
    return added


def build_pricing_rate_card_from_guide_text(
    guide_text: str,
    *,
    source_doc: str = "00_Guide_Pricing",
) -> PricingRateCard:
    """Extract bindable rates from guide text (tables + inline). No invention."""
    text = guide_text or ""
    warnings: list[str] = []
    if not text.strip() or text.strip().startswith("("):
        warnings.append("No usable 00_Guide_Pricing text — rate card empty; flag unknowns.")
        card = PricingRateCard(
            rates=[],
            guideExcerptChars=len(text),
            builtAt=datetime.now(timezone.utc).isoformat(),
            warnings=warnings,
        )
        logger.warning("pricing_rate_card_empty reason=no_guide_text")
        return card

    rates: list[PricingRate] = []
    seen: set[str] = set()

    # Prefer structured markdown tables from the indexed DOCX (stable full-doc shape).
    table_count = _extract_markdown_table_rates(
        text, source_doc=source_doc, rates=rates, seen=seen
    )
    if table_count:
        logger.info("pricing_rate_card_table_extract rates=%s", table_count)

    for match in _RANGE_RE.finditer(text):
        menu = match.group("menu")
        tier = _norm_tier(match.group("tier"))
        low = _parse_money(match.group("low"))
        high = _parse_money(match.group("high"))
        if low is None or high is None:
            continue
        label = re.sub(r"\s+", " ", match.group("label")).strip(" -–—·•")
        _append_rate(
            rates,
            seen,
            menu=menu,
            label=label,
            tier=tier,
            low=low,
            high=high,
            source_doc=source_doc,
            confidence=0.95,
            notes="inline range midpoint from guide extract",
        )

    for match in _SINGLE_RE.finditer(text):
        menu = match.group("menu")
        tier = _norm_tier(match.group("tier"))
        rate_id = f"guide-{menu}-{tier.lower()}"
        if rate_id in seen or any(r.menu_id == menu for r in rates):
            continue
        amt = _parse_money(match.group("amt"))
        if amt is None or amt <= 0:
            continue
        # Reject year-like bare amounts even if somehow matched
        if 1900 <= amt <= 2100 and not match.group("tier"):
            continue
        label = re.sub(r"\s+", " ", match.group("label")).strip(" -–—·•")
        if len(label) < 4 or is_junk_service_label(label):
            continue
        seen.add(rate_id)
        rates.append(
            PricingRate(
                rateId=rate_id,
                service=label,
                tier=tier,
                unit=_infer_unit(label),  # type: ignore[arg-type]
                amount=amt,
                amountLow=amt,
                amountHigh=amt,
                menuId=menu,
                sourceDoc=source_doc,
                confidence=0.85,
                notes="single amount from guide extract",
            )
        )

    for index, match in enumerate(_HOURLY_RE.finditer(text), start=1):
        amt = _parse_money(match.group("amt"))
        if amt is None or amt <= 0:
            continue
        label = re.sub(r"\s+", " ", match.group("label")).strip()
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40]
        rate_id = f"guide-hourly-{slug or index}"
        if rate_id in seen:
            continue
        seen.add(rate_id)
        rates.append(
            PricingRate(
                rateId=rate_id,
                service=label,
                tier="Average",
                unit="hour",
                amount=amt,
                amountLow=amt,
                amountHigh=amt,
                menuId="",
                sourceDoc=source_doc,
                confidence=0.8,
                notes="hourly labor category from guide extract",
            )
        )

    if not rates:
        warnings.append(
            "Guide text present but no menu rates parsed — treat all line rates as manual fill."
        )

    card = PricingRateCard(
        rates=rates,
        guideExcerptChars=len(text),
        builtAt=datetime.now(timezone.utc).isoformat(),
        warnings=warnings,
    )
    logger.info(
        "pricing_rate_card_built rates=%s warnings=%s guide_chars=%s",
        len(rates),
        len(warnings),
        len(text),
    )
    return card


def bindable_rates(card: PricingRateCard | None) -> list[PricingRate]:
    if not card:
        return []
    return [r for r in card.rates if r.confidence >= _MIN_CONFIDENCE_BINDABLE and r.amount]
