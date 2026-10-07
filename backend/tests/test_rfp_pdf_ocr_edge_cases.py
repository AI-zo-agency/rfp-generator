"""Edge cases for RFP PDF text pipeline: pypdf → OCR cache → OpenRouter native PDF.

Covers the OCR matrix. OpenRouter is mocked unless marked live_ocr.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
        text, source = pdf_ocr.extract_text_via_ocr(b"not-a-pdf", rfp_id="edge")
        assert text == "" and source == ""

    def test_A5_password_protected_does_not_crash(self) -> None:
        enc = _encrypted_pdf()
        assert extract_pdf_text_from_bytes(enc) == ""
        with patch("app.services.rfp_content.load_rfp_pdf_bytes", return_value=enc):
            _d, text, exists, _missing, pages, _image_only = load_local_rfp_text(_rfp())
        assert exists
        assert text == ""
        assert pages >= 0

    def test_A6_no_page_cap_full_pdf_sent(self) -> None:
        """50-page scans must send the whole PDF — not a page subset."""
        pdf = _image_only_pdf(pages=12)
        captured: dict = {}

        async def fake_vision(messages, **kwargs):
            captured["plugins"] = kwargs.get("plugins")
            captured["messages"] = messages
            return _long_ocr(200), "openrouter"

        with patch("app.services.llm.chat_text_vision", side_effect=fake_vision):
            text = asyncio.run(
                pdf_ocr.ocr_pdf_via_vision(pdf, rfp_id="edge-a6", max_chars=10_000)
            )
        assert "Cost Proposal" in text
        file_part = next(
            p
            for p in captured["messages"][0]["content"]
            if p.get("type") == "file"
        )
        assert file_part["file"]["file_data"].startswith("data:application/pdf;base64,")
        assert captured["plugins"] == [
            {"id": "file-parser", "pdf": {"engine": "native"}}
        ]
        # Entire PDF bytes encoded — not N page images.
        image_parts = [
            p for p in captured["messages"][0]["content"] if p.get("type") == "image_url"
        ]
        assert image_parts == []

    def test_A7_large_pdf_payload_builds_file_part(self) -> None:
        big = b"%PDF-1.4\n" + (b"0" * 200_000)
        with patch(
            "app.services.llm.chat_text_vision",
            new_callable=AsyncMock,
            return_value=(_long_ocr(200), "openrouter"),
        ) as vision:
            text = asyncio.run(
                pdf_ocr.ocr_pdf_via_vision(big, rfp_id="edge-a7")
            )
        assert len(text) >= IMAGE_ONLY_TEXT_THRESHOLD
        vision.assert_awaited_once()
        assert vision.await_args.kwargs.get("plugins")

    def test_A8_rotated_scan_loader_triggers_ocr(self) -> None:
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
        assert is_image_only_pdf(data)
        with (
            patch("app.services.rfp_content.load_rfp_pdf_bytes", return_value=data),
            patch(
                "app.services.pdf_ocr.extract_text_via_ocr",
                return_value=(_long_ocr(200), "ocr"),
            ) as ocr,
        ):
            load_local_rfp_text(_rfp())
        ocr.assert_called_once()

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


class TestB_LoaderGate:
    def test_B10_missing_pdf(self) -> None:
        with patch("app.services.rfp_content.load_rfp_pdf_bytes", return_value=None):
            _d, text, exists, missing, pages, image_only = load_local_rfp_text(
                _rfp(pdf_path="supabase:rfp-ocr-edge/rfp.pdf")
            )
        assert not exists and missing and text == "" and pages == 0
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
        assert text == "" and not exists and not missing
        assert image_only is False

    def test_B12_ocr_disabled(self) -> None:
        pdf = _image_only_pdf()
        with (
            patch.object(pdf_ocr.settings, "rfp_ocr_enabled", False),
            patch("app.services.pdf_ocr._run_coro_sync") as vision,
        ):
            text, source = pdf_ocr.extract_text_via_ocr(pdf, rfp_id="edge")
        assert text == "" and source == ""
        vision.assert_not_called()

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
        assert exists and pages >= 1 and text == "" and image_only is True

    def test_B14_ocr_short_text_rejected(self) -> None:
        pdf = b"%PDF-1.4 scan"
        with (
            patch.object(pdf_ocr.settings, "rfp_ocr_enabled", True),
            patch("app.services.pdf_ocr.load_cached_ocr_text", return_value=None),
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
            patch("app.services.pdf_ocr._run_coro_sync", return_value=exactly),
            patch("app.services.pdf_ocr.save_ocr_cache_text") as save,
        ):
            text, source = pdf_ocr.extract_text_via_ocr(pdf, rfp_id="edge")
        assert source == "ocr" and len(text) == IMAGE_ONLY_TEXT_THRESHOLD
        save.assert_called_once()

        with (
            patch.object(pdf_ocr.settings, "rfp_ocr_enabled", True),
            patch("app.services.pdf_ocr.load_cached_ocr_text", return_value=None),
            patch("app.services.pdf_ocr._run_coro_sync", return_value=below),
            patch("app.services.pdf_ocr.save_ocr_cache_text") as save2,
        ):
            text2, source2 = pdf_ocr.extract_text_via_ocr(pdf, rfp_id="edge")
        assert text2 == "" and source2 == ""
        save2.assert_not_called()


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
            patch("app.services.pdf_ocr._run_coro_sync", return_value=body) as vision,
        ):
            t1, s1 = pdf_ocr.extract_text_via_ocr(pdf, rfp_id="cache-rfp")
            t2, s2 = pdf_ocr.extract_text_via_ocr(pdf, rfp_id="cache-rfp")
        assert s1 == "ocr" and s2 == "cache"
        assert t1 == body.strip() and t2 == body.strip()
        assert vision.call_count == 1

    def test_C17_hash_mismatch_reocr(self) -> None:
        blob = pdf_ocr.format_ocr_cache_blob("a" * 64, _long_ocr(150))
        assert pdf_ocr.parse_ocr_cache_blob(blob, expected_sha256="b" * 64) is None

    def test_C18_corrupt_cache_header_miss(self) -> None:
        assert (
            pdf_ocr.parse_ocr_cache_blob(b"not-a-header\ntext", expected_sha256="a" * 64)
            is None
        )

    def test_C19_cache_write_failure_still_returns_text(self) -> None:
        pdf = b"%PDF-1.4 write-fail"
        body = _long_ocr(160)
        with (
            patch.object(pdf_ocr.settings, "rfp_ocr_enabled", True),
            patch("app.services.pdf_ocr.load_cached_ocr_text", return_value=None),
            patch("app.services.pdf_ocr._run_coro_sync", return_value=body),
            patch(
                "app.services.pdf_ocr.save_ocr_cache_text",
                side_effect=RuntimeError("storage down"),
            ),
        ):
            text, source = pdf_ocr.extract_text_via_ocr(pdf, rfp_id="edge")
        assert source == "ocr" and text == body.strip()

    def test_C20_save_pdf_deletes_ocr_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(rfp_storage.settings, "pdf_storage_path", tmp_path)
        monkeypatch.setattr(rfp_storage, "use_supabase", lambda: False)
        rfp_id = "del-cache"
        (tmp_path / rfp_id).mkdir()
        cache_path = tmp_path / rfp_id / "rfp.ocr.txt"
        cache_path.write_text("# sha256=" + ("a" * 64) + "\nhello\n", encoding="utf-8")
        rfp_storage.save_rfp_pdf(rfp_id, b"%PDF-1.4\n" + (b"0" * 600))
        assert not cache_path.exists()

    def test_C21_local_disk_cache_roundtrip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(rfp_storage.settings, "pdf_storage_path", tmp_path)
        monkeypatch.setattr(rfp_storage, "use_supabase", lambda: False)
        sha = "c" * 64
        pdf_ocr.save_ocr_cache_text("local-cache", sha, "Local disk OCR body " + ("x" * 100))
        assert pdf_ocr.load_cached_ocr_text("local-cache", sha)
        pdf_ocr.delete_ocr_cache("local-cache")
        assert pdf_ocr.load_cached_ocr_text("local-cache", sha) is None


class TestD_DirectPdf:
    def test_D22_empty_content_skips(self) -> None:
        assert pdf_ocr.extract_text_via_ocr(b"", rfp_id="edge") == ("", "")

    def test_D23_non_pdf_magic_skips(self) -> None:
        assert pdf_ocr.extract_text_via_ocr(b"PNG...", rfp_id="edge") == ("", "")

    def test_D24_native_plugin_always_attached(self) -> None:
        async def fake_vision(messages, **kwargs):
            assert kwargs.get("plugins") == pdf_ocr._NATIVE_PDF_PLUGIN
            return _long_ocr(120), "openrouter"

        with patch("app.services.llm.chat_text_vision", side_effect=fake_vision):
            text = asyncio.run(
                pdf_ocr.ocr_pdf_via_vision(b"%PDF-1.4 stub-content", rfp_id="edge")
            )
        assert len(text) >= IMAGE_ONLY_TEXT_THRESHOLD


class TestE_Vision:
    def test_E25_invalid_model_returns_empty(self) -> None:
        with patch(
            "app.services.llm.chat_text_vision",
            new_callable=AsyncMock,
            side_effect=LlmError("invalid model", status_code=400),
        ):
            text = asyncio.run(
                pdf_ocr.ocr_pdf_via_vision(b"%PDF-1.4 x", rfp_id="edge")
            )
        assert text == ""

    def test_E26_missing_api_key(self) -> None:
        with patch(
            "app.services.llm.chat_text_vision",
            new_callable=AsyncMock,
            side_effect=LlmError("OPENROUTER_API_KEY required", status_code=503),
        ):
            text = asyncio.run(
                pdf_ocr.ocr_pdf_via_vision(b"%PDF-1.4 x", rfp_id="edge")
            )
        assert text == ""

    @pytest.mark.parametrize("code", [401, 402, 429])
    def test_E27_openrouter_http_errors(self, code: int) -> None:
        with patch(
            "app.services.llm.chat_text_vision",
            new_callable=AsyncMock,
            side_effect=LlmError(f"error {code}", status_code=code),
        ):
            text = asyncio.run(
                pdf_ocr.ocr_pdf_via_vision(b"%PDF-1.4 x", rfp_id="edge")
            )
        assert text == ""

    def test_E28_empty_model_content(self) -> None:
        with patch(
            "app.services.llm.chat_text_vision",
            new_callable=AsyncMock,
            return_value=("", "openrouter"),
        ):
            text = asyncio.run(
                pdf_ocr.ocr_pdf_via_vision(b"%PDF-1.4 x", rfp_id="edge")
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
                pdf_ocr.ocr_pdf_via_vision(b"%PDF-1.4 x", rfp_id="edge")
            )
        assert not text.startswith("```")
        assert "Cost Proposal" in text

    def test_E30_max_chars_truncation(self) -> None:
        with patch(
            "app.services.llm.chat_text_vision",
            new_callable=AsyncMock,
            return_value=("A" * 5_000, "openrouter"),
        ):
            text = asyncio.run(
                pdf_ocr.ocr_pdf_via_vision(
                    b"%PDF-1.4 x", rfp_id="edge", max_chars=500
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
        assert pdf_ocr._run_coro_sync(lambda: asyncio.sleep(0, result="sync-ok")) == "sync-ok"

        async def _inside():
            return pdf_ocr._run_coro_sync(lambda: asyncio.sleep(0, result="async-ok"))

        assert asyncio.run(_inside()) == "async-ok"

    def test_E33_cost_logging_and_plugins_forwarded(self) -> None:
        with (
            patch(
                "app.services.llm._post_chat",
                new_callable=AsyncMock,
                return_value=(
                    _long_ocr(120),
                    {"prompt_tokens": 10, "completion_tokens": 20},
                ),
            ) as post,
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
                    plugins=pdf_ocr._NATIVE_PDF_PLUGIN,
                )
            )
        assert record.call_args.kwargs["node_name"] == "rfp_pdf_ocr"
        assert post.await_args.kwargs["plugins"] == pdf_ocr._NATIVE_PDF_PLUGIN


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
        assert info.pdf_image_only is False and info.pdf_extracted is True
        assert not any("image-only" in q for q in _default_clarifying_questions(info))

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
        assert any("image-only" in q for q in _default_clarifying_questions(info))
        assert "image-only" in _needs_input_summary(_rfp(title="Jackson"), info)

    def test_F36_combine_rfp_text(self) -> None:
        combined = combine_rfp_text("Desc here", "PDF body Cost Proposal")
        assert "Desc here" in combined and "Cost Proposal" in combined

    def test_F37_ocr_text_usable_as_rfp_doc_context(self) -> None:
        from app.services.proposal_intelligence.opportunity_extract.agent1_tools import (
            RfpDoc,
        )

        body = (
            "Cost Proposal: Itemized budget.\n" + ("scope detail " * 80)
        )
        assert "Cost Proposal" in RfpDoc.from_context_text(body).full_text()

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
        assert gate == "skip" and detail and "text-readable" in detail


class TestG_Ops:
    def test_G39_parallel_loads_may_double_ocr(self) -> None:
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
            patch("app.services.pdf_ocr._run_coro_sync", side_effect=slow_vision),
            patch("app.services.pdf_ocr.save_ocr_cache_text"),
        ):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = [
                    f.result()
                    for f in [
                        pool.submit(pdf_ocr.extract_text_via_ocr, pdf, rfp_id="race")
                        for _ in range(2)
                    ]
                ]
        assert all(r[1] == "ocr" for r in results)
        assert calls["n"] == 2

    def test_G40_reupload_invalidates_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(rfp_storage.settings, "pdf_storage_path", tmp_path)
        monkeypatch.setattr(rfp_storage, "use_supabase", lambda: False)
        rfp_id = "reupload"
        sha1 = pdf_ocr.pdf_content_sha256(b"%PDF-1.4 old-file-content")
        pdf_ocr.save_ocr_cache_text(rfp_id, sha1, _long_ocr(150))
        rfp_storage.save_rfp_pdf(rfp_id, b"%PDF-1.4\n" + (b"N" * 600))
        assert pdf_ocr.load_cached_ocr_text(rfp_id, sha1) is None

    def test_G41_env_kill_switch_and_model_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pdf_ocr.settings, "rfp_ocr_enabled", False)
        assert pdf_ocr.extract_text_via_ocr(b"%PDF-1.4 x", rfp_id="edge") == ("", "")

        monkeypatch.setattr(pdf_ocr.settings, "rfp_ocr_enabled", True)
        monkeypatch.setattr(
            pdf_ocr.settings, "openrouter_model_ocr", "~google/gemini-flash-latest"
        )
        captured: dict = {}

        async def fake_vision(messages, **kwargs):
            captured["model"] = kwargs.get("model")
            return _long_ocr(120), "openrouter"

        with (
            patch("app.services.pdf_ocr.load_cached_ocr_text", return_value=None),
            patch("app.services.llm.chat_text_vision", side_effect=fake_vision),
            patch("app.services.pdf_ocr.save_ocr_cache_text"),
        ):
            text, source = pdf_ocr.extract_text_via_ocr(
                b"%PDF-1.4 model", rfp_id="edge"
            )
        assert source == "ocr"
        assert captured["model"] == "~google/gemini-flash-latest"


@pytest.mark.live_ocr
def test_live_wyoming_ocr_smoke() -> None:
    """Live OpenRouter native-PDF check. Run: pytest -m live_ocr ..."""
    from app.core.config import settings

    if not WYOMING_PDF.is_file():
        pytest.skip("Wyoming PDF fixture missing")
    if not settings.openrouter_api_key.strip():
        pytest.skip("OPENROUTER_API_KEY empty")
    content = WYOMING_PDF.read_bytes()
    assert extract_pdf_text_from_bytes(content) == ""
    text = asyncio.run(
        pdf_ocr.ocr_pdf_via_vision(content, rfp_id="live-wyoming-edge")
    )
    assert len(text) >= IMAGE_ONLY_TEXT_THRESHOLD
    assert "proposal" in text.casefold()
