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
