"""JustWin auth: cookie refresh replaces the old localStorage bearer token."""

from unittest.mock import MagicMock

import pytest

from app.services.justwin_sync.api import JustWinAuthError, create_api_client
from app.services.justwin_sync.browser import _has_session_credential


def _companies(company_id: str = "co-1", *, status: int = 200) -> MagicMock:
    res = MagicMock()
    res.status = status
    res.ok = status == 200
    res.json.return_value = {"results": [{"id": company_id}]}
    return res


def _refresh(token: str = "new", *, status: int = 200) -> MagicMock:
    res = MagicMock()
    res.status = status
    res.ok = status == 200
    res.json.return_value = {"access": token} if status == 200 else {"detail": "no"}
    return res


def test_stored_token_skips_refresh() -> None:
    page = MagicMock()
    page.evaluate.return_value = "legacy"
    page.request.get.return_value = _companies()
    client = create_api_client(page)
    assert client.company_id == "co-1"
    assert client.headers["Authorization"] == "Bearer legacy"
    page.request.post.assert_not_called()


def test_missing_storage_uses_refresh_cookie() -> None:
    page = MagicMock()
    page.evaluate.return_value = ""
    page.request.post.return_value = _refresh("fresh")
    page.request.get.return_value = _companies()
    client = create_api_client(page)
    assert client.headers["Authorization"] == "Bearer fresh"
    page.request.post.assert_called_once()


def test_rejected_refresh_is_auth_error() -> None:
    page = MagicMock()
    page.evaluate.return_value = ""
    page.request.post.return_value = _refresh(status=401)
    with pytest.raises(JustWinAuthError, match="refresh token rejected"):
        create_api_client(page)
    page.request.get.assert_not_called()


def test_stale_stored_token_refreshes_once() -> None:
    page = MagicMock()
    page.evaluate.return_value = "stale"
    page.request.post.return_value = _refresh("fresh")
    page.request.get.side_effect = [_companies(status=401), _companies()]
    client = create_api_client(page)
    assert client.headers["Authorization"] == "Bearer fresh"
    assert page.request.get.call_count == 2


def test_session_credential_is_refresh_cookie() -> None:
    page = MagicMock()
    page.context.cookies.return_value = [{"name": "jw_refresh"}]
    assert _has_session_credential(page) is True
    page.evaluate.assert_not_called()
