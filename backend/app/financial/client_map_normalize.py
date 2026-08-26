from __future__ import annotations

import re

_LEGAL = re.compile(
    r"\b(incorporated|inc|llc|ltd|l\.?l\.?c\.?|corp|corporation|co|company)\b\.?",
    re.I,
)
_NON_ALNUM = re.compile(r"[^a-z0-9\s]+")
_SPACE = re.compile(r"\s+")


def normalize_name(value: str) -> str:
    s = (value or "").casefold()
    s = _LEGAL.sub(" ", s)
    s = _NON_ALNUM.sub(" ", s)
    return _SPACE.sub(" ", s).strip()
