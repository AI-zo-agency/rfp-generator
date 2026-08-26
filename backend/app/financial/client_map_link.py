"""Deterministic and AI-assisted client-map linking."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

from app.core.config import settings
from app.financial import client_map_repository as repo
from app.financial import qb_repository
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


def apply_exact_links(
    clients: list[dict[str, Any]],
    qb: list[dict[str, Any]],
    tw: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return exact normalized-name updates without mutating source rows."""
    updates: list[dict[str, Any]] = []
    for client in clients:
        if client.get("link_confidence") == "confirmed" or client.get("is_internal"):
            continue

        source_names = [
            client.get("client_name"),
            *(client.get("teamwork_company_names") or []),
        ]
        normalized = {
            normalize_name(str(name))
            for name in source_names
            if name and normalize_name(str(name))
        }

        qb_matches: dict[str, dict[str, Any]] = {}
        for customer in qb:
            qbo_id = customer.get("qbo_id")
            if (
                qbo_id is not None
                and normalize_name(str(customer.get("display_name") or "")) in normalized
            ):
                qb_matches[str(qbo_id)] = customer

        tw_matches = [
            company
            for company in tw
            if normalize_name(str(company.get("name") or "")) in normalized
        ]
        exact_qb = list(qb_matches.values()) if len(qb_matches) == 1 else []
        if not exact_qb and not tw_matches:
            continue

        update = {
            "id": client["id"],
            "qb_customer_ids": _merge(
                client.get("qb_customer_ids") or [],
                [str(row["qbo_id"]) for row in exact_qb],
            ),
            "qb_customer_names": _merge(
                client.get("qb_customer_names") or [],
                [row.get("display_name") for row in exact_qb],
            ),
            "teamwork_company_ids": _merge(
                client.get("teamwork_company_ids") or [],
                [row.get("id") for row in tw_matches],
            ),
            "teamwork_company_names": _merge(
                client.get("teamwork_company_names") or [],
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


def _teamwork_companies() -> list[dict[str, Any]]:
    companies: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for project in overview_from_cache().get("projects") or []:
        company_id = project.get("company_id")
        company_name = str(project.get("company_name") or "").strip()
        if company_id is None and not company_name:
            continue
        key = (str(company_id) if company_id is not None else "", normalize_name(company_name))
        if key in seen:
            continue
        seen.add(key)
        companies.append({"id": company_id, "name": company_name})
    return companies


def _persist(updates: list[dict[str, Any]]) -> None:
    for update in updates:
        repo.update_client_map(
            str(update["id"]),
            {key: value for key, value in update.items() if key != "id"},
        )


async def run_link(*, include_ai: bool = True) -> dict[str, int]:
    """Run exact linking, followed by validated light-model suggestions."""
    clients = repo.list_client_map()
    qb = qb_repository.list_customers(settings.quickbooks_realm_id)
    tw = _teamwork_companies()

    exact_updates = apply_exact_links(clients, qb, tw)
    _persist(exact_updates)
    counts = {"confirmed": len(exact_updates), "suggested": 0}
    logger.info(
        "operation=client_map_link_exact clients=%s qb=%s teamwork=%s confirmed=%s",
        len(clients),
        len(qb),
        len(tw),
        counts["confirmed"],
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
    unmatched_qb = [
        row for row in qb if str(row.get("qbo_id")) not in used_qb_ids
    ]
    unmatched_tw = [
        row
        for row in tw
        if row.get("id") is None or str(row.get("id")) not in used_tw_ids
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
