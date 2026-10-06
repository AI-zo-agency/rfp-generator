"""Image-only RFP PDFs: pypdf → OCR cache / vision fallback."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import pdf_ocr
from app.services.pdf_text import IMAGE_ONLY_TEXT_THRESHOLD
from app.services.rfp_content import load_local_rfp_text


def _rfp(**kwargs: object) -> SimpleNamespace:
    defaults = {
        "id": "rfp-ocr-test",
        "title": "Scan RFP",
        "description": "Short meta description only.",
        "pdf_path": "supabase:rfp-ocr-test/rfp.pdf",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class OcrCacheBlobTests(unittest.TestCase):
    def test_round_trip_and_hash_mismatch(self) -> None:
        sha = "a" * 64
        blob = pdf_ocr.format_ocr_cache_blob(sha, "Hello fee schedule")
        self.assertEqual(
            pdf_ocr.parse_ocr_cache_blob(blob, expected_sha256=sha),
            "Hello fee schedule",
        )
        self.assertIsNone(
            pdf_ocr.parse_ocr_cache_blob(blob, expected_sha256="b" * 64)
        )


class LoadLocalRfpTextOcrTests(unittest.TestCase):
    def test_pypdf_empty_ocr_returns_text_and_clears_image_only(self) -> None:
        pdf_bytes = b"%PDF-1.4 image-only-stub"
        ocr_body = "Cost Proposal\n" + ("fee schedule line\n" * 20)
        self.assertGreaterEqual(len(ocr_body), IMAGE_ONLY_TEXT_THRESHOLD)

        with (
            patch(
                "app.services.rfp_content.load_rfp_pdf_bytes",
                return_value=pdf_bytes,
            ),
            patch(
                "app.services.rfp_content.extract_pdf_text_from_bytes",
                return_value="",
            ),
            patch(
                "app.services.rfp_content.pdf_page_count",
                return_value=5,
            ),
            patch(
                "app.services.rfp_content.is_image_only_pdf",
                side_effect=[True, False],
            ),
            patch(
                "app.services.pdf_ocr.extract_text_via_ocr",
                return_value=(ocr_body, "ocr"),
            ) as ocr_mock,
        ):
            desc, text, exists, missing, pages, image_only = load_local_rfp_text(
                _rfp()
            )

        self.assertEqual(desc, "Short meta description only.")
        self.assertEqual(text, ocr_body.strip())
        self.assertTrue(exists)
        self.assertFalse(missing)
        self.assertEqual(pages, 5)
        self.assertFalse(image_only)
        ocr_mock.assert_called_once()

    def test_cache_hit_skips_vision_ocr(self) -> None:
        pdf_bytes = b"%PDF-1.4 cached-scan"
        sha = pdf_ocr.pdf_content_sha256(pdf_bytes)
        cached = "Cached cost proposal and fee schedule.\n" + ("x" * 120)

        with (
            patch.object(pdf_ocr.settings, "rfp_ocr_enabled", True),
            patch(
                "app.services.pdf_ocr.load_cached_ocr_text",
                return_value=cached,
            ) as cache_load,
            patch(
                "app.services.pdf_ocr.render_pdf_pages_png",
            ) as render,
            patch(
                "app.services.pdf_ocr._run_coro_sync",
            ) as vision,
            patch(
                "app.services.pdf_ocr.save_ocr_cache_text",
            ) as cache_save,
        ):
            text, source = pdf_ocr.extract_text_via_ocr(
                pdf_bytes, rfp_id="rfp-ocr-test"
            )

        self.assertEqual(text, cached)
        self.assertEqual(source, "cache")
        cache_load.assert_called_once_with("rfp-ocr-test", sha)
        render.assert_not_called()
        vision.assert_not_called()
        cache_save.assert_not_called()

    def test_ocr_miss_runs_vision_and_writes_cache(self) -> None:
        pdf_bytes = b"%PDF-1.4 needs-ocr"
        ocr_body = "Section 7 Cost Proposal / Fee Schedule\n" + ("line\n" * 30)

        with (
            patch.object(pdf_ocr.settings, "rfp_ocr_enabled", True),
            patch(
                "app.services.pdf_ocr.load_cached_ocr_text",
                return_value=None,
            ),
            patch(
                "app.services.pdf_ocr.render_pdf_pages_png",
                return_value=[b"png1", b"png2"],
            ),
            patch(
                "app.services.pdf_ocr._run_coro_sync",
                return_value=ocr_body,
            ) as vision,
            patch(
                "app.services.pdf_ocr.save_ocr_cache_text",
            ) as cache_save,
        ):
            text, source = pdf_ocr.extract_text_via_ocr(
                pdf_bytes, rfp_id="rfp-ocr-test"
            )

        self.assertEqual(text, ocr_body.strip())
        self.assertEqual(source, "ocr")
        vision.assert_called_once()
        cache_save.assert_called_once()
        self.assertEqual(cache_save.call_args.args[0], "rfp-ocr-test")
        self.assertEqual(cache_save.call_args.args[2], ocr_body.strip())


if __name__ == "__main__":
    unittest.main()
