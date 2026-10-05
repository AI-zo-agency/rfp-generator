from app.leads import hubspot


def test_deal_row_maps_core_fields():
    raw = {
        "id": "99",
        "updatedAt": "2026-10-01T12:00:00.000Z",
        "properties": {
            "dealname": "Speedee Delivery",
            "amount": "10000",
            "dealstage": "3342914252",
            "pipeline": "default",
            "closedate": "2026-10-01T00:00:00.000Z",
            "hs_is_closed": "false",
            "hs_is_closed_won": "false",
            "hs_is_closed_lost": "false",
            "hs_deal_stage_probability": "0.5",
            "hubspot_owner_id": "1",
        },
        "associations": {
            "companies": {"results": [{"id": "55", "type": "deal_to_company"}]}
        },
    }
    row = hubspot.deal_row(raw, owners={"1": "Sonja"}, synced_at="2026-10-05T00:00:00+00:00")
    assert row["hs_id"] == 99
    assert row["dealname"] == "Speedee Delivery"
    assert float(row["amount"]) == 10000.0
    assert row["company_hs_id"] == 55
    assert float(row["stage_probability"]) == 0.5
    assert row["hs_is_closed"] is False
    assert row["owner_name"] == "Sonja"


def test_stage_rows_from_pipeline_payload():
    pipe = {
        "id": "default",
        "label": "New Clients and scope expansions",
        "stages": [
            {
                "id": "a",
                "label": "Contact made",
                "displayOrder": 0,
                "metadata": {"probability": "0.1", "isClosed": "false"},
            },
            {
                "id": "b",
                "label": "Closed won",
                "displayOrder": 1,
                "metadata": {"probability": "1.0", "isClosed": "true"},
            },
        ],
    }
    rows = hubspot.stage_rows(pipe, synced_at="2026-10-05T00:00:00+00:00")
    assert len(rows) == 2
    assert rows[0]["probability"] == 0.1
    assert rows[0]["pipeline_id"] == "default"
    assert rows[1]["is_closed"] is True
    assert rows[1]["stage_label"] == "Closed won"
