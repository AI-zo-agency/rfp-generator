"""Self-check: page offset mapper (no LLM). Run from demo folder:

  ../../.venv/bin/python check_page_map.py
"""

from __future__ import annotations

from langextract_harvest import build_page_index, page_for_char


def main() -> None:
    pages = [
        "KEY INFORMATION. Contract begins July 1, 2027.",
        "Offeror shall submit three references.",
        "Technical Approach shall not exceed one page EACH.",
    ]
    full, ranges = build_page_index(pages)
    assert "Page 2" in full
    p = page_for_char(ranges, full.find("three references"))
    assert p == 2, p
    print("check_page_map: ok")


if __name__ == "__main__":
    main()
