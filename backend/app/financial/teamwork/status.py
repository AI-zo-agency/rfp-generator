"""Safe, read-only Teamwork connection health probe."""

from __future__ import annotations

from app.core.config import settings
from app.financial.teamwork import client
from app.financial.teamwork.errors import NOT_CONFIGURED_MESSAGE, TeamworkAuthError, TeamworkError


def connection_status() -> dict[str, object]:
    if not settings.teamwork_configured:
        return {
            "connected": False,
            "base_url": None,
            "reason": NOT_CONFIGURED_MESSAGE,
        }
    try:
        client.request_json(client.PROJECTS_PATH, params={"pageSize": 1, "skipCounts": True})
        return {"connected": True, "base_url": client.origin(), "reason": None}
    except TeamworkAuthError as exc:
        return {"connected": False, "base_url": client.origin(), "reason": str(exc)}
    except TeamworkError as exc:
        return {"connected": False, "base_url": client.origin(), "reason": str(exc)}
