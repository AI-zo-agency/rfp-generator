"""Stale autosave must not clobber a newer server manuscript."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.api.v1.proposals import _parse_draft_updated_at


class StaleDraftAutosaveGuardTests(unittest.TestCase):
    def test_parse_draft_updated_at_accepts_iso(self) -> None:
        raw = "2026-09-23T13:57:35.743971+00:00"
        parsed = _parse_draft_updated_at(raw)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.year, 2026)

    def test_stale_incoming_timestamp_is_older(self) -> None:
        server = _parse_draft_updated_at("2026-09-23T13:57:35.743971+00:00")
        client = _parse_draft_updated_at("2026-09-23T13:50:00.000000+00:00")
        self.assertIsNotNone(server)
        self.assertIsNotNone(client)
        assert server is not None and client is not None
        self.assertLess(client, server)

    def test_fresh_local_edit_is_newer(self) -> None:
        server = datetime(2026, 9, 23, 13, 57, tzinfo=timezone.utc)
        client = server + timedelta(seconds=30)
        self.assertGreater(client, server)


if __name__ == "__main__":
    unittest.main()
