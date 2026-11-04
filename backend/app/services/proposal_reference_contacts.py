"""Client reference contacts read straight from zö's won proposals.

Semantic search finds these lists only some of the time, and a References tab
without them ships as empty MANUAL FILL rows. 01_ClientList_Approved has no
contact details at all, so the reference lists inside 06_WON proposals are the
real source. Only won proposals are read (07_FIN bids were not delivered work)
and only clients approved for public use on the ClientList are kept.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace

from app.models.proposal import EvidenceItem
from app.services import supermemory
from app.services.evidence_trust.load_client_list import load_client_list_registry
from app.services.evidence_trust.provenance import ProvenanceKind, classify_provenance
from app.services.proposal_integrity_guards import _REAL_EMAIL_RE, _REAL_PHONE_RE

logger = logging.getLogger(__name__)

_CACHE_TTL_SEC = 600.0
_LIST_HEADINGS = ("references", "client references")
_LIST_END_PHRASE = "additional references"
_LIST_MAX_LINES = 40
_AGENCY_EMAIL_DOMAIN = "zo.agency"
_BULLETS = ("-", "*", "•")
_NARRATIVE_WORDS_WORTH_KEEPING = 150

_cache: list[ReferenceContact] | None = None
_cached_at = 0.0


@dataclass(frozen=True)
class ReferenceContact:
    name: str
    organization: str
    email: str
    phone: str = ""
    title: str = ""
    project: str = ""
    source: str = ""


def clear_reference_contact_cache() -> None:
    global _cache, _cached_at
    _cache = None
    _cached_at = 0.0


def _strip_marks(line: str) -> str:
    return line.strip().lstrip("-*•# ").strip()


def _split_name_rest(text: str) -> tuple[str, str]:
    name, _, rest = text.partition(",")
    return name.strip(), rest.strip().rstrip(",").strip()


def _contact_from_line(
    line: str,
    *,
    previous_line: str,
    next_line: str,
) -> ReferenceContact | None:
    match = _REAL_EMAIL_RE.search(line)
    if not match or _AGENCY_EMAIL_DOMAIN in match.group(0).casefold():
        return None
    before = _strip_marks(line[: match.start()]).rstrip(" |,").strip()
    phone = _REAL_PHONE_RE.search(line[match.end() :])

    # "- Name, Title" with "  Organization, email | phone" on the line below.
    name_is_above = (
        not line.strip().startswith(_BULLETS)
        and previous_line.strip().startswith(_BULLETS)
        and not _REAL_EMAIL_RE.search(previous_line)
    )
    if name_is_above:
        name, title = _split_name_rest(_strip_marks(previous_line))
        organization = before
    else:
        name, organization = _split_name_rest(before)
        title = ""
    if not name or not organization:
        return None

    project = ""
    if next_line.strip().casefold().startswith("project:"):
        project = next_line.strip()[len("project:") :].strip()
    return ReferenceContact(
        name=name,
        organization=organization,
        email=match.group(0),
        phone=phone.group(0).strip() if phone else "",
        title=title,
        project=project,
    )


def parse_reference_contacts(text: str) -> list[ReferenceContact]:
    """Contacts listed under a References heading. Other emails are not references."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    contacts: list[ReferenceContact] = []
    lines_left = 0
    for index, line in enumerate(lines):
        cleaned = _strip_marks(line).casefold().rstrip(":")
        if cleaned in _LIST_HEADINGS:
            lines_left = _LIST_MAX_LINES
            continue
        if _LIST_END_PHRASE in cleaned:
            lines_left = 0
        if lines_left <= 0:
            continue
        lines_left -= 1
        contact = _contact_from_line(
            line,
            previous_line=lines[index - 1] if index else "",
            next_line=lines[index + 1] if index + 1 < len(lines) else "",
        )
        if contact:
            contacts.append(contact)
    return contacts


def _document_name(doc: dict) -> str:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    return str(metadata.get("fileName") or metadata.get("file_name") or doc.get("title") or "")


def _document_year(name: str) -> int:
    parts = name.replace(".", "_").split("_")
    years = [int(part) for part in parts if len(part) == 4 and part.isdigit()]
    return max(years, default=0)


def _won_proposal_documents(docs: list[dict]) -> list[tuple[str, str]]:
    """(file name, fetch key) for won proposals, newest first."""
    found: dict[str, str] = {}
    for doc in docs:
        name = _document_name(doc)
        key = supermemory.document_fetch_key(doc)
        if (
            key
            and name not in found
            and "proposal" in name.casefold()
            and classify_provenance(name) == ProvenanceKind.WON
        ):
            found[name] = key
    return sorted(found.items(), key=lambda item: _document_year(item[0]), reverse=True)


