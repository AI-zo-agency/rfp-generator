"""Person-initiated manuscript changes ("tweaks"): diffing and recording.

A tweak is a persisted change to section text or the outline made by a person,
not by a pipeline phase. Each one is stored with full before/after text in
`proposal_tweak_events` (see proposal_tweaks_store) so the Activity ledger can
show it as a diff. Nothing here may raise into a save.
"""

from __future__ import annotations

import difflib
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.models.proposal import ProposalDraft, ProposalSection
from app.services import proposal_tweaks_store as store
from app.services.llm_call_context import get_llm_node_name, get_llm_user_email

logger = logging.getLogger(__name__)

TWEAK_SOURCES: tuple[str, ...] = (
    "manual_edit",
    "personas",
    "chat_apply",
    "chat_confirm",
    "restore_snapshot",
    "restore_archive",
    "restart",
    "reset",
    "other",
)

BUCKET_BY_SOURCE: dict[str, str] = {
    "manual_edit": "manual",
    "personas": "manual",
    "chat_apply": "ai",
    "chat_confirm": "ai",
    "restore_snapshot": "restore",
    "restore_archive": "restore",
    "restart": "restore",
    "reset": "restore",
    "other": "other",
}

# Manual typing autosaves repeatedly; edits by one person to one section merge
# into a single row until this much quiet time passes.
MANUAL_BURST_GAP = timedelta(minutes=10)

# Above this many words on either side, skip the O(n^2) diff and report the
# length difference instead.
MAX_DIFF_WORDS = 20_000


@dataclass(frozen=True)
class SectionChange:
    section_id: str
    section_title: str
    kind: str  # "text" | "added" | "removed" | "renamed"
    before_title: str
    after_title: str
    before_text: str
    after_text: str


def _norm(text: str | None) -> str:
    return " ".join((text or "").split())


def _counts(section: ProposalSection) -> bool:
    """Empty outline stubs coming and going are structure noise, not tweaks."""
    return bool(_norm(section.content)) or bool(section.custom)


def diff_drafts(
    before: ProposalDraft | None, after: ProposalDraft | None
) -> list[SectionChange]:
    """Per-section differences between two drafts, ignoring whitespace-only edits."""
    old = {s.id: s for s in (before.sections if before else [])}
    new = {s.id: s for s in (after.sections if after else [])}
    changes: list[SectionChange] = []
    for section_id, section in new.items():
        prior = old.get(section_id)
        if prior is None:
            if _counts(section):
                changes.append(
                    SectionChange(
                        section_id, section.title, "added", "", section.title,
                        "", section.content or "",
                    )
                )
            continue
        if _norm(prior.content) != _norm(section.content):
            changes.append(
                SectionChange(
                    section_id, section.title, "text", prior.title, section.title,
                    prior.content or "", section.content or "",
                )
            )
        elif _norm(prior.title) != _norm(section.title):
            changes.append(
                SectionChange(
                    section_id, section.title, "renamed", prior.title, section.title,
                    prior.content or "", section.content or "",
                )
            )
    for section_id, prior in old.items():
        if section_id not in new and _counts(prior):
            changes.append(
                SectionChange(
                    section_id, prior.title, "removed", prior.title, "",
                    prior.content or "", "",
                )
            )
    return changes


def count_words_changed(before: str, after: str) -> tuple[int, int]:
    """(words added, words removed) going from `before` to `after`."""
    a, b = before.split(), after.split()
    if len(a) > MAX_DIFF_WORDS or len(b) > MAX_DIFF_WORDS:
        delta = len(b) - len(a)
        return (delta, 0) if delta >= 0 else (0, -delta)
    added = removed = 0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=True)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def record_tweaks(
    before: ProposalDraft | None,
    after: ProposalDraft | None,
    *,
    source: str,
    actor_email: str,
    rfp_id: str,
    rfp_title: str = "",
    instruction: str = "",
    now: datetime | None = None,
) -> int:
    """Store one tweak per changed section. Returns how many rows were written or extended."""
    if source not in TWEAK_SOURCES or not (actor_email or "").strip():
        return 0
    moment = now or datetime.now(timezone.utc)
    stamp = moment.isoformat()
    written = 0
    for change in diff_drafts(before, after):
        added, removed = count_words_changed(change.before_text, change.after_text)
        if source == "manual_edit" and change.kind == "text":
            burst = store.find_open_burst(
                rfp_id, change.section_id, actor_email, (moment - MANUAL_BURST_GAP).isoformat()
            )
            if burst:
                burst_added, burst_removed = count_words_changed(
                    burst["before_text"], change.after_text
                )
                if store.update_tweak(
                    burst["id"],
                    {
                        "after_text": change.after_text,
                        "after_title": change.after_title,
                        "words_after": len(change.after_text.split()),
                        "words_added": burst_added,
                        "words_removed": burst_removed,
                        "updated_at": stamp,
                    },
                ):
                    written += 1
                    continue
        inserted = store.insert_tweak(
            {
                "created_at": stamp,
                "rfp_id": rfp_id,
                "rfp_title": rfp_title,
                "section_id": change.section_id,
                "section_title": change.section_title,
                "actor_email": actor_email,
                "source": source,
                "bucket": BUCKET_BY_SOURCE[source],
                "kind": change.kind,
                "instruction": instruction,
                "words_before": len(change.before_text.split()),
                "words_after": len(change.after_text.split()),
                "words_added": added,
                "words_removed": removed,
                "before_title": change.before_title,
                "after_title": change.after_title,
                "before_text": change.before_text,
                "after_text": change.after_text,
            }
        )
        if inserted:
            written += 1
    return written


