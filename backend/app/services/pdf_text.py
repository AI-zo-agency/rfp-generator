from pathlib import Path

from pypdf import PdfReader


def extract_pdf_text(pdf_path: str, *, max_chars: int = 120_000) -> str:
    path = Path(pdf_path)
    if not path.is_file():
        return ""

    reader = PdfReader(str(path))
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
