"""User activity feed API — one workspace per request."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.services import proposal_tweaks_store
from app.services.llm_call_context import get_llm_user_email
from app.services.rfp_ledger import get_rfp_ledger_detail, list_people, list_rfp_ledger
from app.services.rfp_opens import record_rfp_open
from app.services.user_activity import WORKSPACES, list_activity

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("/rfps")
def get_rfp_ledger(
    person: str | None = Query(None, description="Only RFPs this person ran, tweaked, opened or deleted"),
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    include_deleted: bool = Query(True),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    return list_rfp_ledger(
        person=person, from_ts=from_ts, to_ts=to_ts, include_deleted=include_deleted, limit=limit
    )


@router.get("/rfps/{rfp_id}")
def get_rfp_ledger_detail_endpoint(
    rfp_id: str, include_opens: bool = Query(False)
) -> dict[str, Any]:
    return get_rfp_ledger_detail(rfp_id, include_opens=include_opens)


@router.get("/people")
def get_people(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
) -> dict[str, Any]:
    return list_people(from_ts=from_ts, to_ts=to_ts)


@router.get("/tweaks/{tweak_id}")
def get_tweak_detail(tweak_id: str) -> dict[str, Any]:
    """The only endpoint that returns before/after text and the typed instruction."""
    tweak = proposal_tweaks_store.get_tweak(tweak_id)
    if tweak is None:
        raise HTTPException(status_code=404, detail="Tweak not found")
    return tweak


class RfpOpenedBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rfp_id: str = Field(alias="rfpId", min_length=1, max_length=200)


@router.post("/rfp-opened")
def post_rfp_opened(body: RfpOpenedBody) -> dict[str, bool]:
    """The RFP page reports that the signed-in person opened it (throttled server-side)."""
    return {"recorded": record_rfp_open(body.rfp_id, get_llm_user_email())}


@router.get("")
def get_activity(
    workspace: str = Query(..., description="rfp | financial | leads"),
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, description="ISO timestamp cursor (older than)"),
    actor: str | None = Query(None, description="Filter by actor email"),
    action: str | None = Query(None, description="Exact action verb"),
    exclude_action: str | None = Query(None, description="Leave out this exact action verb"),
    entity_id: str | None = Query(None),
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
) -> dict[str, Any]:
    ws = (workspace or "").strip().lower()
    if ws not in WORKSPACES:
        raise HTTPException(
            status_code=400,
            detail=f"workspace must be one of: {', '.join(sorted(WORKSPACES))}",
        )
    return list_activity(
        workspace=ws,  # type: ignore[arg-type]
        limit=limit,
        cursor=cursor,
        actor=actor,
        action=action,
        exclude_action=exclude_action,
        entity_id=entity_id,
        from_ts=from_ts,
        to_ts=to_ts,
    )
