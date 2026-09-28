"""Product analytics ingest + summary APIs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.llm_call_context import get_llm_user_email
from app.services.user_analytics import WORKSPACES, ingest_events, summarize

router = APIRouter(prefix="/analytics", tags=["analytics"])


class AnalyticsIngestBody(BaseModel):
    workspace: str
    events: list[dict[str, Any]] = Field(default_factory=list)
    actor_email: str | None = None


@router.post("/ingest")
def analytics_ingest(body: AnalyticsIngestBody) -> dict[str, Any]:
    ws = (body.workspace or "").strip().lower()
    if ws not in WORKSPACES:
        raise HTTPException(
            status_code=400,
            detail=f"workspace must be one of: {', '.join(sorted(WORKSPACES))}",
        )
    return ingest_events(
        workspace=ws,
        events=body.events,
        actor_email=body.actor_email or get_llm_user_email() or None,
    )


@router.get("/summary")
def analytics_summary(
    workspace: str = Query(...),
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    actor: str | None = Query(None),
) -> dict[str, Any]:
    ws = (workspace or "").strip().lower()
    if ws not in WORKSPACES:
        raise HTTPException(
            status_code=400,
            detail=f"workspace must be one of: {', '.join(sorted(WORKSPACES))}",
        )
    return summarize(workspace=ws, from_ts=from_ts, to_ts=to_ts, actor=actor)
