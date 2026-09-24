"""User activity feed API — one workspace per request."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services.user_activity import WORKSPACES, list_activity

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("")
def get_activity(
    workspace: str = Query(..., description="rfp | financial | leads"),
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, description="ISO timestamp cursor (older than)"),
    actor: str | None = Query(None, description="Filter by actor email"),
    action: str | None = Query(None, description="Exact action verb"),
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
        entity_id=entity_id,
        from_ts=from_ts,
        to_ts=to_ts,
    )
