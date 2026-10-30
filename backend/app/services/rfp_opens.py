"""Who opened which RFP: one throttled activity row per person per RFP.

Called from the RFP page in the browser (never from prefetch or API reads).
Failures return False — recording an open must never break the page.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.services.llm_call_context import normalize_user_email
from app.services.rfp_repository import get_rfp, rfp_exists
from app.services.user_activity import OPEN_ACTION, emit_activity, list_activity

logger = logging.getLogger(__name__)

OPEN_THROTTLE = timedelta(minutes=30)

__all__ = ["OPEN_ACTION", "OPEN_THROTTLE", "record_rfp_open"]


def _parse_ts(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def record_rfp_open(rfp_id: str, actor_email: str, *, now: datetime | None = None) -> bool:
    """Write an `rfp.opened` row unless this person opened this RFP within 30 minutes."""
    rid = (rfp_id or "").strip()
    email = normalize_user_email(actor_email)
    if not rid or not email:
        return False
    try:
        if not rfp_exists(rid):
            return False
        moment = now or datetime.now(timezone.utc)
        latest = list_activity(
            workspace="rfp",
            limit=1,
            actor=email,
            action=OPEN_ACTION,
            entity_id=rid,
            with_stats=False,
        )["items"]
        if latest:
            last = _parse_ts(latest[0]["created_at"])
            if last is not None and last > moment - OPEN_THROTTLE:
                return False
        rfp = get_rfp(rid)
        title = ((rfp.title if rfp else None) or rid).strip()
        return (
            emit_activity(
                workspace="rfp",
                action=OPEN_ACTION,
                summary=f"Opened RFP “{title[:80]}”",
                actor_email=email,
                entity_type="rfp",
                entity_id=rid,
                entity_label=title[:300],
            )
            is not None
        )
    except Exception:  # noqa: BLE001 — never break the page
        logger.exception("rfp_opens: failed to record open of %s", rid)
        return False
