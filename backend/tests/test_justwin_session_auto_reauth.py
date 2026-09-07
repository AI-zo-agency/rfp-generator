"""JustWin session auto-recovery — no manual JUSTWIN_SESSION_PATH delete."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.justwin_sync import browser as justwin_browser


class InvalidateSessionTests(unittest.TestCase):
    def test_invalidate_deletes_existing_session_file(self) -> None:
        with patch.object(justwin_browser, "session_path") as sp:
            path = Path(self.id().replace(".", "_") + "_session.json")
            path.write_text('{"cookies":[]}', encoding="utf-8")
            sp.return_value = path
            try:
                self.assertTrue(justwin_browser.invalidate_session())
                self.assertFalse(path.is_file())
            finally:
                if path.is_file():
                    path.unlink()

    def test_invalidate_noop_when_missing(self) -> None:
        with patch.object(justwin_browser, "session_path") as sp:
            sp.return_value = Path("/tmp/does-not-exist-justwin-session.json")
            self.assertFalse(justwin_browser.invalidate_session())


class RunnerAutoAuthMessageTests(unittest.TestCase):
    def test_runner_no_longer_asks_user_to_delete_session(self) -> None:
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "app/services/justwin_sync/runner.py"
        ).read_text(encoding="utf-8")
        self.assertIn("ensure_authenticated_page", src)
        self.assertNotIn("delete JUSTWIN_SESSION_PATH", src)


if __name__ == "__main__":
    unittest.main()
