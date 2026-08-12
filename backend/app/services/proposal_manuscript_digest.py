"""Stable manuscript fingerprints for skip-if-unchanged pipeline passes."""

from __future__ import annotations

import hashlib

from app.models.proposal import ProposalDraft


def manuscript_content_hash(draft: ProposalDraft | None) -> str:
    """SHA256 of section id + content — cheap gate for redundant LLM scans."""
    if not draft or not draft.sections:
        return ""
    parts: list[str] = []
    for section in draft.sections:
        sid = section.id or ""
        body = (section.content or "").strip()
        parts.append(f"{sid}\n{body}")
    digest = hashlib.sha256("\n---\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:24]
