"""URL ingest helpers for one-shot /api/run."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

import server


class FilenameFromUrlTests(unittest.TestCase):
    def test_uses_path_basename(self) -> None:
        self.assertEqual(
            server._filename_from_url("https://buyer.example/docs/RFQ%2013180.pdf"),
            "RFQ 13180.pdf",
        )

    def test_fallback_when_no_pdf_name(self) -> None:
        self.assertEqual(
            server._filename_from_url("https://buyer.example/download?id=1"),
            "rfp.pdf",
        )


class FetchPdfFromUrlTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_non_http(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            await server._fetch_pdf_from_url("file:///tmp/rfp.pdf")
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_accepts_pdf_bytes(self) -> None:
        raw = b"%PDF-1.4 fake"
        resp = MagicMock()
        resp.status_code = 200
        resp.content = raw
        resp.headers = {"content-type": "application/pdf"}
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch.object(server.httpx, "AsyncClient", return_value=client):
            body, name = await server._fetch_pdf_from_url(
                "https://example.com/files/demo.pdf"
            )
        self.assertEqual(body, raw)
        self.assertEqual(name, "demo.pdf")


if __name__ == "__main__":
    unittest.main()
