import pytest

from app.leads.monid import (
    MonidError,
    billed_usd,
    extract_completed_output,
    result_preview,
    run_cost_usd,
    normalize_company_enrichment,
    normalize_person_enrichment,
    unwrap_pdl_output,
)


def test_normalize_company_enrichment_maps_a_high_confidence_pdl_match():
    result = normalize_company_enrichment(
        {
            "name": "giustina land & timber",
            "display_name": "Giustina Land & Timber",
            "industry": "Paper & Forest Products",
            "industry_v2": "Paper & Forest Products",
            "type": "private",
            "employee_count": 84,
            "size": "51-200",
            "founded": 1952,
            "inferred_revenue": "$10M-$25M",
            "linkedin_url": "linkedin.com/company/giustina",
            "website": "giustinaland.com",
            "headline": "Pacific Northwest timber.",
            "tags": ["forestry", "timber", "oregon"],
            "location": {"locality": "Eugene", "region": "OR"},
            "likelihood": 8,
        },
        "giustinaland.com",
    )

    assert result == {
        "company_name": "Giustina Land & Timber",
        "industry": "Paper & Forest Products",
        "company_type": "private",
        "city": "Eugene",
        "state": "OR",
        "employee_count": 84,
        "employee_band": "51-200",
        "founded": 1952,
        "inferred_revenue": "$10M-$25M",
        "linkedin_url": "linkedin.com/company/giustina",
        "website": "giustinaland.com",
        "what_they_do": "Pacific Northwest timber.",
        "tags": ["forestry", "timber", "oregon"],
        "confidence": "high",
        "basis": "Monid / People Data Labs match for giustinaland.com (likelihood 8/10).",
        "source": "monid-pdl",
        "domain": "giustinaland.com",
    }


def test_extract_completed_output_uses_monid_sync_output():
    assert extract_completed_output(
        {"status": "COMPLETED", "providerResponse": {"httpStatus": 200}, "output": {"name": "Acme"}}
    ) == {"name": "Acme"}


def test_extract_completed_output_reports_a_provider_no_match():
    with pytest.raises(MonidError, match="No records were found"):
        extract_completed_output(
            {
                "status": "COMPLETED",
                "providerResponse": {"httpStatus": 404, "error": {"message": "No records were found matching your request"}},
                "output": None,
            }
        )


def test_run_cost_usd_prefers_price_and_converts_micro_dollars():
    run = {
        "price": {"type": "PER_CALL", "amount": 0.10, "currency": "USD"},
        "billing": {
            "actualCost": {"value": 100000, "unit": "MICRO_DOLLAR", "currency": "USD"},
            "reportedCost": {"value": 100000, "unit": "MICRO_DOLLAR", "currency": "USD"},
        },
        "output": {
            "name": "Acme",
            "industry": "Libraries",
            "employee_count_by_month": {"2024-01": 12},
            "location": {"locality": "Eugene", "region": "OR"},
        },
    }
    assert run_cost_usd(run) == 0.10
    assert billed_usd(run) == 0.10
    preview = result_preview(run["output"])
    assert preview["name"] == "Acme"
    assert preview["location"] == {"locality": "Eugene", "region": "OR"}
    assert "employee_count_by_month" in preview["other_keys"]


def test_unwrap_pdl_output_lifts_person_data_and_likelihood():
    assert unwrap_pdl_output(
        {"status": 200, "likelihood": 9, "data": {"full_name": "Sam King", "job_title": "Buyer"}}
    ) == {"full_name": "Sam King", "job_title": "Buyer", "likelihood": 9}


def test_normalize_person_enrichment_maps_title_role_and_phone():
    result = normalize_person_enrichment(
        {
            "full_name": "Sam King",
            "job_title": "Purchasing Manager",
            "job_title_role": "operations",
            "job_title_levels": ["manager"],
            "job_company_name": "Mt Baker Products",
            "mobile_phone": "+13605550199",
            "linkedin_url": "linkedin.com/in/samking",
            "likelihood": 7,
        },
        "mtbakerproducts.com",
    )
    assert result == {
        "full_name": "Sam King",
        "job_title": "Purchasing Manager",
        "job_title_role": "operations",
        "job_title_levels": "manager",
        "job_company_name": "Mt Baker Products",
        "phone": "+13605550199",
        "linkedin_url": "linkedin.com/in/samking",
        "confidence": "high",
        "basis": "Monid / People Data Labs person match for mtbakerproducts.com (likelihood 7/10).",
        "source": "monid-pdl",
    }
