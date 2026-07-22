"""Parse 01_ClientList_Approved.md into a queryable registry."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


_TABLE_ROW_RE = re.compile(
    r"^\|\s*(?P<client>[^|]+?)\s*\|\s*(?P<sector>[^|]+?)\s*\|\s*(?P<work>[^|]+?)\s*\|\s*(?P<public>[^|]+?)\s*\|$"
)
_HEADER_SKIP = re.compile(r"^\|\s*-+", re.I)


@dataclass(frozen=True)
class ClientListEntry:
    name: str
    sector: str
    work_type: str
    public: str  # "Yes" | "Confirm" | other

    @property
    def is_public_yes(self) -> bool:
        return self.public.strip().casefold() == "yes"

    @property
    def is_confirm(self) -> bool:
        return "confirm" in self.public.strip().casefold()

    @property
    def work_type_cf(self) -> str:
        return self.work_type.casefold()


# Claim tokens → phrases that must appear in Work Type (any match = ok).
CLAIM_WORK_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "website": (
        "website",
        "web site",
        "web build",
        "site build",
        "web development",
        "digital marketing",  # weak; prefer explicit website — still allow digital when claim is broader
    ),
    "website_build": (
        "website",
        "web site",
        "web build",
        "site build",
        "web development",
        "custom mortgage calculator",  # Deschutes County Title pattern
    ),
    "brand": ("brand", "identity", "rebrand", "branding"),
    "pr": ("pr", "public relations", "communications"),
    "collateral": ("collateral", "signage", "merchandise", "templates"),
    "tourism_leisure": (
        "leisure",
        "visitor",
        "destination brand",
        "tourism leisure",
        "hospitality",
    ),
    "tourism_mci": (
        "meeting",
        "conference",
        "mci",
        "events strategy",
        "meeting and conference",
    ),
    "destination_marketing": (
        "destination",
        "tourism",
        "visitor",
        "travel",
        "hospitality",
        "events strategy",
        "meeting",
        "conference",
        "campaign",
    ),
}


@dataclass
class ClientListRegistry:
    entries: list[ClientListEntry] = field(default_factory=list)
    source_label: str = "01_ClientList_Approved.md"

    def find(self, name: str) -> ClientListEntry | None:
        needle = _normalize_client_name(name)
        if not needle:
            return None
        for entry in self.entries:
            en = _normalize_client_name(entry.name)
            if en == needle or needle in en or en in needle:
                return entry
        # Token overlap for "Medford" vs "City of Medford"
        tokens = [t for t in re.split(r"\W+", needle) if len(t) >= 4]
        if not tokens:
            return None
        for entry in self.entries:
            en = _normalize_client_name(entry.name)
            if all(t in en for t in tokens):
                return entry
        return None

    def work_type_supports_claim(self, entry: ClientListEntry, claim: str) -> bool:
        claim_key = claim.strip().casefold().replace(" ", "_").replace("-", "_")
        aliases = CLAIM_WORK_TYPE_ALIASES.get(claim_key)
        if not aliases:
            # Unknown claim: require the raw claim token in work type.
            token = claim.strip().casefold()
            return bool(token) and token in entry.work_type_cf
        # website_build is strict — do not treat bare "digital marketing" as a site build
        if claim_key in {"website", "website_build"}:
            strict = (
                "website",
                "web site",
                "web build",
                "site build",
                "web development",
                "custom mortgage calculator",
            )
            return any(a in entry.work_type_cf for a in strict)
        return any(a in entry.work_type_cf for a in aliases)

    def public_clients_for_claim(self, claim: str) -> list[ClientListEntry]:
        out: list[ClientListEntry] = []
        for entry in self.entries:
            if not entry.is_public_yes:
                continue
            if self.work_type_supports_claim(entry, claim):
                out.append(entry)
        return out


def _normalize_client_name(name: str) -> str:
    n = (name or "").strip().casefold()
    n = re.sub(r"\s+", " ", n)
    n = re.sub(r"^(the\s+)?(city|county|town)\s+of\s+", "", n)
    return n.strip()


def parse_client_list_markdown(text: str) -> ClientListRegistry:
    """Parse markdown tables from 01_ClientList_Approved.md."""
    entries: list[ClientListEntry] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        raw = line.strip()
        if not raw.startswith("|"):
            continue
        if _HEADER_SKIP.match(raw) or re.search(r"\|\s*Client\s*\|", raw, re.I):
            continue
        m = _TABLE_ROW_RE.match(raw)
        if not m:
            continue
        client = m.group("client").strip()
        if not client or client.casefold() == "client":
            continue
        public = m.group("public").strip()
        # Normalize Public cell to Yes / Confirm
        pub_cf = public.casefold()
        if "confirm" in pub_cf:
            public_norm = "Confirm"
        elif pub_cf == "yes":
            public_norm = "Yes"
        else:
            public_norm = public
        entry = ClientListEntry(
            name=client,
            sector=m.group("sector").strip(),
            work_type=m.group("work").strip(),
            public=public_norm,
        )
        key = entry.name.casefold()
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    return ClientListRegistry(entries=entries)
