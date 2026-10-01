"""Brand voice standards revisions: list, view, add, set the default.

The platform is used only by admins, so any signed-in user may add and activate a revision.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.services import brand_voice_revisions as bvr
from app.services.user_activity import emit_activity

router = APIRouter(prefix="/brand-voice", tags=["brand-voice"])

_NO_STORE = "Revisions need Supabase, and this environment has none configured."


def _email(request: Request) -> str:
    return (getattr(request.state, "user_email", "") or "").strip().lower()


def _summary(rev: bvr.Revision, active_id: str) -> dict:
    return {
        "id": rev.id,
        "label": rev.label,
        "sha256": rev.sha256,
        "notes": rev.notes,
        "createdBy": rev.created_by,
        "createdAt": rev.created_at,
        "size": len(rev.body.encode("utf-8")),
        "isActive": rev.id == active_id,
    }


@router.get("/revisions")
def list_revisions() -> dict:
    active = bvr.active_revision()
    return {
        "activeId": active.id,
        "enabled": bvr.enabled(),
        "revisions": [_summary(r, active.id) for r in bvr.list_revisions()],
    }


@router.get("/revisions/{revision_id}")
def get_revision(revision_id: str) -> dict:
    rev = bvr.get_revision(revision_id)
    if rev is None:
        raise HTTPException(status_code=404, detail="Revision not found.")
    return {**_summary(rev, bvr.active_revision().id), "body": rev.body}


@router.post("/revisions", status_code=201)
async def add_revision(
    request: Request,
    file: UploadFile = File(...),
    label: str = Form(...),
    notes: str = Form(""),
) -> dict:
    if not bvr.enabled():
        raise HTTPException(status_code=503, detail=_NO_STORE)
    raw = await file.read()
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="The file must be UTF-8 text (.md).")
    actor = _email(request) or "unknown"
    try:
        rev = bvr.add_revision(label=label, body=body, created_by=actor, notes=notes)
    except bvr.DuplicateRevision as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "existingId": exc.existing_id})
    except bvr.RevisionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    emit_activity(
        workspace="rfp",
        action="brand_voice.revision_added",
        summary=f"Added brand voice revision {rev.label}",
        actor_email=actor,
        entity_type="brand_voice_revision",
        entity_id=rev.id,
        entity_label=rev.label,
    )
    return _summary(rev, bvr.active_revision().id)


class SetActiveBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    revision_id: str = Field(alias="revisionId")


@router.put("/active")
def set_active(request: Request, body: SetActiveBody) -> dict:
    if not bvr.enabled():
        raise HTTPException(status_code=503, detail=_NO_STORE)
    actor = _email(request) or "unknown"
    try:
        rev = bvr.set_active(body.revision_id, updated_by=actor)
    except bvr.RevisionError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    emit_activity(
        workspace="rfp",
        action="brand_voice.default_changed",
        summary=f"Set brand voice default to {rev.label}",
        actor_email=actor,
        entity_type="brand_voice_revision",
        entity_id=rev.id,
        entity_label=rev.label,
    )
    return _summary(rev, rev.id)
