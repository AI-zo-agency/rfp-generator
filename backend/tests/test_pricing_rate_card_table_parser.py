"""Multi-format rate-card extraction: markdown tier tables + inline prose."""

from __future__ import annotations

from app.services.pricing_rate_card_builder import build_pricing_rate_card_from_guide_text

_TABLE_GUIDE = """
# Pricing Guide

**4.4 Email Newsletter Design & Setup**

| **TIER**    | **RANGE**        | **BASIS**                           |
| ----------- | ---------------- | ----------------------------------- |
| **Low**     | $750 to $900     | **○ PROPOSED** (internal benchmark) |
| **Average** | $1,000 to $1,850 | **✓ VERIFIED** Santa Clara $1,000   |
| **High**    | $1,500 to $2,800 | **○ PROPOSED** (internal benchmark) |

**5.3 Monthly Social Media Management (3 Platforms)**

| **TIER**    | **RANGE**         | **BASIS**                         |
| ----------- | ----------------- | --------------------------------- |
| **Low**     | $2,500 to $3,200  | **○ PROPOSED** (internal benchmark) |
| **Average** | $3,200 to $4,800  | **✓ VERIFIED** Santa Clara $3,200 |
| **High**    | $5,500 to $9,500  | **○ PROPOSED** (internal benchmark) |

**5.4 Monthly Digital Advertising Management**

| **TIER**    | **RANGE**         | **BASIS** |
| ----------- | ----------------- | --------- |
| **Low**     | $2,000 to $2,400  | x |
| **Average** | $2,500 to $4,500  | x |
| **High**    | $5,000 to $8,000  | x |
"""

_INLINE_GUIDE = """
- 5.3 Monthly Social Media Management 3 platforms (Avg: $3,200–$4,800)
- 5.4 Monthly Digital Advertising Management (Avg: $2,500–$4,500)
"""

_MIXED_GUIDE = (
    _TABLE_GUIDE
    + "\n\nLegacy line:\n"
    + "- 9.1 Project Management short projects (Avg: $7,500–$12,000)\n"
)


def test_table_guide_extracts_menu_tiers() -> None:
    card = build_pricing_rate_card_from_guide_text(_TABLE_GUIDE)
    assert len(card.rates) >= 6  # at least 2 services × 3 tiers, or Average+…
    by_id = {r.rate_id: r for r in card.rates}
    assert "guide-5.3-average" in by_id
    avg = by_id["guide-5.3-average"]
    assert avg.amount_low == 3200.0
    assert avg.amount_high == 4800.0
    assert avg.amount == 4000.0
    assert "social" in (avg.service or "").casefold()
    assert avg.unit == "monthly"
    assert "guide-4.4-average" in by_id
    assert by_id["guide-4.4-average"].amount_low == 1000.0


def test_inline_prose_still_works() -> None:
    card = build_pricing_rate_card_from_guide_text(_INLINE_GUIDE)
    menus = {r.menu_id for r in card.rates}
    assert "5.3" in menus
    assert "5.4" in menus


def test_mixed_table_and_inline_dedupes_by_menu_tier() -> None:
    card = build_pricing_rate_card_from_guide_text(_MIXED_GUIDE)
    menus = {r.menu_id for r in card.rates}
    assert "5.3" in menus
    assert "9.1" in menus
    # table path should win / not invent duplicate average for 5.3
    avg_53 = [r for r in card.rates if r.menu_id == "5.3" and r.tier == "Average"]
    assert len(avg_53) == 1


def test_version_april_line_not_a_rate() -> None:
    text = (
        "| **VERSION v1.0 · April 26, 2026**Approved. |\n"
        + _TABLE_GUIDE
    )
    card = build_pricing_rate_card_from_guide_text(text)
    assert not any("april" in (r.service or "").casefold() for r in card.rates)
    assert len(card.rates) >= 6
