"""Paginated query and CDC record helpers."""

import urllib.parse
from unittest.mock import patch

from app.financial import quickbooks as qb


def test_query_page_uses_startposition():
    captured = {}

    def fake_get(path: str, _retried: bool = False):
        captured["path"] = path
        return {"QueryResponse": {"Invoice": [{"Id": "1"}]}}

    with patch.object(qb, "_get", side_effect=fake_get):
        rows = qb.query_page("select * from Invoice", "Invoice", startposition=1001)
    assert rows == [{"Id": "1"}]
    assert "startposition 1001" in urllib.parse.unquote(captured["path"])


def test_cdc_records_groups_entities():
    payload = {
        "CDCResponse": [{
            "QueryResponse": [{
                "Invoice": [{"Id": "1"}, {"Id": "2", "status": "Deleted"}],
                "Bill": [{"Id": "9"}],
            }]
        }]
    }
    with patch.object(qb, "_get", return_value=payload):
        records = qb.cdc_records(["Invoice", "Bill"], "2026-08-01T00:00:00-07:00")
    assert len(records["Invoice"]) == 2
    assert len(records["Bill"]) == 1
