"""Deterministic and AI-assisted client-map linking."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

from app.core.config import settings
from app.financial import client_map_repository as repo
from app.financial import qb_repository
from app.financial.client_map import parse_job_key
from app.financial.client_map_normalize import normalize_name
from app.financial.teamwork.teamwork_sync import overview_from_cache
from app.services.llm import LlmError, chat_json

logger = logging.getLogger(__name__)


def _merge(existing: Iterable[Any], additions: Iterable[Any]) -> list[Any]:
    merged = list(existing)
    seen = {str(value) for value in merged}
    for value in additions:
        if value is None or str(value) in seen:
            continue
        merged.append(value)
        seen.add(str(value))
    return merged


def _tw_name_key(name: str | None) -> str:
    return normalize_name(name or "")


def apply_exact_links(
    clients: list[dict[str, Any]],
    qb: list[dict[str, Any]],
    tw: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return exact normalized-name updates without mutating source rows."""
    eligible = [
        client
        for client in clients
        if client.get("link_confidence") != "confirmed"
        and not client.get("is_internal")
    ]
    normalized_by_client = {
        str(client["id"]): normalize_name(str(client.get("client_name") or ""))
        for client in eligible
    }
    confirmed_qb_ids = {
        str(qbo_id)
        for client in clients
        if client.get("link_confidence") == "confirmed"
        for qbo_id in (client.get("qb_customer_ids") or [])
    }
    qb_by_id: dict[str, dict[str, Any]] = {}
    matching_clients_by_qb: dict[str, set[str]] = {}
    for customer in qb:
        qbo_id = customer.get("qbo_id")
        normalized_name = normalize_name(str(customer.get("display_name") or ""))
        if qbo_id is None or str(qbo_id) in confirmed_qb_ids or not normalized_name:
            continue
        qbo_id = str(qbo_id)
        qb_by_id.setdefault(qbo_id, customer)
        matching_clients_by_qb.setdefault(qbo_id, set()).update(
            client_id
            for client_id, client_name in normalized_by_client.items()
            if normalized_name == client_name
        )

    exact_qb_by_client: dict[str, dict[str, dict[str, Any]]] = {}
    ambiguous_client_ids: set[str] = set()
    for qbo_id, matching_clients in matching_clients_by_qb.items():
        if len(matching_clients) == 1:
            client_id = next(iter(matching_clients))
            exact_qb_by_client.setdefault(client_id, {})[qbo_id] = qb_by_id[qbo_id]
        elif len(matching_clients) > 1:
            ambiguous_client_ids.update(matching_clients)

    updates: list[dict[str, Any]] = []
    for client in eligible:
        client_id = str(client["id"])
        if client_id in ambiguous_client_ids:
            continue
        normalized = normalized_by_client[client_id]
        qb_matches = exact_qb_by_client.get(client_id, {})
        tw_matches = [
            company
            for company in tw
            if normalize_name(str(company.get("name") or "")) == normalized
        ]
        exact_qb = list(qb_matches.values()) if len(qb_matches) == 1 else []
        if not exact_qb and not tw_matches:
            continue

        update = {
            "id": client["id"],
            "qb_customer_ids": _merge(
                [],
                [str(row["qbo_id"]) for row in exact_qb],
            ),
            "qb_customer_names": _merge(
                [],
                [row.get("display_name") for row in exact_qb],
            ),
            "teamwork_company_ids": _merge(
                [],
                [row.get("id") for row in tw_matches],
            ),
            "teamwork_company_names": _merge(
                [],
                [row.get("name") for row in tw_matches],
            ),
            "link_confidence": "confirmed",
            "link_reason": "exact normalized name",
        }
        updates.append(update)
    return updates


