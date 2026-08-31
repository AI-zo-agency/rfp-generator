"""Supabase client lifecycle — thread-local + HTTP/1.1."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import httpx

from app.services import supabase_db as sb


def test_get_client_is_thread_local(monkeypatch):
    created: list[object] = []

    def fake_create(url, key, options=None):
        client = MagicMock(name=f"client-{len(created)}")
        created.append(client)
        return client

    monkeypatch.setattr(sb, "use_supabase_db", lambda: True)
    monkeypatch.setattr("supabase.create_client", fake_create)
    sb.reset_supabase_client()

    main_client = sb._get_client()

    other: dict[str, object] = {}

    def worker() -> None:
        sb.reset_supabase_client()
        other["client"] = sb._get_client()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert main_client is created[0]
    assert other["client"] is created[1]
    assert main_client is not other["client"]


def test_get_client_disables_http2(monkeypatch):
    captured: dict[str, httpx.Client] = {}

    def fake_create(url, key, options=None):
        captured["options"] = options
        return MagicMock()

    monkeypatch.setattr(sb, "use_supabase_db", lambda: True)
    monkeypatch.setattr("supabase.create_client", fake_create)
    sb.reset_supabase_client()
    sb._get_client()

    http_client = captured["options"].httpx_client
    assert isinstance(http_client, httpx.Client)
    assert http_client._transport._pool._http2 is False  # noqa: SLF001 — assert transport config
