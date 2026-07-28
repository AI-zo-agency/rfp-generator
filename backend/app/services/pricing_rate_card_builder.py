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

# e.g. "4.4 Email Newsletter … Average $1,850" or "9.1 … $7,500"
_SINGLE_RE = re.compile(
    r"(?P<menu>\d+\.\d+)\s+"
    r"(?P<label>[^\n$]{3,100}?)"
    r"(?P<tier>Low|Average|Avg|High)?\s*"
    r"\$?(?P<amt>[\d,]+(?:\.\d+)?)",
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

_MIN_CONFIDENCE_BINDABLE = 0.75


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


def build_pricing_rate_card_from_guide_text(
    guide_text: str,
    *,
    source_doc: str = "00_Guide_Pricing",
) -> PricingRateCard:
    """Extract bindable rates from retrieved guide prose. No invention."""
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

    for match in _RANGE_RE.finditer(text):
        menu = match.group("menu")
        tier = _norm_tier(match.group("tier"))
        low = _parse_money(match.group("low"))
        high = _parse_money(match.group("high"))
        if low is None or high is None:
            continue
        label = re.sub(r"\s+", " ", match.group("label")).strip(" -–—")
        rate_id = f"guide-{menu}-{tier.lower()}"
        if rate_id in seen:
            continue
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
                confidence=0.95,
                notes="range midpoint from guide extract",
            )
        )

    for match in _SINGLE_RE.finditer(text):
        menu = match.group("menu")
        # Skip if already captured as range for this menu+tier
        tier = _norm_tier(match.group("tier"))
        rate_id = f"guide-{menu}-{tier.lower()}"
        if rate_id in seen or any(r.menu_id == menu for r in rates):
            continue
        amt = _parse_money(match.group("amt"))
        if amt is None or amt <= 0:
            continue
        label = re.sub(r"\s+", " ", match.group("label")).strip(" -–—")
        # Avoid matching random numbers that aren't prices (menu ids elsewhere)
        if len(label) < 4:
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
