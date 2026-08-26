import asyncio

from app.financial import client_map_link
from app.financial.client_map_link import (
    apply_exact_links,
    apply_llm_suggestions,
    run_link,
)
from app.services.llm import LlmError


def _client(**overrides):
    return {
        "id": "1",
        "tag_code": "TOR",
        "client_name": "Torrent Laboratories",
        "qb_customer_ids": [],
        "qb_customer_names": [],
        "teamwork_company_ids": [],
        "teamwork_company_names": [],
        "link_confidence": "unmatched",
        "is_internal": False,
        **overrides,
    }


def test_exact_confirms_normalized_qb_name():
    clients = [_client()]
    qb = [{"qbo_id": "55", "display_name": "Torrent Laboratories LLC"}]
    tw = [{"id": 9, "name": "Torrent Laboratory"}]

    updates = apply_exact_links(clients, qb, tw)

    assert updates[0]["qb_customer_ids"] == ["55"]
    assert updates[0]["link_confidence"] == "confirmed"
    assert updates[0]["id"] == "1"


def test_exact_does_not_overwrite_confirmed():
    clients = [
        _client(
            tag_code="X",
            client_name="Acme",
            qb_customer_ids=["1"],
            qb_customer_names=["Acme"],
            link_confidence="confirmed",
        )
    ]

    assert apply_exact_links(
        clients, [{"qbo_id": "2", "display_name": "Acme"}], []
    ) == []


def test_llm_suggestions_land_suggested_only():
    proposal = {
        "matches": [
            {
                "client_map_id": "1",
                "qb_customer_id": "77",
                "qb_customer_name": "Mt. View Heating",
                "reason": "abbreviation of Mountain View Heating",
            }
        ]
    }
    clients = [_client(tag_code="MVH", client_name="Mountain View Heating")]

    updates = apply_llm_suggestions(clients, proposal, valid_qb_ids={"77"})

    assert updates[0]["link_confidence"] == "suggested"
    assert updates[0]["qb_customer_ids"] == ["77"]
    assert "abbreviation" in (updates[0].get("link_reason") or "")


def test_llm_suggestion_with_unknown_qb_id_is_dropped():
    proposal = {
        "matches": [
            {
                "client_map_id": "1",
                "qb_customer_id": "unknown",
                "qb_customer_name": "Torrent Labs",
                "reason": "shortened name",
            }
        ]
    }

    assert apply_llm_suggestions(
        [_client()], proposal, valid_qb_ids={"55"}
    ) == []


def test_llm_suggestion_for_confirmed_row_is_dropped():
    proposal = {
        "matches": [
            {
                "client_map_id": "1",
                "qb_customer_id": "55",
                "qb_customer_name": "Torrent Labs",
                "reason": "shortened name",
            }
        ]
    }

    assert apply_llm_suggestions(
        [_client(link_confidence="confirmed")],
        proposal,
        valid_qb_ids={"55"},
    ) == []


def test_run_link_persists_exact_updates(monkeypatch):
    updates = []
    monkeypatch.setattr(client_map_link.repo, "list_client_map", lambda: [_client()])
    monkeypatch.setattr(
        client_map_link.repo,
        "update_client_map",
        lambda row_id, patch: updates.append((row_id, patch)),
    )
    monkeypatch.setattr(
        client_map_link.qb_repository,
        "list_customers",
        lambda _realm_id: [
            {"qbo_id": "55", "display_name": "Torrent Laboratories LLC"}
        ],
    )
    monkeypatch.setattr(
        client_map_link,
        "overview_from_cache",
        lambda: {"projects": []},
    )

    result = asyncio.run(run_link(include_ai=False))

    assert result == {"confirmed": 1, "suggested": 0}
    assert updates[0][0] == "1"
    assert updates[0][1]["link_confidence"] == "confirmed"
    assert "id" not in updates[0][1]


def test_run_link_returns_partial_counts_on_llm_error(monkeypatch):
    clients = [_client()]
    monkeypatch.setattr(client_map_link.repo, "list_client_map", lambda: clients)
    monkeypatch.setattr(
        client_map_link.repo, "update_client_map", lambda _row_id, _patch: None
    )
    monkeypatch.setattr(
        client_map_link.qb_repository,
        "list_customers",
        lambda _realm_id: [{"qbo_id": "55", "display_name": "Torrent Labs"}],
    )
    monkeypatch.setattr(
        client_map_link,
        "overview_from_cache",
        lambda: {"projects": []},
    )

    async def fail_llm(*_args, **_kwargs):
        raise LlmError("provider unavailable")

    monkeypatch.setattr(client_map_link, "chat_json", fail_llm)

    assert asyncio.run(run_link()) == {"confirmed": 0, "suggested": 0}
