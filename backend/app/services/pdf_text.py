from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

IMAGE_ONLY_TEXT_THRESHOLD = 100


def pdf_page_count(content: bytes) -> int:
    if not content or not content.startswith(b"%PDF"):
        return 0
    try:
        return len(PdfReader(BytesIO(content)).pages)
    except Exception:
        return 0


def is_image_only_pdf(content: bytes, *, extracted_text: str = "") -> bool:
    """True when a PDF has pages but almost no machine-readable text (typical scan)."""
    pages = pdf_page_count(content)
    if pages == 0:
        return False
    text_len = len(extracted_text.strip()) if extracted_text else len(
        extract_pdf_text_from_bytes(content, max_chars=IMAGE_ONLY_TEXT_THRESHOLD + 1)
    )
    return text_len < IMAGE_ONLY_TEXT_THRESHOLD


def extract_pdf_text(pdf_path: str, *, max_chars: int = 120_000) -> str:
    path = Path(pdf_path)
    if not path.is_file():
        return ""
    return extract_pdf_text_from_bytes(path.read_bytes(), max_chars=max_chars)


def extract_pdf_text_from_bytes(content: bytes, *, max_chars: int = 120_000) -> str:
    if not content or not content.startswith(b"%PDF"):
        return ""

    reader = PdfReader(BytesIO(content))
    parts: list[str] = []
    total = 0

    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        chunk = text[:remaining]
        parts.append(chunk)
        total += len(chunk)

    return "\n\n".join(parts).strip()