# ---------------------------------------------------------------------------
# Who is acting: the request's tweak source
# ---------------------------------------------------------------------------
# The middleware classifies each HTTP request and sets the source for its
# duration. Workers, the scheduler and GET requests leave it unset, so only a
# person's request can ever record a tweak. "run" marks person-triggered AI
# runs, which are runs (see activity_runs), not tweaks.

_tweak_source: ContextVar[str] = ContextVar("tweak_source", default="")
_tweak_instruction: ContextVar[str] = ContextVar("tweak_instruction", default="")


def get_tweak_source() -> str:
    return _tweak_source.get() or ""


def get_tweak_instruction() -> str:
    return _tweak_instruction.get() or ""


def set_tweak_instruction(text: str) -> None:
    """Remember the typed instruction for the rest of this request."""
    _tweak_instruction.set((text or "").strip())


@contextmanager
def tweak_source_scope(source: str) -> Iterator[None]:
    source_token = _tweak_source.set(source)
    instruction_token = _tweak_instruction.set("")
    try:
        yield
    finally:
        _tweak_instruction.reset(instruction_token)
        _tweak_source.reset(source_token)


_RUN_TAILS = frozenset(
    {
        "generate",
        "pricing/generate",
        "build-finalize",
        "fulfill-rfp-gaps",
        "align-rfp-outline",
        "align-rfp-outline/preview",
        "packet-redistribute",
        "packet-redistribute/preview",
        "match-case-studies",
        "stop",
        "generation/clear-stop",
        "voice-rev",
    }
)
_RESTART_TAILS = frozenset({"restart-from-intelligence", "restart-from-case-studies"})


def classify_proposal_request(method: str, path: str) -> str:
    """Tweak source for a request: a TWEAK_SOURCES member, "run", or "" (not a manuscript write)."""
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return ""
    parts = [p for p in path.split("/") if p]
    if "rfps" not in parts:
        return ""
    tail = parts[parts.index("rfps") + 2 :]
    if not tail:
        return ""
    if tail[0] == "key-personas":
        return "personas"
    if tail[0] != "proposal":
        return ""
    rest = tail[1:]
    key = "/".join(rest)
    if not rest:
        return "manual_edit" if method.upper() == "PUT" else "other"
    if len(rest) == 3 and rest[0] == "sections" and rest[2] == "improve":
        return "chat_apply"
    if key == "confirm-preview":
        return "chat_confirm"
    if key == "restore-snapshot":
        return "restore_snapshot"
    if len(rest) == 3 and rest[0] == "archives" and rest[2] == "restore":
        return "restore_archive"
    if key in _RESTART_TAILS:
        return "restart"
    if key == "reset":
        return "reset"
    if key == "key-personas":
        return "personas"
    if key in _RUN_TAILS or rest[0].startswith("phase-") or rest[0] == "export":
        return "run"
    return "other"


# ---------------------------------------------------------------------------
# Instruction typed at preview time (confirm-preview carries only the draft)
# ---------------------------------------------------------------------------
# Process-local: if the confirm lands on another API process the instruction
# is simply blank, as the design allows.

_PREVIEW_TTL_SECONDS = 3600.0
_preview_instructions: dict[tuple[str, str], tuple[float, str]] = {}
_preview_lock = threading.Lock()


def remember_preview_instruction(
    rfp_id: str, actor_email: str, text: str, *, now: float | None = None
) -> None:
    moment = time.monotonic() if now is None else now
    with _preview_lock:
        if len(_preview_instructions) > 500:
            for key in [
                k for k, (at, _t) in _preview_instructions.items()
                if moment - at > _PREVIEW_TTL_SECONDS
            ]:
                del _preview_instructions[key]
        _preview_instructions[(rfp_id, (actor_email or "").lower())] = (moment, (text or "").strip())


def recall_preview_instruction(
    rfp_id: str, actor_email: str, *, now: float | None = None
) -> str:
    moment = time.monotonic() if now is None else now
    with _preview_lock:
        entry = _preview_instructions.get((rfp_id, (actor_email or "").lower()))
    if entry is None or moment - entry[0] > _PREVIEW_TTL_SECONDS:
        return ""
    return entry[1]


# ---------------------------------------------------------------------------
# The save/delete hook
# ---------------------------------------------------------------------------


@dataclass
class TweakCapture:
    """What a person's save started from; `finish` records the difference."""

    rfp_id: str
    source: str
    actor_email: str
    before: ProposalDraft | None
    instruction: str

    def finish(self, after: ProposalDraft | None) -> None:
        try:
            from app.services.rfp_repository import get_rfp

            rfp = get_rfp(self.rfp_id)
            record_tweaks(
                self.before,
                after,
                source=self.source,
                actor_email=self.actor_email,
                rfp_id=self.rfp_id,
                rfp_title=(rfp.title if rfp else "") or "",
                instruction=self.instruction,
            )
        except Exception:  # noqa: BLE001 — recording must never break a save
            logger.exception("proposal_tweaks: failed to record tweak for %s", self.rfp_id)


def begin_tweak_capture(rfp_id: str) -> TweakCapture | None:
    """Snapshot the stored draft when a person is saving; None for pipelines and workers."""
    source = get_tweak_source()
    if source not in TWEAK_SOURCES or get_llm_node_name():
        return None
    actor = get_llm_user_email()
    if not actor:
        return None
    try:
        from app.services.proposal_repository import get_proposal_draft_fresh

        before = get_proposal_draft_fresh(rfp_id)
    except Exception:  # noqa: BLE001
        logger.exception("proposal_tweaks: could not read the draft before saving %s", rfp_id)
        return None
    instruction = ""
    if source == "chat_apply":
        instruction = get_tweak_instruction()
    elif source == "chat_confirm":
        instruction = recall_preview_instruction(rfp_id, actor)
    return TweakCapture(rfp_id, source, actor, before, instruction)
