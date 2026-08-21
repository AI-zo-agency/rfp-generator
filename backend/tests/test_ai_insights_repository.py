"""Tests for ai_insights_repository error handling and upsert logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.financial.ai_insights_repository import get_latest_insight, upsert_insight


class TestUpsertInsight:
    """Test upsert_insight error truncation and on_conflict parameter."""

    def test_error_empty_string_becomes_none(self):
        """Empty error string should be converted to None in payload."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.upsert.return_value = mock_table
        mock_table.execute.return_value = MagicMock()

        with patch("app.financial.ai_insights_repository._get_client", return_value=mock_client):
            upsert_insight(
                source="quickbooks",
                scope_key="test_realm",
                as_of="2026-08-21",
                payload={"brief": "test"},
                evidence={},
                provider="anthropic",
                model="claude-3-sonnet",
                status="failed",
                error="",
            )

        # Verify the upsert was called
        mock_client.table.assert_called_once_with("ai_insights")
        # Extract the payload argument
        upsert_call_args = mock_table.upsert.call_args
        payload = upsert_call_args[0][0]
        assert payload["error"] is None

    def test_error_truncates_to_500_chars(self):
        """Error longer than 500 chars should be truncated to exactly 500."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.upsert.return_value = mock_table
        mock_table.execute.return_value = MagicMock()

        long_error = "x" * 600

        with patch("app.financial.ai_insights_repository._get_client", return_value=mock_client):
            upsert_insight(
                source="quickbooks",
                scope_key="test_realm",
                as_of="2026-08-21",
                payload={"brief": "test"},
                evidence={},
                provider="anthropic",
                model="claude-3-sonnet",
                status="failed",
                error=long_error,
            )

        upsert_call_args = mock_table.upsert.call_args
        payload = upsert_call_args[0][0]
        assert len(payload["error"]) == 500
        assert payload["error"] == "x" * 500

    def test_error_none_stays_none(self):
        """None error should stay None in payload."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.upsert.return_value = mock_table
        mock_table.execute.return_value = MagicMock()

        with patch("app.financial.ai_insights_repository._get_client", return_value=mock_client):
            upsert_insight(
                source="quickbooks",
                scope_key="test_realm",
                as_of="2026-08-21",
                payload={"brief": "test"},
                evidence={},
                provider="anthropic",
                model="claude-3-sonnet",
                status="ok",
                error=None,
            )

        upsert_call_args = mock_table.upsert.call_args
        payload = upsert_call_args[0][0]
        assert payload["error"] is None

    def test_on_conflict_parameter_matches_unique_constraint(self):
        """on_conflict parameter must match SQL UNIQUE constraint exactly."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.upsert.return_value = mock_table
        mock_table.execute.return_value = MagicMock()

        with patch("app.financial.ai_insights_repository._get_client", return_value=mock_client):
            upsert_insight(
                source="quickbooks",
                scope_key="test_realm",
                as_of="2026-08-21",
                payload={"brief": "test"},
                evidence={},
                provider="anthropic",
                model="claude-3-sonnet",
                status="ok",
            )

        # Verify the upsert was called with correct on_conflict
        upsert_call_args = mock_table.upsert.call_args
        assert upsert_call_args[1]["on_conflict"] == "source,scope_key,as_of"


class TestGetLatestInsight:
    """Test get_latest_insight status filter and ordering."""

    def test_filters_to_status_ok_only(self):
        """get_latest_insight should only return rows with status='ok'."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        # Chain the fluent API calls
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_eq1 = MagicMock()
        mock_select.eq.return_value = mock_eq1
        mock_eq2 = MagicMock()
        mock_eq1.eq.return_value = mock_eq2
        mock_eq3 = MagicMock()
        mock_eq2.eq.return_value = mock_eq3
        mock_order = MagicMock()
        mock_eq3.order.return_value = mock_order
        mock_limit = MagicMock()
        mock_order.limit.return_value = mock_limit
        mock_limit.execute.return_value = MagicMock(data=[])

        with patch("app.financial.ai_insights_repository._get_client", return_value=mock_client):
            get_latest_insight("quickbooks", "test_realm")

        # Verify the query chain
        mock_table.select.assert_called_once_with("*")
        # Count the eq calls: should be 3 (source, scope_key, status)
        eq_calls = mock_select.eq.call_args_list
        assert len([c for c in mock_eq1.eq.call_args_list + mock_eq2.eq.call_args_list]) >= 1
        # Verify status filter is set to 'ok'
        all_eq_calls = (
            mock_select.eq.call_args_list
            + mock_eq1.eq.call_args_list
            + mock_eq2.eq.call_args_list
        )
        status_calls = [c for c in all_eq_calls if c[0][0] == "status"]
        assert len(status_calls) > 0
        assert status_calls[0][0][1] == "ok"

    def test_orders_by_as_of_descending(self):
        """get_latest_insight should order by as_of DESC to get newest first."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        # Chain the fluent API calls
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_eq1 = MagicMock()
        mock_select.eq.return_value = mock_eq1
        mock_eq2 = MagicMock()
        mock_eq1.eq.return_value = mock_eq2
        mock_eq3 = MagicMock()
        mock_eq2.eq.return_value = mock_eq3
        mock_order = MagicMock()
        mock_eq3.order.return_value = mock_order
        mock_limit = MagicMock()
        mock_order.limit.return_value = mock_limit
        mock_limit.execute.return_value = MagicMock(data=[])

        with patch("app.financial.ai_insights_repository._get_client", return_value=mock_client):
            get_latest_insight("quickbooks", "test_realm")

        # Verify order was called with as_of and desc=True on the eq3 result
        mock_eq3.order.assert_called_once_with("as_of", desc=True)

    def test_returns_none_when_no_rows(self):
        """get_latest_insight should return None when no rows match."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        # Chain the fluent API calls
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_eq1 = MagicMock()
        mock_select.eq.return_value = mock_eq1
        mock_eq2 = MagicMock()
        mock_eq1.eq.return_value = mock_eq2
        mock_eq3 = MagicMock()
        mock_eq2.eq.return_value = mock_eq3
        mock_order = MagicMock()
        mock_eq3.order.return_value = mock_order
        mock_limit = MagicMock()
        mock_order.limit.return_value = mock_limit
        mock_limit.execute.return_value = MagicMock(data=[])

        with patch("app.financial.ai_insights_repository._get_client", return_value=mock_client):
            result = get_latest_insight("quickbooks", "test_realm")

        assert result is None

    def test_returns_first_row_when_found(self):
        """get_latest_insight should return the first row when data exists."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        # Chain the fluent API calls
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_eq1 = MagicMock()
        mock_select.eq.return_value = mock_eq1
        mock_eq2 = MagicMock()
        mock_eq1.eq.return_value = mock_eq2
        mock_eq3 = MagicMock()
        mock_eq2.eq.return_value = mock_eq3
        mock_order = MagicMock()
        mock_eq3.order.return_value = mock_order
        mock_limit = MagicMock()
        mock_order.limit.return_value = mock_limit
        mock_row = {"id": "123", "payload": {"brief": "test"}}
        mock_limit.execute.return_value = MagicMock(data=[mock_row])

        with patch("app.financial.ai_insights_repository._get_client", return_value=mock_client):
            result = get_latest_insight("quickbooks", "test_realm")

        assert result == mock_row
