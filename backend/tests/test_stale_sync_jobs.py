"""Stale JustWin sync jobs must not pin the UI forever."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.services import supabase_db as sb


def test_expire_stale_running_sync_jobs_marks_old_running_failed() -> None:
    old = (datetime.now(timezone.utc) - timedelta(minutes=12)).isoformat()
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"id": "job-old", "started_at": old}]
    )
    # finish_sync_job path
    client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[]
    )

    with patch.object(sb, "_get_client", return_value=client):
        with patch.object(sb, "_handle_response", side_effect=lambda data, context="": data):
            expired = sb.expire_stale_running_sync_jobs(max_age_minutes=8)

    assert expired == ["job-old"]
    # update called to finish the job
    assert client.table.return_value.update.called
