"""Tests for canonical agency tenure — Who We Are and Business Info must match."""

from datetime import date

from app.services.agency_facts import (
    agency_years_in_operation,
    enforce_agency_tenure,
)


def test_years_in_operation_2026() -> None:
    assert agency_years_in_operation(date(2026, 7, 20)) == 13


def test_enforce_aligns_who_we_are_and_business_info() -> None:
    who = (
        "At zo agency, we are raw, real marketing experts with street-smart "
        "instincts and 12 years of lived experience guiding purpose-driven brands."
    )
    biz = "Founded | August 21, 2012\nYears in Operation | 12\n"
    as_of = date(2026, 7, 20)
    fixed_who = enforce_agency_tenure(who, as_of=as_of)
    fixed_biz = enforce_agency_tenure(biz, as_of=as_of)
    assert "13 years of lived experience" in fixed_who
    assert "12 years" not in fixed_who
    assert "Years in Operation | 13" in fixed_biz
    assert "August 21, 2013" in fixed_biz


def test_enforce_combines_years_of_experience_agency_voice() -> None:
    who = (
        "zö agency combines 12 years of experience with strategy and storytelling "
        "to guide purpose-driven brands."
    )
    fixed = enforce_agency_tenure(who, as_of=date(2026, 8, 14))
    assert "13 years of experience" in fixed
    assert "12 years" not in fixed


def test_enforce_does_not_rewrite_specialist_bio_years() -> None:
    bio = "Shawn has 12 years of WordPress development experience specializing in civic sites."
    assert enforce_agency_tenure(bio, as_of=date(2026, 8, 14)) == bio


def test_strips_complete_scan_tenure_banners() -> None:
    from app.services.agency_facts import strip_tenure_auditor_tags

    text = (
        "zö agency combines 13 years of experience with strategy.\n\n"
        "[MANUAL FILL: Confirm 12 vs 13 years of experience]\n"
    )
    cleaned = strip_tenure_auditor_tags(text)
    assert "MANUAL FILL" not in cleaned
    assert "13 years" in cleaned
