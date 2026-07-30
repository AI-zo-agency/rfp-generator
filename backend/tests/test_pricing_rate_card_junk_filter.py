"""Hardening: reject junk guide labels like '· April' / bare years."""

from app.services.pricing_rate_card_builder import (
    build_pricing_rate_card_from_guide_text,
    is_junk_service_label,
)


def test_junk_april_fragment_rejected() -> None:
    assert is_junk_service_label("· April")
    assert is_junk_service_label("April")
    assert not is_junk_service_label("Monthly Social Media Management")


def test_version_date_fragment_not_extracted_as_rate() -> None:
    text = """
    Guide v2.1 · April 2025
    2.1 · April $26
    5.3 Monthly Social Media Management 3 platforms (Avg: $3,200–$4,800)
    5.4 Monthly Digital Advertising Management (Avg: $2,500–$4,500)
    """
    card = build_pricing_rate_card_from_guide_text(text)
    services = [r.service.casefold() for r in card.rates]
    assert not any("april" in s for s in services)
    assert len(card.rates) >= 2
    amounts = {r.amount for r in card.rates}
    assert 26.0 not in amounts
    assert 2025.0 not in amounts


def test_single_amount_requires_dollar_sign() -> None:
    text = "4.4 Email Newsletter Average 1850\n5.3 Social (Avg: $3,200–$4,800)\n5.4 Ads (Avg: $2,500–$4,500)"
    card = build_pricing_rate_card_from_guide_text(text)
    # bare 1850 without $ must not become a rate; ranges still parse
    assert all(r.amount != 1850.0 for r in card.rates)
    assert len(card.rates) >= 2
