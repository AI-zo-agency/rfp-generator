from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from app.financial import client_map_repository as repo
from app.financial.client_map_normalize import normalize_name

logger = logging.getLogger(__name__)

_JOB = re.compile(r"^([A-Za-z]{2,4})\s+(\d{5})\b")


@dataclass(frozen=True)
class ClientMatch:
    kind: Literal["match"] = "match"
    client_map_id: str | None = None
    tag_code: str | None = None
    client_name: str | None = None
    qb_customer_ids: list[str] | None = None
    link_confidence: str = "unmatched"
    via: Literal["override", "tag", "company"] = "tag"
    is_internal: bool = False


@dataclass(frozen=True)
class Ambiguous:
    kind: Literal["ambiguous"] = "ambiguous"
    tag_code: str | None = None
    candidates: tuple[str, ...] = ()


def parse_job_key(project_name: str) -> dict[str, str] | None:
    m = _JOB.match((project_name or "").strip())
    if not m:
        return None
    return {"tag": m.group(1).upper(), "job_number": m.group(2)}


def resolve_project(
    site_id: str,
    project_id: int | str,
    project_name: str,
    company_id: int | str | None,
    company_name: str | None,
    *,
    client_rows: list[dict[str, Any]] | None = None,
    overrides_loaded: bool = False,
    override: dict[str, Any] | None = None,
) -> ClientMatch | Ambiguous | None:
    ov = override
    if ov is None and not overrides_loaded:
        ov = repo.get_job_override(site_id, int(project_id))
    if ov:
        logger.info(
            "operation=resolve_project site_id=%s project_id=%s via=override",
            site_id,
            project_id,
        )
        return ClientMatch(
            client_map_id=ov.get("client_map_id"),
            qb_customer_ids=list(ov.get("qb_customer_ids") or []),
            link_confidence=ov.get("link_confidence") or "confirmed",
            via="override",
        )
    rows = client_rows if client_rows is not None else repo.list_client_map()
    key = parse_job_key(project_name)
    if key:
        hits = [r for r in rows if str(r.get("tag_code") or "").upper() == key["tag"]]
        if len(hits) > 1:
            logger.info(
                "operation=resolve_project site_id=%s project_id=%s via=ambiguous tag=%s candidates=%d",
                site_id,
                project_id,
                key["tag"],
                len(hits),
            )
            return Ambiguous(tag_code=key["tag"], candidates=tuple(h["id"] for h in hits))
        if len(hits) == 1:
            r = hits[0]
            logger.info(
                "operation=resolve_project site_id=%s project_id=%s via=tag client_map_id=%s",
                site_id,
                project_id,
                r.get("id"),
            )
            return ClientMatch(
                client_map_id=r["id"],
                tag_code=r.get("tag_code"),
                client_name=r.get("client_name"),
                qb_customer_ids=list(r.get("qb_customer_ids") or []),
                link_confidence=r.get("link_confidence") or "unmatched",
                via="tag",
                is_internal=bool(r.get("is_internal")),
            )
    cid = str(company_id) if company_id is not None else None
    cname_norm = normalize_name(company_name) if (company_name or "").strip() else ""
    for r in rows:
        ids = {str(x) for x in (r.get("teamwork_company_ids") or [])}
        names = {
            normalize_name(str(n))
            for n in (r.get("teamwork_company_names") or [])
            if str(n).strip()
        }
        if (cid and cid in ids) or (cname_norm and cname_norm in names):
            logger.info(
                "operation=resolve_project site_id=%s project_id=%s via=company client_map_id=%s",
                site_id,
                project_id,
                r.get("id"),
            )
            return ClientMatch(
                client_map_id=r["id"],
                tag_code=r.get("tag_code"),
                client_name=r.get("client_name"),
                qb_customer_ids=list(r.get("qb_customer_ids") or []),
                link_confidence=r.get("link_confidence") or "unmatched",
                via="company",
                is_internal=bool(r.get("is_internal")),
            )
    logger.info(
        "operation=resolve_project site_id=%s project_id=%s via=none",
        site_id,
        project_id,
    )
    return None