def apply_llm_suggestions(
    clients: list[dict[str, Any]],
    proposal: dict[str, Any],
    valid_qb_ids: set[str],
    valid_tw_ids: set[Any] | None = None,
) -> list[dict[str, Any]]:
    """Validate an LLM proposal and return suggestion-only updates."""
    eligible = {
        str(row.get("id")): row
        for row in clients
        if row.get("id") is not None
        and row.get("link_confidence") != "confirmed"
        and not row.get("is_internal")
    }
    valid_qb = {str(value) for value in valid_qb_ids}
    valid_tw = (
        {str(value): value for value in valid_tw_ids}
        if valid_tw_ids is not None
        else {}
    )
    pending: dict[str, dict[str, Any]] = {}

    matches = proposal.get("matches")
    if not isinstance(matches, list):
        return []
    for match in matches:
        if not isinstance(match, dict):
            continue
        client_id = str(match.get("client_map_id") or "")
        client = eligible.get(client_id)
        if client is None:
            continue

        raw_qb_id = match.get("qb_customer_id")
        qb_id = str(raw_qb_id) if raw_qb_id is not None else None
        if qb_id is not None and qb_id not in valid_qb:
            continue

        raw_tw_id = match.get("teamwork_company_id")
        tw_id = valid_tw.get(str(raw_tw_id)) if raw_tw_id is not None else None
        if raw_tw_id is not None and tw_id is None:
            continue
        if qb_id is None and tw_id is None:
            continue

        update = pending.setdefault(
            client_id,
            {
                "id": client["id"],
                "qb_customer_ids": list(client.get("qb_customer_ids") or []),
                "qb_customer_names": list(client.get("qb_customer_names") or []),
                "teamwork_company_ids": list(
                    client.get("teamwork_company_ids") or []
                ),
                "teamwork_company_names": list(
                    client.get("teamwork_company_names") or []
                ),
                "link_confidence": "suggested",
                "link_reason": "",
            },
        )
        if qb_id is not None:
            update["qb_customer_ids"] = _merge(
                update["qb_customer_ids"], [qb_id]
            )
            update["qb_customer_names"] = _merge(
                update["qb_customer_names"], [match.get("qb_customer_name")]
            )
        if tw_id is not None:
            update["teamwork_company_ids"] = _merge(
                update["teamwork_company_ids"], [tw_id]
            )
            update["teamwork_company_names"] = _merge(
                update["teamwork_company_names"],
                [match.get("teamwork_company_name")],
            )
        reason = str(match.get("reason") or "").strip()
        if reason:
            update["link_reason"] = reason

    return list(pending.values())


