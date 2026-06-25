from pathlib import Path

from pypdf import PdfReader


def extract_pdf_text(pdf_path: str, *, max_chars: int = 120_000) -> str:
    path = Path(pdf_path)
    if not path.is_file():
        return ""
    return extract_pdf_text_from_bytes(path.read_bytes(), max_chars=max_chars)


def extract_pdf_text_from_bytes(content: bytes, *, max_chars: int = 120_000) -> str:
    if not content or not content.startswith(b"%PDF"):
        return ""

    from io import BytesIO

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
