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


def test_teamwork_tag_links_attach_by_project_tag():
    from app.financial.client_map_link import apply_teamwork_tag_links

    clients = [
        _client(
            id="eff",
            tag_code="EFF",
            client_name="EverFast Fiber",
            qb_customer_ids=["1049"],
            qb_customer_names=["EverFast Fiber"],
            link_confidence="confirmed",
            link_reason="exact normalized name",
        )
    ]
    projects = [
        {
            "id": "1",
            "name": "EFF 26132 EverFast August Retainer",
            "company_id": None,
            "company_name": "Everfast Fiber Networks LLC",
        }
    ]

    updates = apply_teamwork_tag_links(clients, projects)

    assert len(updates) == 1
    assert updates[0]["teamwork_company_names"] == ["Everfast Fiber Networks LLC"]
    assert updates[0]["qb_customer_ids"] == ["1049"]
    assert updates[0]["link_confidence"] == "confirmed"


def test_teamwork_tag_links_skip_ambiguous_tags():
    from app.financial.client_map_link import apply_teamwork_tag_links

    clients = [
        _client(id="1", tag_code="ATC", client_name="Arctic"),
        _client(id="2", tag_code="ATC", client_name="Anti-Trafficking"),
    ]
    projects = [
        {"name": "ATC 25001 x", "company_id": 9, "company_name": "Arctic Chiller"}
    ]

    assert apply_teamwork_tag_links(clients, projects) == []



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


def test_exact_confirm_replaces_prior_suggestions_with_exact_entities():
    clients = [
        _client(
            qb_customer_ids=["77"],
            qb_customer_names=["Suggested Torrent"],
            teamwork_company_ids=[8],
            teamwork_company_names=["Suggested Torrent"],
            link_confidence="suggested",
        )
    ]
    qb = [{"qbo_id": "55", "display_name": "Torrent Laboratories LLC"}]
    tw = [{"id": 9, "name": "Torrent Laboratories"}]

    updates = apply_exact_links(clients, qb, tw)

    assert updates[0]["qb_customer_ids"] == ["55"]
    assert updates[0]["qb_customer_names"] == ["Torrent Laboratories LLC"]
    assert updates[0]["teamwork_company_ids"] == [9]
    assert updates[0]["teamwork_company_names"] == ["Torrent Laboratories"]


def test_exact_does_not_confirm_from_suggested_teamwork_name():
    clients = [
        _client(
            client_name="Mountain View Heating",
            teamwork_company_names=["Mt. View Heating"],
            link_confidence="suggested",
        )
    ]
    qb = [{"qbo_id": "77", "display_name": "Mt. View Heating"}]

    assert apply_exact_links(clients, qb, []) == []


def test_exact_does_not_reuse_qb_owned_by_confirmed_client():
    clients = [
        _client(),
        _client(
            id="2",
            qb_customer_ids=["55"],
            qb_customer_names=["Torrent Laboratories LLC"],
            link_confidence="confirmed",
        ),
    ]
    qb = [{"qbo_id": "55", "display_name": "Torrent Laboratories LLC"}]

    assert apply_exact_links(clients, qb, []) == []


def test_exact_leaves_duplicate_client_name_batch_unmatched():
    clients = [_client(), _client(id="2")]
    qb = [{"qbo_id": "55", "display_name": "Torrent Laboratories LLC"}]
    tw = [{"id": 9, "name": "Torrent Laboratories"}]

    assert apply_exact_links(clients, qb, tw) == []


def test_exact_never_assigns_duplicate_qb_catalog_id_to_two_clients():
    clients = [_client(), _client(id="2", client_name="Acme")]
    qb = [
        {"qbo_id": "55", "display_name": "Torrent Laboratories LLC"},
        {"qbo_id": "55", "display_name": "Acme LLC"},
    ]

    assert apply_exact_links(clients, qb, []) == []


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

    assert result == {"confirmed": 1, "suggested": 0, "teamwork_tag": 0}
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

    assert asyncio.run(run_link()) == {
        "confirmed": 0,
        "suggested": 0,
        "teamwork_tag": 0,
    }