def _teamwork_companies(projects: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    companies: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for project in projects if projects is not None else overview_from_cache().get("projects") or []:
        company_id = project.get("company_id")
        company_name = str(project.get("company_name") or "").strip()
        if company_id is None and not company_name:
            continue
        key = (str(company_id) if company_id is not None else "", _tw_name_key(company_name))
        if key in seen:
            continue
        seen.add(key)
        companies.append({"id": company_id, "name": company_name})
    return companies


def apply_teamwork_tag_links(
    clients: list[dict[str, Any]],
    projects: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach Teamwork companies using the TAG in live project titles.

    Project names look like `HML 24016 …`. That tag is a stronger join than
    company-name equality (`EverFast Fiber` vs `Everfast Fiber Networks LLC`).
    Enriches existing rows; never clears QB links or demotes confidence.
    """
    by_tag: dict[str, list[dict[str, Any]]] = {}
    for row in clients:
        if row.get("is_internal"):
            continue
        tag = str(row.get("tag_code") or "").strip().upper()
        if not tag:
            continue
        by_tag.setdefault(tag, []).append(row)

    pending: dict[str, dict[str, Any]] = {}
    for project in projects:
        key = parse_job_key(str(project.get("name") or ""))
        if not key:
            continue
        hits = by_tag.get(key["tag"]) or []
        if len(hits) != 1:
            continue
        client = hits[0]
        company_name = str(project.get("company_name") or "").strip()
        company_id = project.get("company_id")
        if company_id is None and not company_name:
            continue

        client_id = str(client["id"])
        update = pending.get(client_id)
        if update is None:
            update = {
                "id": client["id"],
                "qb_customer_ids": list(client.get("qb_customer_ids") or []),
                "qb_customer_names": list(client.get("qb_customer_names") or []),
                "teamwork_company_ids": list(client.get("teamwork_company_ids") or []),
                "teamwork_company_names": list(client.get("teamwork_company_names") or []),
                "link_confidence": client.get("link_confidence") or "unmatched",
                "link_reason": client.get("link_reason") or "",
                "_changed": False,
            }
            pending[client_id] = update

        before_ids = list(update["teamwork_company_ids"])
        before_names = list(update["teamwork_company_names"])
        update["teamwork_company_ids"] = _merge(
            update["teamwork_company_ids"], [company_id]
        )
        update["teamwork_company_names"] = _merge(
            update["teamwork_company_names"], [company_name]
        )
        if (
            update["teamwork_company_ids"] == before_ids
            and update["teamwork_company_names"] == before_names
        ):
            continue
        update["_changed"] = True
        if update["link_confidence"] != "confirmed":
            update["link_confidence"] = "confirmed"
            update["link_reason"] = "teamwork project tag"

    out: list[dict[str, Any]] = []
    for update in pending.values():
        if not update.pop("_changed", False):
            continue
        out.append(update)
    return out


def _persist(updates: list[dict[str, Any]]) -> None:
    for update in updates:
        repo.update_client_map(
            str(update["id"]),
            {key: value for key, value in update.items() if key != "id"},
        )


def _used_teamwork_names(clients: list[dict[str, Any]]) -> set[str]:
    return {
        _tw_name_key(name)
        for row in clients
        if row.get("link_confidence") == "confirmed"
        for name in (row.get("teamwork_company_names") or [])
        if _tw_name_key(name)
    }


async def run_link(*, include_ai: bool = True) -> dict[str, int]:
    """Run exact linking, tag-based Teamwork attach, then light-model suggestions."""
    clients = repo.list_client_map()
    qb = qb_repository.list_customers(settings.quickbooks_realm_id)
    projects = list(overview_from_cache().get("projects") or [])
    tw = _teamwork_companies(projects)

    exact_updates = apply_exact_links(clients, qb, tw)
    _persist(exact_updates)
    clients = repo.list_client_map()
    tag_updates = apply_teamwork_tag_links(clients, projects)
    _persist(tag_updates)
    newly_confirmed_via_tag = sum(
        1 for u in tag_updates if u.get("link_reason") == "teamwork project tag"
    )
    counts = {
        "confirmed": len(exact_updates) + newly_confirmed_via_tag,
        "suggested": 0,
        "teamwork_tag": len(tag_updates),
    }
    logger.info(
        "operation=client_map_link_exact clients=%s qb=%s teamwork=%s confirmed=%s tag_links=%s",
        len(clients),
        len(qb),
        len(tw),
        len(exact_updates),
        len(tag_updates),
    )
    if not include_ai:
        return counts

    clients = repo.list_client_map()
    leftovers = [
        row
        for row in clients
        if row.get("link_confidence") in {"unmatched", "suggested", None}
        and not row.get("is_internal")
    ]
    used_qb_ids = {
        str(qbo_id)
        for row in clients
        for qbo_id in (row.get("qb_customer_ids") or [])
    }
    used_tw_ids = {
        str(company_id)
        for row in clients
        for company_id in (row.get("teamwork_company_ids") or [])
    }
    used_tw_names = _used_teamwork_names(clients)
    unmatched_qb = [
        row for row in qb if str(row.get("qbo_id")) not in used_qb_ids
    ]
    unmatched_tw = [
        row
        for row in tw
        if (
            (row.get("id") is None or str(row.get("id")) not in used_tw_ids)
            and _tw_name_key(row.get("name")) not in used_tw_names
        )
    ]
    if not leftovers or not unmatched_qb:
        return counts

    prompt = {
        "clients": [
            {
                "client_map_id": row.get("id"),
                "tag_code": row.get("tag_code"),
                "client_name": row.get("client_name"),
                "teamwork_company_names": row.get("teamwork_company_names") or [],
            }
            for row in leftovers
        ],
        "quickbooks_customers": [
            {
                "qb_customer_id": str(row.get("qbo_id")),
                "qb_customer_name": row.get("display_name"),
            }
            for row in unmatched_qb
        ],
        "teamwork_companies": [
            {
                "teamwork_company_id": row.get("id"),
                "teamwork_company_name": row.get("name"),
            }
            for row in unmatched_tw
        ],
    }
    messages = [
        {
            "role": "system",
            "content": (
                'Return JSON only: {"matches":[{"client_map_id":"...",'
                '"qb_customer_id":"...","qb_customer_name":"...",'
                '"teamwork_company_id":null,"reason":"..."}]}. '
                "Use only IDs supplied below. Never invent IDs. Skip uncertain, "
                "many-to-many, or ambiguous matches."
            ),
        },
        {"role": "user", "content": json.dumps(prompt)},
    ]
    try:
        proposal, provider = await chat_json(
            messages,
            node_name="client_map.link",
            tier="light",
            max_tokens=4096,
            temperature=0.1,
        )
    except LlmError:
        logger.exception(
            "operation=client_map_link_ai status=failed confirmed=%s",
            counts["confirmed"],
        )
        return counts

    suggestions = apply_llm_suggestions(
        clients,
        proposal,
        valid_qb_ids={str(row.get("qbo_id")) for row in unmatched_qb},
        valid_tw_ids={
            row["id"] for row in unmatched_tw if row.get("id") is not None
        },
    )
    _persist(suggestions)
    counts["suggested"] = len(suggestions)
    logger.info(
        "operation=client_map_link_ai status=success provider=%s leftovers=%s suggested=%s",
        provider,
        len(leftovers),
        counts["suggested"],
    )
    return counts
