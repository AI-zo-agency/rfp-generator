"""JustWin RFP sync via Playwright (Python) — used by /sync-jobs/trigger."""

from app.services.justwin_sync.runner import run_justwin_sync

__all__ = ["run_justwin_sync"]
