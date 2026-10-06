"""JustWin S3 PDF download prefers httpx (Playwright body-stream timeouts)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.justwin_sync.api import _download_target_pdf


class DownloadTargetPdfTests(unittest.TestCase):
    def test_uses_httpx_first_and_skips_playwright_on_success(self) -> None:
        page = MagicMock()
        view = MagicMock()
        view.ok = True
        view.json.return_value = {"url": "https://s3.example/rfp.pdf"}
        page.request.get.return_value = view
        client = SimpleNamespace(page=page, headers={})

        with patch(
            "app.services.justwin_sync.api._download_bytes_httpx",
            return_value=b"%PDF-1.4" + b"x" * 600,
        ) as httpx_dl:
            body = _download_target_pdf(client, "target-1")

        self.assertTrue(body.startswith(b"%PDF"))
        httpx_dl.assert_called_once()
        # view endpoint only — no second Playwright GET for the S3 object
        self.assertEqual(page.request.get.call_count, 1)

    def test_falls_back_to_playwright_when_httpx_fails(self) -> None:
        page = MagicMock()
        view = MagicMock()
        view.ok = True
        view.json.return_value = {"url": "https://s3.example/rfp.pdf"}
        pdf = MagicMock()
        pdf.ok = True
        pdf.body.return_value = b"%PDF-1.4" + b"y" * 600
        page.request.get.side_effect = [view, pdf]
        client = SimpleNamespace(page=page, headers={})

        with patch(
            "app.services.justwin_sync.api._download_bytes_httpx",
            side_effect=RuntimeError("network"),
        ):
            body = _download_target_pdf(client, "target-1")

        self.assertTrue(body.startswith(b"%PDF"))
        self.assertEqual(page.request.get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
