"""The Prompts page: list agent prompts, read one, save an edit, reset to the code default.

The platform is used only by admins, so any signed-in user may edit a prompt.
"""

from __future__ import annotations

import logging
from typing import Callable, TypeVar

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.services import prompt_assist, prompt_store
from app.services.user_activity import emit_activity

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/prompts", tags=["prompts"])
T = TypeVar("T")

_UNAVAILABLE = (
    "Prompt storage is unavailable. If this is a new environment, apply the "
    "20261102_agent_prompts.sql migration in Supabase and restart the backend."
)


def _storage(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except prompt_store.PromptNotFound:
        raise HTTPException(status_code=404, detail="Prompt not found.")
    except prompt_store.PromptConflict:
        raise HTTPException(
            status_code=409,
            detail="Someone else saved this prompt after you opened it. Reload it to see their version.",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("prompt storage failed: %s", str(exc)[:200])
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)


def _email(request: Request) -> str:
    return (getattr(request.state, "user_email", "") or "").strip().lower()


def _summary(row: dict) -> dict:
    return {
        "key": row["key"],
        "group": row["group_name"],
        "name": row["name"],
        "description": row["description"],
        "source": row["source"],
        "isEdited": bool(row["is_edited"]),
        "updatedAt": row["updated_at"],
        "updatedBy": row.get("updated_by"),
    }


def _detail(row: dict) -> dict:
    return {**_summary(row), "body": row["body"], "defaultBody": row["default_body"]}


@router.get("")
def list_prompts() -> dict:
    rows = _storage(prompt_store.list_prompts)
    return {"prompts": sorted((_summary(r) for r in rows), key=lambda p: (p["group"], p["name"]))}


@router.get("/{key}")
def get_prompt(key: str) -> dict:
    return _detail(_storage(lambda: prompt_store.fetch_prompt(key)))


class SaveBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    body: str = Field(max_length=prompt_store.MAX_BODY_CHARS)
    expected_updated_at: str | None = Field(default=None, alias="expectedUpdatedAt")
    note: str = Field(default="", max_length=300)


@router.put("/{key}")
def save_prompt(key: str, payload: SaveBody, request: Request) -> dict:
    if not payload.body.strip():
        raise HTTPException(status_code=422, detail="A prompt cannot be empty. Use Reset to restore the default.")
    actor = _email(request) or "unknown"
    row = _storage(
        lambda: prompt_store.save_prompt(
            key,
            payload.body,
            editor=actor,
            expected_updated_at=payload.expected_updated_at,
            note=payload.note.strip(),
        )
    )
    emit_activity(
        workspace="rfp",
        action="prompt.edited",
        summary=f"Edited agent prompt {row['name']}",
        actor_email=actor,
        entity_type="agent_prompt",
        entity_id=key,
        entity_label=row["name"],
    )
    return _detail(row)


@router.get("/{key}/versions")
def prompt_versions(key: str) -> dict:
    """Earlier saved versions, newest first. Empty when history is not set up yet."""
    _storage(lambda: prompt_store.fetch_prompt(key))
    try:
        rows = prompt_store.list_versions(key)
    except Exception as exc:  # noqa: BLE001
        logger.info("prompt versions unavailable for %s: %s", key, str(exc)[:200])
        return {"versions": [], "available": False}
    return {
        "available": True,
        "versions": [
            {
                "id": r["id"],
                "body": r["body"],
                "note": r["note"],
                "updatedBy": r.get("created_by"),
                "updatedAt": r["created_at"],
            }
            for r in rows
        ],
    }


@router.get("/{key}/guide")
async def prompt_guide(key: str) -> dict:
    """Plain-English "what it does / when to change it". Empty strings when the model is unavailable."""
    row = _storage(lambda: prompt_store.fetch_prompt(key))
    try:
        return await prompt_assist.guide(key, row["body"])
    except Exception as exc:  # noqa: BLE001
        logger.info("prompt guide unavailable for %s: %s", key, str(exc)[:200])
        return {"what": "", "when": ""}


class AssistBody(BaseModel):
    instruction: str = Field(max_length=prompt_assist.MAX_INSTRUCTION_CHARS)
    current_body: str = Field(alias="currentBody", max_length=prompt_store.MAX_BODY_CHARS)
    model_config = ConfigDict(populate_by_name=True)


@router.post("/{key}/assist")
async def assist_prompt(key: str, payload: AssistBody) -> dict:
    """Suggest a rewrite from a plain-language request. Nothing is saved."""
    _storage(lambda: prompt_store.fetch_prompt(key))
    try:
        return await prompt_assist.suggest_change(payload.current_body, payload.instruction)
    except prompt_assist.AssistError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("prompt assist failed for %s: %s", key, str(exc)[:200])
        raise HTTPException(status_code=503, detail="The assistant is not available right now. Try again in a moment.")


@router.post("/{key}/reset")
def reset_prompt(key: str, request: Request) -> dict:
    actor = _email(request) or "unknown"
    row = _storage(lambda: prompt_store.reset_prompt(key, editor=actor))
    emit_activity(
        workspace="rfp",
        action="prompt.reset",
        summary=f"Reset agent prompt {row['name']} to its default",
        actor_email=actor,
        entity_type="agent_prompt",
        entity_id=key,
        entity_label=row["name"],
    )
    return _detail(row)
