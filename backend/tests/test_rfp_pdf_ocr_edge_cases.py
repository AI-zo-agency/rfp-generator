"""Edge cases for RFP PDF text pipeline: pypdf → OCR cache → OpenRouter vision.

Covers the 41-case matrix (A1–G41). OpenRouter is mocked unless marked live_ocr.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pymupdf
import pytest

from app.services import pdf_ocr
from app.services import rfp_storage
from app.services.go_no_go_service import (
    RfpContentInfo,
    _assess_rfp_content,
    _default_clarifying_questions,
    _needs_input_summary,
)
from app.services.llm import LlmError
from app.services.pdf_text import (
    IMAGE_ONLY_TEXT_THRESHOLD,
    extract_pdf_text_from_bytes,
    is_image_only_pdf,
    pdf_page_count,
)
from app.services.proposal_intelligence.schemas import (
    PlanAmbiguity,
    ProposalExecutionPlan,
    WritingIntelligence,
)
from app.services.proposal_submission_authority import phase35_budget_gate
from app.services.rfp_content import combine_rfp_text, load_local_rfp_text

WYOMING_PDF = Path(
    "/Users/princepatel/.cursor/projects/Users-princepatel-Projects-zo-agency/"
    "attachments/f4ab76cc-84e1-40d4-85df-be75b7c7862e/SMB_-_Wyoming.pdf"
)


def _rfp(**kwargs: object) -> SimpleNamespace:
    defaults = {
        "id": "rfp-ocr-edge",
        "title": "Edge RFP",
        "client": "Town of Test",
        "description": "Metadata description for the RFP.",
        "pdf_path": "supabase:rfp-ocr-edge/rfp.pdf",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _text_pdf(*, pages: int = 1, text: str = "Cost Proposal Fee Schedule") -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"{text} page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def _image_only_pdf(*, pages: int = 1, label: str = "SCAN Cost Proposal") -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        tmp = pymupdf.open()
        p = tmp.new_page(width=400, height=400)
        p.insert_text((40, 80), f"{label} page {i + 1}")
        pix = p.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        tmp.close()
        page = doc.new_page(width=400, height=400)
        page.insert_image(page.rect, stream=pix.tobytes("png"))
    data = doc.tobytes()
    doc.close()
    return data


def _hybrid_tiny_text_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "x")
    tmp = pymupdf.open()
    tp = tmp.new_page()
    tp.insert_text((40, 80), "Hidden Cost Proposal Fee Schedule " * 8)
    pix = tp.get_pixmap()
    tmp.close()
    page.insert_image(pymupdf.Rect(50, 100, 500, 700), stream=pix.tobytes("png"))
    data = doc.tobytes()
    doc.close()
    return data


def _encrypted_pdf() -> bytes:
    src = pymupdf.open()
    page = src.new_page()
    page.insert_text((72, 72), "Secret Cost Proposal content")
    buf = BytesIO()
    src.save(
        buf,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        owner_pw="owner",
    )
    src.close()
    return buf.getvalue()


def _long_ocr(n: int = 150) -> str:
    return ("Cost Proposal line.\n" * ((n // 20) + 1))[:n]


def _native_text_pdf() -> bytes:
    return _text_pdf(text="Cost Proposal Fee Schedule " + ("word " * 40))


# ---------------------------------------------------------------------------
# A. Input / PDF shape
# ---------------------------------------------------------------------------


class TestA_InputPdfShape:
    def test_A1_native_text_pdf_skips_ocr(self) -> None:
        pdf = _native_text_pdf()
        assert not is_image_only_pdf(pdf)
        with (
            patch("app.services.rfp_content.load_rfp_pdf_bytes", return_value=pdf),
            patch("app.services.pdf_ocr.extract_text_via_ocr") as ocr,
        ):
            _d, out, exists, missing, pages, image_only = load_local_rfp_text(_rfp())
        assert exists and not missing and pages >= 1
        assert not image_only
        assert "Cost Proposal" in out
        ocr.assert_not_called()

    def test_A2_pure_image_scan_triggers_ocr(self) -> None:
        pdf = _image_only_pdf(pages=2)
        assert extract_pdf_text_from_bytes(pdf) == ""
        assert is_image_only_pdf(pdf)
        ocr_body = _long_ocr(200)
        with (
            patch("app.services.rfp_content.load_rfp_pdf_bytes", return_value=pdf),
            patch(
                "app.services.pdf_ocr.extract_text_via_ocr",
                return_value=(ocr_body, "ocr"),
            ) as ocr,
        ):
            _d, out, _e, _m, pages, image_only = load_local_rfp_text(_rfp())
        assert pages == 2
        assert out == ocr_body.strip()
        assert not image_only
        ocr.assert_called_once()

    def test_A3_hybrid_tiny_text_still_image_only_then_ocr(self) -> None:
        pdf = _hybrid_tiny_text_pdf()
        text = extract_pdf_text_from_bytes(pdf)
        assert len(text.strip()) < IMAGE_ONLY_TEXT_THRESHOLD
        assert is_image_only_pdf(pdf, extracted_text=text)
        with (
            patch("app.services.rfp_content.load_rfp_pdf_bytes", return_value=pdf),
            patch(
                "app.services.pdf_ocr.extract_text_via_ocr",
                return_value=(_long_ocr(200), "ocr"),
            ) as ocr,
        ):
            _d, out, _e, _m, _p, image_only = load_local_rfp_text(_rfp())
        ocr.assert_called_once()
        assert not image_only
        assert "Cost Proposal" in out

    def test_A4_corrupt_and_non_pdf(self) -> None:
        assert extract_pdf_text_from_bytes(b"") == ""
        assert extract_pdf_text_from_bytes(b"not-a-pdf") == ""
        assert extract_pdf_text_from_bytes(b"%PDF-1.4 truncated") == ""
        assert pdf_page_count(b"not-a-pdf") == 0
        assert pdf_ocr.render_pdf_pages_png(b"not-a-pdf") == []
        assert pdf_ocr.render_pdf_pages_png(b"%PDF-1.4 bad") == []

    def test_A5_password_protected_does_not_crash(self) -> None:
        enc = _encrypted_pdf()
        assert extract_pdf_text_from_bytes(enc) == ""
        assert pdf_ocr.render_pdf_pages_png(enc) == []
        with patch("app.services.rfp_content.load_rfp_pdf_bytes", return_value=enc):
            _d, text, exists, _missing, pages, _image_only = load_local_rfp_text(_rfp())
        assert exists
        assert text == ""
        assert pages >= 0

    def test_A6_page_cap_honored(self) -> None:
        pdf = _image_only_pdf(pages=5)
        with patch.object(pdf_ocr.settings, "rfp_ocr_max_pages", 2):
            pages = pdf_ocr.render_pdf_pages_png(pdf)
        assert len(pages) == 2

    def test_A7_large_png_payload_still_builds_message(self) -> None:
        big = b"\x89PNG" + (b"x" * 50_000)
        with patch(
            "app.services.llm.chat_text_vision",
            new_callable=AsyncMock,
            return_value=(_long_ocr(200), "openrouter"),
        ) as vision:
            text = asyncio.run(
                pdf_ocr.ocr_pdf_pages_via_vision([big, big], rfp_id="edge-a7")
            )
        assert len(text) >= IMAGE_ONLY_TEXT_THRESHOLD
        vision.assert_awaited_once()
        messages = vision.await_args.args[0]
        parts = messages[0]["content"]
        image_parts = [p for p in parts if p.get("type") == "image_url"]
        assert len(image_parts) == 2

    def test_A8_rotated_scan_render_no_crash(self) -> None:
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 200), "Rotated Cost Proposal Fee Schedule " * 5)
        page.set_rotation(90)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        out = pymupdf.open()
        op = out.new_page(width=page.rect.width, height=page.rect.height)
        op.insert_image(op.rect, stream=pix.tobytes("png"))
        data = out.tobytes()
        doc.close()
        out.close()
        rendered = pdf_ocr.render_pdf_pages_png(data)
        assert len(rendered) == 1

    def test_A9_thin_ocr_keeps_image_only(self) -> None:
        pdf = _image_only_pdf()
        with (
            patch("app.services.rfp_content.load_rfp_pdf_bytes", return_value=pdf),
            patch(
                "app.services.pdf_ocr.extract_text_via_ocr",
                return_value=("", ""),
            ),
        ):
            _d, text, _e, _m, _p, image_only = load_local_rfp_text(_rfp())
        assert text == ""
        assert image_only is True


# ---------------------------------------------------------------------------
# B. Loader gate
# ---------------------------------------------------------------------------


class TestB_LoaderGate:
    def test_B10_missing_pdf(self) -> None:
        with patch("app.services.rfp_content.load_rfp_pdf_bytes", return_value=None):
            _d, text, exists, missing, pages, image_only = load_local_rfp_text(
                _rfp(pdf_path="supabase:rfp-ocr-edge/rfp.pdf")
            )
        assert not exists
        assert missing
        assert text == ""
        assert pages == 0
        assert image_only is False

    def test_B11_description_only_no_pdf_path(self) -> None:
        with (
            patch("app.services.rfp_content.load_rfp_pdf_bytes", return_value=None),
            patch("app.services.rfp_content.get_rfp_pdf_path", return_value=None),
        ):
            desc, text, exists, missing, _pages, image_only = load_local_rfp_text(
                _rfp(pdf_path=None, description="Scope only description.")
            )
        assert desc.startswith("Scope only")
        assert text == ""
        assert not exists
        assert not missing
        assert image_only is False

    def test_B12_ocr_disabled(self) -> None:
        pdf = _image_only_pdf()
        with (
            patch.object(pdf_ocr.settings, "rfp_ocr_enabled", False),
            patch("app.services.pdf_ocr.load_cached_ocr_text") as cache,
            patch("app.services.pdf_ocr.render_pdf_pages_png") as render,
        ):
            text, source = pdf_ocr.extract_text_via_ocr(pdf, rfp_id="edge")
        assert text == "" and source == ""
        cache.assert_not_called()
        render.assert_not_called()

    def test_B13_ocr_unexpected_exception_swallowed(self) -> None:
        pdf = _image_only_pdf()
        with (
            patch("app.services.rfp_content.load_rfp_pdf_bytes", return_value=pdf),
            patch(
                "app.services.pdf_ocr.extract_text_via_ocr",
                side_effect=RuntimeError("boom"),
            ),
        ):
            _d, text, exists, _m, pages, image_only = load_local_rfp_text(_rfp())
        assert exists and pages >= 1
        assert text == ""
        assert image_only is True

    def test_B14_ocr_short_text_rejected(self) -> None:
        pdf = b"%PDF-1.4 scan"
        with (
            patch.object(pdf_ocr.settings, "rfp_ocr_enabled", True),
            patch("app.services.pdf_ocr.load_cached_ocr_text", return_value=None),
            patch("app.services.pdf_ocr.render_pdf_pages_png", return_value=[b"png"]),
            patch("app.services.pdf_ocr._run_coro_sync", return_value="tiny"),
            patch("app.services.pdf_ocr.save_ocr_cache_text") as save,
        ):
            text, source = pdf_ocr.extract_text_via_ocr(pdf, rfp_id="edge")
        assert text == "" and source == ""
        save.assert_not_called()

    def test_B15_threshold_boundary(self) -> None:
        pdf = b"%PDF-1.4 scan"
        exactly = "a" * IMAGE_ONLY_TEXT_THRESHOLD
        below = "a" * (IMAGE_ONLY_TEXT_THRESHOLD - 1)
        with (
            patch.object(pdf_ocr.settings, "rfp_ocr_enabled", True),
            patch("app.services.pdf_ocr.load_cached_ocr_text", return_value=None),
            patch("app.services.pdf_ocr.render_pdf_pages_png", return_value=[b"png"]),
            patch("app.services.pdf_ocr._run_coro_sync", return_value=exactly),
            patch("app.services.pdf_ocr.save_ocr_cache_text") as save,
        ):
            text, source = pdf_ocr.extract_text_via_ocr(pdf, rfp_id="edge")
        assert source == "ocr" and len(text) == IMAGE_ONLY_TEXT_THRESHOLD
        save.assert_called_once()

        with (
            patch.object(pdf_ocr.settings, "rfp_ocr_enabled", True),
            patch("app.services.pdf_ocr.load_cached_ocr_text", return_value=None),
            patch("app.services.pdf_ocr.render_pdf_pages_png", return_value=[b"png"]),
            patch("app.services.pdf_ocr._run_coro_sync", return_value=below),
            patch("app.services.pdf_ocr.save_ocr_cache_text") as save2,
        ):
            text2, source2 = pdf_ocr.extract_text_via_ocr(pdf, rfp_id="edge")
        assert text2 == "" and source2 == ""
        save2.assert_not_called()


# ---------------------------------------------------------------------------
# C. Cache
# ---------------------------------------------------------------------------


class TestC_Cache:
    def test_C16_miss_then_hit_skips_vision(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(rfp_storage.settings, "pdf_storage_path", tmp_path)
        monkeypatch.setattr(rfp_storage, "use_supabase", lambda: False)
        pdf = b"%PDF-1.4 cache-roundtrip"
        body = _long_ocr(180)
        with (
            patch.object(pdf_ocr.settings, "rfp_ocr_enabled", True),
            patch("app.services.pdf_ocr.render_pdf_pages_png", return_value=[b"png"]),
            patch("app.services.pdf_ocr._run_coro_sync", return_value=body) as vision,
        ):
            t1, s1 = pdf_ocr.extract_text_via_ocr(pdf, rfp_id="cache-rfp")
            t2, s2 = pdf_ocr.extract_text_via_ocr(pdf, rfp_id="cache-rfp")
        assert s1 == "ocr" and s2 == "cache"
        assert t1 == body.strip() and t2 == body.strip()
        assert vision.call_count == 1

    def test_C17_hash_mismatch_reocr(self) -> None:
        sha_old = "a" * 64
        blob = pdf_ocr.format_ocr_cache_blob(sha_old, _long_ocr(150))
        assert pdf_ocr.parse_ocr_cache_blob(blob, expected_sha256="b" * 64) is None

    def test_C18_corrupt_cache_header_miss(self) -> None:
        assert (
            pdf_ocr.parse_ocr_cache_blob(b"not-a-header\ntext", expected_sha256="a" * 64)
            is None
        )
        assert pdf_ocr.parse_ocr_cache_blob(b"", expected_sha256="a" * 64) is None

    def test_C19_cache_write_failure_still_returns_text(self) -> None:
        pdf = b"%PDF-1.4 write-fail"
        body = _long_ocr(160)
        with (
            patch.object(pdf_ocr.settings, "rfp_ocr_enabled", True),
            patch("app.services.pdf_ocr.load_cached_ocr_text", return_value=None),
            patch("app.services.pdf_ocr.render_pdf_pages_png", return_value=[b"png"]),
            patch("app.services.pdf_ocr._run_coro_sync", return_value=body),
            patch(
                "app.services.pdf_ocr.save_ocr_cache_text",
                side_effect=RuntimeError("storage down"),
            ),
        ):
            text, source = pdf_ocr.extract_text_via_ocr(pdf, rfp_id="edge")
        assert source == "ocr"
        assert text == body.strip()

    def test_C20_save_pdf_deletes_ocr_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(rfp_storage.settings, "pdf_storage_path", tmp_path)
        monkeypatch.setattr(rfp_storage, "use_supabase", lambda: False)
        rfp_id = "del-cache"
        (tmp_path / rfp_id).mkdir()
        cache_path = tmp_path / rfp_id / "rfp.ocr.txt"
        cache_path.write_text("# sha256=" + ("a" * 64) + "\nhello\n", encoding="utf-8")
        content = b"%PDF-1.4\n" + (b"0" * 600)
        path = rfp_storage.save_rfp_pdf(rfp_id, content)
        assert path
        assert not cache_path.exists()

    def test_C21_local_disk_cache_roundtrip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(rfp_storage.settings, "pdf_storage_path", tmp_path)
        monkeypatch.setattr(rfp_storage, "use_supabase", lambda: False)
        rfp_id = "local-cache"
        sha = "c" * 64
        pdf_ocr.save_ocr_cache_text(rfp_id, sha, "Local disk OCR body " + ("x" * 100))
        loaded = pdf_ocr.load_cached_ocr_text(rfp_id, sha)
        assert loaded is not None
        assert loaded.startswith("Local disk OCR body")
        pdf_ocr.delete_ocr_cache(rfp_id)
        assert pdf_ocr.load_cached_ocr_text(rfp_id, sha) is None


# ---------------------------------------------------------------------------
# D. Render
# ---------------------------------------------------------------------------


class TestD_Render:
    def test_D22_pymupdf_missing(self) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "pymupdf":
                raise ImportError("simulated missing pymupdf")
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=fake_import):
            assert pdf_ocr.render_pdf_pages_png(_image_only_pdf()) == []

    def test_D23_open_failure_returns_empty(self) -> None:
        assert pdf_ocr.render_pdf_pages_png(b"%PDF-1.4\n%EOF") == []

    def test_D24_one_page_render_failure_continues(self) -> None:
        class BoomPage:
            def get_pixmap(self, *a, **k):
                raise RuntimeError("page boom")

        class OkPage:
            def get_pixmap(self, *a, **k):
                m = MagicMock()
                m.tobytes.return_value = b"png-ok"
                return m

        class FakeDoc:
            is_encrypted = False
            page_count = 3

            def load_page(self, i):
                return BoomPage() if i == 1 else OkPage()

            def close(self):
                return None

        with patch("pymupdf.open", return_value=FakeDoc()):
            pages = pdf_ocr.render_pdf_pages_png(b"%PDF-1.4 stub", max_pages=3)
        assert pages == [b"png-ok", b"png-ok"]


# ---------------------------------------------------------------------------
# E. Vision / OpenRouter
# ---------------------------------------------------------------------------


class TestE_Vision:
    def test_E25_invalid_model_returns_empty(self) -> None:
        with patch(
            "app.services.llm.chat_text_vision",
            new_callable=AsyncMock,
            side_effect=LlmError(
                "OpenRouter API error (400): invalid model", status_code=400
            ),
        ):
            text = asyncio.run(
                pdf_ocr.ocr_pdf_pages_via_vision([b"png"], rfp_id="edge")
            )
        assert text == ""

    def test_E26_missing_api_key(self) -> None:
        with patch(
            "app.services.llm.chat_text_vision",
            new_callable=AsyncMock,
            side_effect=LlmError(
                "OPENROUTER_API_KEY required for vision OCR", status_code=503
            ),
        ):
            text = asyncio.run(
                pdf_ocr.ocr_pdf_pages_via_vision([b"png"], rfp_id="edge")
            )
        assert text == ""

    @pytest.mark.parametrize("code", [401, 402, 429])
    def test_E27_openrouter_http_errors(self, code: int) -> None:
        with patch(
            "app.services.llm.chat_text_vision",
            new_callable=AsyncMock,
            side_effect=LlmError(f"OpenRouter API error ({code})", status_code=code),
        ):
            text = asyncio.run(
                pdf_ocr.ocr_pdf_pages_via_vision([b"png"], rfp_id="edge")
            )
        assert text == ""

    def test_E28_empty_model_content(self) -> None:
        with patch(
            "app.services.llm.chat_text_vision",
            new_callable=AsyncMock,
            return_value=("", "openrouter"),
        ):
            text = asyncio.run(
                pdf_ocr.ocr_pdf_pages_via_vision([b"png"], rfp_id="edge")
            )
        assert text == ""

    def test_E29_markdown_fence_stripped(self) -> None:
        fenced = "```text\nCost Proposal Fee Schedule\n" + ("line\n" * 20) + "```"
        with patch(
            "app.services.llm.chat_text_vision",
            new_callable=AsyncMock,
            return_value=(fenced, "openrouter"),
        ):
            text = asyncio.run(
                pdf_ocr.ocr_pdf_pages_via_vision([b"png"], rfp_id="edge")
            )
        assert not text.startswith("```")
        assert "Cost Proposal" in text

    def test_E30_max_chars_truncation(self) -> None:
        huge = "A" * 5_000
        with patch(
            "app.services.llm.chat_text_vision",
            new_callable=AsyncMock,
            return_value=(huge, "openrouter"),
        ):
            text = asyncio.run(
                pdf_ocr.ocr_pdf_pages_via_vision(
                    [b"png"], rfp_id="edge", max_chars=500
                )
            )
        assert len(text) == 500

    def test_E31_timeout_swallowed_by_loader(self) -> None:
        pdf = _image_only_pdf()
        with (
            patch("app.services.rfp_content.load_rfp_pdf_bytes", return_value=pdf),
            patch(
                "app.services.pdf_ocr.extract_text_via_ocr",
                side_effect=concurrent.futures.TimeoutError(),
            ),
        ):
            _d, text, _e, _m, _p, image_only = load_local_rfp_text(_rfp())
        assert text == "" and image_only is True

    def test_E32_sync_and_async_run_coro_branches(self) -> None:
        result = pdf_ocr._run_coro_sync(lambda: asyncio.sleep(0, result="sync-ok"))
        assert result == "sync-ok"

        async def _inside():
            return pdf_ocr._run_coro_sync(lambda: asyncio.sleep(0, result="async-ok"))

        assert asyncio.run(_inside()) == "async-ok"

    def test_E33_cost_logging_node_name(self) -> None:
        with (
            patch(
                "app.services.llm._post_chat",
                new_callable=AsyncMock,
                return_value=(
                    _long_ocr(120),
                    {"prompt_tokens": 10, "completion_tokens": 20},
                ),
            ),
            patch("app.services.llm._openrouter_key", return_value="test-key"),
            patch("app.services.llm._enforce_llm_preflight"),
            patch("app.services.llm._enforce_run_cost_cap"),
            patch("app.services.llm._record_successful_call") as record,
        ):
            from app.services.llm import chat_text_vision

            asyncio.run(
                chat_text_vision(
                    [{"role": "user", "content": "hi"}],
                    model="~google/gemini-flash-latest",
                    rfp_id="edge-cost",
                )
            )
        assert record.call_args.kwargs["node_name"] == "rfp_pdf_ocr"
        assert record.call_args.kwargs["rfp_id"] == "edge-cost"


# ---------------------------------------------------------------------------
# F. Downstream consumers
# ---------------------------------------------------------------------------


class TestF_Downstream:
    def test_F34_gng_clears_image_only_after_ocr(self) -> None:
        pdf = _image_only_pdf()
        body = _long_ocr(220) + "\nFull municipal brand scope and deliverables. " * 5
        with (
            patch("app.services.rfp_content.load_rfp_pdf_bytes", return_value=pdf),
            patch(
                "app.services.pdf_ocr.extract_text_via_ocr",
                return_value=(body, "ocr"),
            ),
            patch("app.services.go_no_go_service.get_rfp_pdf_path", return_value=None),
            patch(
                "app.services.go_no_go_service.resolve_rfp_pdf_path", return_value=None
            ),
        ):
            info = _assess_rfp_content(_rfp())
        assert info.pdf_image_only is False
        assert info.pdf_extracted is True
        qs = _default_clarifying_questions(info)
        assert not any("image-only" in q for q in qs)

    def test_F35_gng_keeps_image_only_message_on_ocr_fail(self) -> None:
        info = RfpContentInfo(
            pdf_path=None,
            pdf_path_recorded="supabase:x/rfp.pdf",
            pdf_file_missing=False,
            pdf_exists=True,
            pdf_page_count=5,
            pdf_image_only=True,
            pdf_text="",
            description="meta",
            substantive_chars=4,
            metadata_only=True,
        )
        qs = _default_clarifying_questions(info)
        assert any("image-only" in q for q in qs)
        summary = _needs_input_summary(_rfp(title="Jackson"), info)
        assert "image-only" in summary

    def test_F36_combine_rfp_text(self) -> None:
        combined = combine_rfp_text("Desc here", "PDF body Cost Proposal")
        assert combined.startswith("Desc here")
        assert "Cost Proposal" in combined

    def test_F37_ocr_text_usable_as_rfp_doc_context(self) -> None:
        from app.services.proposal_intelligence.opportunity_extract.agent1_tools import (
            RfpDoc,
        )

        body = (
            "3. PROPOSAL REQUIREMENTS\n"
            "Cost Proposal: Itemized budget broken down by Brand Strategy and Social Media.\n"
            + ("scope detail " * 80)
        )
        doc = RfpDoc.from_context_text(body, filename="wyoming.txt")
        assert "Cost Proposal" in doc.full_text()

    def test_F38_budget_gate_not_blocked_when_cost_confirmed(self) -> None:
        plan = ProposalExecutionPlan(
            writing=WritingIntelligence(cost_requirement_status="confirmed")
        )
        assert phase35_budget_gate(plan)[0] == "proceed"
        ambiguous = ProposalExecutionPlan(
            writing=WritingIntelligence(
                cost_requirement_status="ambiguous",
                ambiguities=[
                    PlanAmbiguity(
                        topic="Cost / pricing deliverable",
                        status="unresolved",
                        recommendedAction="Obtain a text-readable copy",
                        blocksBudget=True,
                    )
                ],
            )
        )
        gate, detail = phase35_budget_gate(ambiguous)
        assert gate == "skip"
        assert detail and "text-readable" in detail


# ---------------------------------------------------------------------------
# G. Concurrency / ops
# ---------------------------------------------------------------------------


class TestG_Ops:
    def test_G39_parallel_loads_may_double_ocr(self) -> None:
        """Documents current behavior: no lock → both callers can miss cache."""
        pdf = b"%PDF-1.4 race"
        calls = {"n": 0}
        lock = threading.Lock()

        def slow_vision(_factory):
            with lock:
                calls["n"] += 1
            import time

            time.sleep(0.05)
            return _long_ocr(160)

        with (
            patch.object(pdf_ocr.settings, "rfp_ocr_enabled", True),
            patch("app.services.pdf_ocr.load_cached_ocr_text", return_value=None),
            patch("app.services.pdf_ocr.render_pdf_pages_png", return_value=[b"png"]),
            patch("app.services.pdf_ocr._run_coro_sync", side_effect=slow_vision),
            patch("app.services.pdf_ocr.save_ocr_cache_text"),
        ):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futs = [
                    pool.submit(pdf_ocr.extract_text_via_ocr, pdf, rfp_id="race")
                    for _ in range(2)
                ]
                results = [f.result() for f in futs]
        assert all(r[1] == "ocr" for r in results)
        assert calls["n"] == 2  # known ceiling: no single-flight lock

    def test_G40_reupload_invalidates_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(rfp_storage.settings, "pdf_storage_path", tmp_path)
        monkeypatch.setattr(rfp_storage, "use_supabase", lambda: False)
        rfp_id = "reupload"
        sha1 = pdf_ocr.pdf_content_sha256(b"%PDF-1.4 old-file-content")
        pdf_ocr.save_ocr_cache_text(rfp_id, sha1, _long_ocr(150))
        assert pdf_ocr.load_cached_ocr_text(rfp_id, sha1) is not None
        rfp_storage.save_rfp_pdf(rfp_id, b"%PDF-1.4\n" + (b"N" * 600))
        assert pdf_ocr.load_cached_ocr_text(rfp_id, sha1) is None

    def test_G41_env_kill_switch_and_model_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pdf_ocr.settings, "rfp_ocr_enabled", False)
        text, source = pdf_ocr.extract_text_via_ocr(b"%PDF-1.4 x", rfp_id="edge")
        assert text == "" and source == ""

        monkeypatch.setattr(pdf_ocr.settings, "rfp_ocr_enabled", True)
        monkeypatch.setattr(
            pdf_ocr.settings, "openrouter_model_ocr", "~google/gemini-flash-latest"
        )
        monkeypatch.setattr(pdf_ocr.settings, "rfp_ocr_max_pages", 3)
        captured: dict = {}

        async def fake_vision(messages, **kwargs):
            captured["model"] = kwargs.get("model")
            return _long_ocr(120), "openrouter"

        pdf = _image_only_pdf(pages=10)
        pages = pdf_ocr.render_pdf_pages_png(pdf)
        assert len(pages) == 3
        with (
            patch("app.services.pdf_ocr.load_cached_ocr_text", return_value=None),
            patch(
                "app.services.pdf_ocr.render_pdf_pages_png",
                return_value=[b"p1", b"p2", b"p3"],
            ),
            patch("app.services.llm.chat_text_vision", side_effect=fake_vision),
            patch("app.services.pdf_ocr.save_ocr_cache_text"),
        ):
            text, source = pdf_ocr.extract_text_via_ocr(pdf, rfp_id="edge")
        assert source == "ocr"
        assert captured["model"] == "~google/gemini-flash-latest"


@pytest.mark.live_ocr
def test_live_wyoming_ocr_smoke() -> None:
    """Live OpenRouter check. Run: pytest -m live_ocr tests/test_rfp_pdf_ocr_edge_cases.py"""
    from app.core.config import settings

    if not WYOMING_PDF.is_file():
        pytest.skip("Wyoming PDF fixture missing")
    if not settings.openrouter_api_key.strip():
        pytest.skip("OPENROUTER_API_KEY empty")
    content = WYOMING_PDF.read_bytes()
    assert extract_pdf_text_from_bytes(content) == ""
    pages = pdf_ocr.render_pdf_pages_png(content)
    assert len(pages) == 5
    text = asyncio.run(
        pdf_ocr.ocr_pdf_pages_via_vision(pages, rfp_id="live-wyoming-edge")
    )
    assert len(text) >= IMAGE_ONLY_TEXT_THRESHOLD
    assert "proposal" in text.casefold()