def _merge_newest_first(lists: list[list[ReferenceContact]]) -> list[ReferenceContact]:
    """One row per person. The newest list wins; older lists only fill its gaps."""
    merged: dict[str, ReferenceContact] = {}
    for contacts in lists:
        for contact in contacts:
            key = contact.name.casefold()
            current = merged.get(key)
            if current is None:
                merged[key] = contact
                continue
            merged[key] = replace(
                current,
                phone=current.phone or contact.phone,
                title=current.title or contact.title,
                project=current.project or contact.project,
            )
    return list(merged.values())


async def _read_won_reference_contacts() -> list[ReferenceContact]:
    documents = _won_proposal_documents(await supermemory.list_all_container_documents())
    bodies = await asyncio.gather(
        *(supermemory.get_document_content(custom_id=key) for _, key in documents),
        return_exceptions=True,
    )
    per_document: list[list[ReferenceContact]] = []
    for (name, _), body in zip(documents, bodies):
        if isinstance(body, BaseException):
            logger.warning("Reference contacts: could not read %s: %s", name, body)
            continue
        per_document.append(
            [replace(contact, source=name) for contact in parse_reference_contacts(body)]
        )

    registry = await load_client_list_registry()
    approved: list[ReferenceContact] = []
    for contact in _merge_newest_first(per_document):
        entry = registry.find(contact.organization)
        if entry and entry.public.strip().casefold() == "yes":
            approved.append(contact)
    return approved


async def load_won_reference_contacts(*, force_refresh: bool = False) -> list[ReferenceContact]:
    """Approved client contacts from won proposals. Empty when they cannot be read."""
    global _cache, _cached_at
    if not supermemory.is_configured():
        return []
    now = time.monotonic()
    if not force_refresh and _cache is not None and (now - _cached_at) < _CACHE_TTL_SEC:
        return _cache
    try:
        contacts = await _read_won_reference_contacts()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Reference contacts unavailable: %s", exc)
        return _cache or []
    _cache = contacts
    _cached_at = now
    return contacts


def format_reference_contacts(contacts: list[ReferenceContact]) -> str:
    """Prompt block the writer copies rows from. Empty when there are no contacts."""
    if not contacts:
        return ""
    lines = [
        "Verified client references from zö's won proposals. Use ONLY these people "
        "as reference contacts, pick the ones closest to this RFP, and copy contact "
        "details exactly. Do not invent a missing title, phone, or email. Write one "
        "table row per reference, with a short scope cell and no line breaks."
    ]
    for contact in contacts:
        person = ", ".join(part for part in (contact.name, contact.title) if part)
        fields = [f"{person}, {contact.organization}", contact.email]
        if contact.phone:
            fields.append(contact.phone)
        if contact.project:
            fields.append(f"Project: {contact.project}")
        lines.append("- " + " | ".join(fields))
    return "\n".join(lines)


def cached_reference_contacts_text() -> str:
    """Last loaded list, for sync guards. They must accept phones the writer was given."""
    return format_reference_contacts(_cache or [])


async def reference_contact_evidence_item(
    section_id: str,
    *,
    evidence_id: str,
) -> EvidenceItem | None:
    contacts = await load_won_reference_contacts()
    excerpt = format_reference_contacts(contacts)
    if not excerpt:
        return None
    return EvidenceItem(
        id=evidence_id,
        source=contacts[0].source,
        excerpt=excerpt,
        sectionIds=[section_id],
        chunkKey="won-reference-contacts",
    )


async def add_reference_contacts_to_corpus(
    corpus: list[EvidenceItem],
    section_id: str,
    section_title: str,
) -> list[EvidenceItem]:
    """Put the contact list first in a reference tab's evidence.

    Improve assembles its own evidence, and the writer's evidence block is what
    it trusts most. Saved with the corpus, so the phone guard accepts these later.
    """
    from app.services.proposal_evidence_corpus import ensure_unique_evidence_ids
    from app.services.proposal_intelligence.agents.retrieval_planner import (
        section_wants_client_references,
    )

    if not section_wants_client_references(section_title):
        return corpus
    item = await reference_contact_evidence_item(section_id, evidence_id="won-references")
    if item is None:
        return corpus
    others = [entry for entry in corpus if entry.chunk_key != item.chunk_key]
    return ensure_unique_evidence_ids([item, *others])


def reference_table_is_only_placeholders(content: str) -> bool:
    """True for a short tab whose table holds placeholders and no client contact."""
    text = content or ""
    for match in _REAL_EMAIL_RE.finditer(text):
        if _AGENCY_EMAIL_DOMAIN not in match.group(0).casefold():
            return False
    lines = [line.strip() for line in text.splitlines()]
    has_placeholder_row = any(
        line.startswith("|") and "[manual fill" in line.casefold() for line in lines
    )
    narrative_words = sum(len(line.split()) for line in lines if not line.startswith("|"))
    return has_placeholder_row and narrative_words <= _NARRATIVE_WORDS_WORTH_KEEPING
