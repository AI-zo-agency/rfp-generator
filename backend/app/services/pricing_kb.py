"""Pricing docs: the one place that knows where zö agency's prices and pricing rules live.

Three Markdown docs of the same version, uploaded through the knowledge base:

  Pricing Book       client prices (searchable, category pricing)
  Rules and Wording  prose and approved text (searchable, category pricing)
  Pricing Internal   settings, loaded rates, catalog costs (hidden, category pricing_internal)

Each doc starts with a Field/Value table (Document, Version, Effective, Valid through,
Description). The engine reads numbers only from named headings and named table
columns; a doc that breaks the template is refused with every problem listed, so a
bad upload never reaches an agent. Free prose is handed to the AI, never parsed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date

logger = logging.getLogger(__name__)

BOOK, RULES, INTERNAL = "Pricing Book", "Rules and Wording", "Pricing Internal"
DOC_NAMES = (BOOK, RULES, INTERNAL)
PO_COLUMNS = ("Creative Director", "Strategist", "Copywriter", "Photo & Video")

SETTINGS = {  # name -> is a percentage
    "Margin floor": True,
    "Target multiplier": False,
    "Blended rate": False,
    "Minimum price per in-house hour": False,
    "Nonprofit discount": True,
    "Traditional media commission": True,
}
BILLING_NAMES = (
    "Standard 50/25/25", "Production 100", "Government Monthly", "Retainer Monthly", "Lump Sum at Completion",
)
WORDING = (
    "Monthly Retainer", "Fixed Quote, private client", "Government contract",
    "Time & Materials, Time & Deliverables, Not to Exceed", "Production", "Outside the price",
    "Travel", "Nonprofit discount", "Change orders", "Rates",
)
HEADINGS = {
    BOOK: ("Catalog", "Hourly rates and fees", "Digital media fees", "Billing terms"),
    RULES: ("How zö agency prices", "RFP pricing rules", "Standing rules", "Approved wording",
            "Budget page guide", "Lines to replace in old drafts"),
    INTERNAL: ("Settings", "Negotiated rates", "Roles", "Vendors", "Markup tiers", "Catalog costs",
               "Task library", "Notes for the pricing AI"),
}


class PricingDocError(ValueError):
    """A pricing doc breaks the template. `problems` lists every reason, in plain words."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass(frozen=True)
class Settings:
    margin_floor: float
    target_multiple: float
    blended_rate: float
    min_price_per_hour: float
    nonprofit_discount: float
    traditional_commission: float

    @property
    def cost_ratio(self) -> float:
        """Share of the price that cost may take at the floor (0.47 at a 53% floor)."""
        return 1 - self.margin_floor


@dataclass(frozen=True)
class Role:
    name: str
    loaded: float


@dataclass(frozen=True)
class CatalogItem:
    code: str
    category: str
    item: str
    price: float
    unit: str
    description: str
    hours: dict[str, float] = field(default_factory=dict)
    pos: dict[str, float] = field(default_factory=dict)  # PO type -> dollars, "Other POs" included
    hard_cost: float = 0.0
    notes: str = ""


@dataclass(frozen=True)
class MediaTier:
    up_to: float  # inf for the last row
    percent: float
    minimum: float


@dataclass(frozen=True)
class PricingBook:
    version: str
    effective: date
    valid_through: date
    settings: Settings
    roles: dict[str, Role]
    negotiated_rates: dict[str, float]  # casefolded client -> hourly rate
    catalog: dict[str, CatalogItem]
    media_fees: list[MediaTier]
    billing_terms: dict[str, dict[str, str]]
    wording: dict[str, str]
    book_md: str
    rules_md: str
    internal_md: str

    def all_in_cost(self, item: CatalogItem) -> float:
        hours = sum(self.roles[k].loaded * h for k, h in item.hours.items())
        return hours + sum(item.pos.values()) + item.hard_cost

    def is_expired(self, today: date) -> bool:
        return today > self.valid_through


# ---------------------------------------------------------------- markdown helpers


def _cells(line: str) -> list[str]:
    s = line.strip().removeprefix("|").removesuffix("|")
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def _is_rule(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{2,}:?", c) for c in cells)


def _table(body: str) -> tuple[list[str], list[list[str]]] | None:
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("|") and i + 1 < len(lines) and _is_rule(_cells(lines[i + 1])):
            rows = []
            for row in lines[i + 2:]:
                if not row.startswith("|"):
                    break
                rows.append(_cells(row))
            return _cells(line), rows
    return None


def _split(md: str, level: str) -> dict[str, str]:
    parts = re.split(rf"(?m)^(?={level} )", md)
    out: dict[str, str] = {}
    for p in parts:
        if p.startswith(f"{level} "):
            title, _, body = p.partition("\n")
            out[title[len(level) + 1:].strip()] = body.strip()
    return out


def _number(text: str) -> float:
    return float(text.replace(",", "").replace("$", "").strip())


class _Doc:
    """One doc under check. Collects problems instead of stopping at the first."""

    def __init__(self, name: str, md: str):
        self.name, self.md, self.problems = name, md, []
        self.h2 = _split(md, "##")

    def bad(self, msg: str) -> None:
        self.problems.append(f"{self.name}: {msg}")

    def section(self, heading: str) -> str | None:
        if heading not in self.h2:
            self.bad(f"missing the heading '## {heading}'")
            return None
        return self.h2[heading]

    def rows(self, heading: str, columns: tuple[str, ...] | list[str], *, allow_empty: bool = True) -> list[dict[str, str]]:
        """Rows of the first table under `heading`, keyed by column name. Missing columns are reported."""
        body = self.section(heading)
        if body is None:
            return []
        t = _table(body)
        if t is None:
            self.bad(f"'{heading}' has no table")
            return []
        headers, data = t
        lower = {h.casefold(): h for h in headers}
        missing = [c for c in columns if c.casefold() not in lower]
        if missing:
            self.bad(f"'{heading}' table is missing the column(s) {', '.join(repr(m) for m in missing)}")
            return []
        if not data and not allow_empty:
            self.bad(f"'{heading}' table has no rows")
        return [{h: (r[i] if i < len(r) else "") for i, h in enumerate(headers)} for r in data]

    def num(self, where: str, column: str, text: str, *, blank: float | None = None) -> float | None:
        if text.strip() == "" and blank is not None:
            return blank
        try:
            return _number(text)
        except ValueError:
            self.bad(f"{where}: {text!r} in column {column} is not a number")
            return None


def _fields(md: str) -> dict[str, str]:
    t = _table(md.split("\n## ", 1)[0])
    if t is None:
        return {}
    return {r[0]: r[1] for r in t[1] if len(r) >= 2}


# ---------------------------------------------------------------- per-doc parsers


def _parse_book(d: _Doc) -> dict:
    catalog: dict[str, dict] = {}
    for r in d.rows("Catalog", ("Code", "Category", "Item", "Price", "Unit", "What the client gets")):
        code = r["Code"]
        price = d.num(f"Catalog {code}", "Price", r["Price"])
        if price is not None and price <= 0:
            d.bad(f"Catalog {code}: price must be above 0")
        if code in catalog:
            d.bad(f"Catalog code {code} appears twice")
        catalog[code] = {"code": code, "category": r["Category"], "item": r["Item"], "price": price,
                         "unit": r["Unit"], "description": r["What the client gets"]}
    d.rows("Hourly rates and fees", ("Item", "Client price"))
    media = []
    for r in d.rows("Digital media fees", ("Spend up to", "Fee percent", "Minimum fee"), allow_empty=False):
        raw = r["Spend up to"].strip().casefold()
        up_to = float("inf") if raw == "above" else d.num("Digital media fees", "Spend up to", raw)
        pct = d.num("Digital media fees", "Fee percent", r["Fee percent"])
        minimum = d.num("Digital media fees", "Minimum fee", r["Minimum fee"], blank=0.0)
        if None not in (up_to, pct, minimum):
            media.append(MediaTier(up_to, pct, minimum))
    if media and media[-1].up_to != float("inf"):
        d.bad("Digital media fees: the last row's 'Spend up to' must be 'above'")
    if [m.up_to for m in media] != sorted(m.up_to for m in media):
        d.bad("Digital media fees: rows must run from the lowest spend to the highest")
    terms = {r["Name"]: r for r in d.rows("Billing terms", ("Name", "How it bills", "Terms"), allow_empty=False)}
    for name in BILLING_NAMES:
        if terms and name not in terms:
            d.bad(f"Billing terms: missing the row '{name}'")
    return {"catalog": catalog, "media": media, "terms": terms}


def _parse_rules(d: _Doc) -> dict:
    for h in HEADINGS[RULES]:
        d.section(h)
    blocks = _split(d.h2.get("Approved wording", ""), "###")
    for name in WORDING:
        if d.h2.get("Approved wording") is not None and not blocks.get(name):
            d.bad(f"Approved wording: missing or empty '### {name}'")
    return {"wording": blocks}


def _parse_internal(d: _Doc) -> dict:
    settings: dict[str, float] = {}
    rows = {r["Setting"]: r["Value"] for r in d.rows("Settings", ("Setting", "Value"))}
    for name, is_pct in SETTINGS.items():
        if name not in rows:
            if rows:  # an unreadable table was already reported
                d.bad(f"Settings: missing the row '{name}'")
            continue
        text = rows[name].strip()
        if is_pct and not text.endswith("%"):
            d.bad(f"Settings: '{name}' must be a percentage with a % sign, got {text!r}")
            continue
        v = d.num(f"Settings '{name}'", "Value", text.removesuffix("%"))
        if v is not None:
            settings[name] = v / 100 if is_pct else v
    negotiated = {}
    for r in d.rows("Negotiated rates", ("Client", "Rate")):
        v = d.num(f"Negotiated rates '{r['Client']}'", "Rate", r["Rate"])
        if v is not None:
            negotiated[r["Client"].casefold()] = v
    roles = {}
    for r in d.rows("Roles", ("Key", "Role", "Loaded cost per hour"), allow_empty=False):
        v = d.num(f"Roles {r['Key']}", "Loaded cost per hour", r["Loaded cost per hour"])
        if v is not None:
            roles[r["Key"]] = Role(r["Role"], v)
    d.rows("Vendors", ("PO type", "Usual vendor", "Rate"))
    d.rows("Markup tiers", ("Multiplier", "Gross profit", "When to use"))
    costs: dict[str, dict] = {}
    role_cols = list(roles)
    columns = ["Code", *role_cols, *PO_COLUMNS, "Other POs", "Hard cost"]
    for r in d.rows("Catalog costs", columns, allow_empty=False):
        code = r["Code"]
        where = f"Catalog costs {code}"
        hours = {k: v for k in role_cols if (v := d.num(where, k, r[k], blank=0.0))}
        pos = {c: v for c in (*PO_COLUMNS, "Other POs") if (v := d.num(where, c, r[c], blank=0.0))}
        hard = d.num(where, "Hard cost", r["Hard cost"], blank=0.0) or 0.0
        costs[code] = {"hours": hours, "pos": pos, "hard": hard, "notes": r.get("Other PO names", "")}
    for h in ("Task library", "Notes for the pricing AI"):
        body = d.section(h)
        if body is not None and not body:
            d.bad(f"'{h}' is empty")
    return {"settings": settings, "negotiated": negotiated, "roles": roles, "costs": costs}


PARSERS = {BOOK: _parse_book, RULES: _parse_rules, INTERNAL: _parse_internal}


def _header(md: str, problems: list[str]) -> tuple[str | None, str | None, date | None, date | None]:
    f = _fields(md)
    doc, version = f.get("Document"), f.get("Version")
    if doc not in DOC_NAMES:
        problems.append("This is not one of the three zö pricing docs (the top table needs 'Document' = "
                        + ", ".join(DOC_NAMES) + ")")
        return None, None, None, None
    if not version or not re.fullmatch(r"v\d+(\.\d+)*", version):
        problems.append(f"{doc}: 'Version' must look like v2 or v2.1, got {version!r}")
    dates = []
    for key in ("Effective", "Valid through"):
        try:
            dates.append(date.fromisoformat(f.get(key, "")))
        except ValueError:
            problems.append(f"{doc}: '{key}' must be a date like 2026-12-31, got {f.get(key)!r}")
            dates.append(None)
    if not f.get("Description"):
        problems.append(f"{doc}: 'Description' is empty")
    return doc, version, dates[0], dates[1]


def check_doc(md: str) -> tuple[str | None, str | None, list[str]]:
    """Check one doc on its own (what an upload sees). Returns (doc name, version, problems)."""
    problems: list[str] = []
    doc, version, *_ = _header(md, problems)
    if doc is None:
        return None, None, problems
    d = _Doc(doc, md)
    PARSERS[doc](d)
    return doc, version, problems + d.problems


# ---------------------------------------------------------------- the whole set


def build_book(book_md: str, rules_md: str, internal_md: str) -> PricingBook:
    """Parse and cross-check the three docs. Raises PricingDocError listing every problem."""
    problems: list[str] = []
    heads = {name: _header(md, problems) for name, md in ((BOOK, book_md), (RULES, rules_md), (INTERNAL, internal_md))}
    for name, (doc, *_rest) in heads.items():
        if doc is not None and doc != name:
            problems.append(f"{name} slot holds a '{doc}' doc")
    docs = {n: _Doc(n, md) for n, md in ((BOOK, book_md), (RULES, rules_md), (INTERNAL, internal_md))}
    parsed = {n: PARSERS[n](d) for n, d in docs.items()}
    for d in docs.values():
        problems += d.problems
    versions = {n: h[1] for n, h in heads.items() if h[1]}
    if len(set(versions.values())) > 1:
        problems.append("The three docs must carry the same version, got "
                        + ", ".join(f"{n} {v}" for n, v in versions.items()))

    book, internal = parsed[BOOK], parsed[INTERNAL]
    costs = internal["costs"]
    for code in book["catalog"]:
        if code not in costs:
            problems.append(f"Catalog costs: no cost row for code {code}, which is in the Pricing Book")
    for code in costs:
        if code not in book["catalog"]:
            problems.append(f"Catalog costs: code {code} is not in the Pricing Book catalog")
    if len(internal["settings"]) < len(SETTINGS) and not any("Settings" in p for p in problems):
        problems.append("Settings: some settings could not be read")
    if problems:
        raise PricingDocError(problems)

    s = internal["settings"]
    return PricingBook(
        version=heads[BOOK][1],
        effective=heads[BOOK][2],
        valid_through=heads[BOOK][3],
        settings=Settings(
            margin_floor=s["Margin floor"], target_multiple=s["Target multiplier"],
            blended_rate=s["Blended rate"], min_price_per_hour=s["Minimum price per in-house hour"],
            nonprofit_discount=s["Nonprofit discount"], traditional_commission=s["Traditional media commission"],
        ),
        roles=internal["roles"],
        negotiated_rates=internal["negotiated"],
        catalog={
            c: CatalogItem(**v, hours=costs[c]["hours"], pos=costs[c]["pos"], hard_cost=costs[c]["hard"],
                           notes=costs[c]["notes"])
            for c, v in book["catalog"].items()
        },
        media_fees=book["media"],
        billing_terms=book["terms"],
        wording=parsed[RULES]["wording"],
        book_md=book_md,
        rules_md=rules_md,
        internal_md=internal_md,
    )


# ---------------------------------------------------------------- loading from the knowledge base


@dataclass(frozen=True)
class PricingState:
    book: PricingBook
    skipped: dict[str, list[str]]  # newer versions that were passed over -> why


_cache: dict[tuple[str, ...], PricingBook] = {}
_failed: dict[tuple[str, ...], list[str]] = {}


def looks_like_pricing_doc(md: str) -> bool:
    return _fields(md).get("Document") in DOC_NAMES


def _version_key(v: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", v))


def _stamp(doc: dict) -> str:
    return str(doc.get("updatedAt") or doc.get("createdAt") or "")


def _meta(doc: dict) -> dict:
    return doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}


def pick_sets(docs: list[dict]) -> list[tuple[str, dict[str, dict]]]:
    """Versions that have all three docs, newest version first. The newest upload wins per doc."""
    by_version: dict[str, dict[str, dict]] = {}
    for doc in docs:
        name, version = _meta(doc).get("pricingDoc"), _meta(doc).get("pricingVersion")
        if name in DOC_NAMES and version:
            slot = by_version.setdefault(str(version), {})
            if name not in slot or _stamp(doc) > _stamp(slot[name]):
                slot[name] = doc
    full = [(v, s) for v, s in by_version.items() if len(s) == len(DOC_NAMES)]
    return sorted(full, key=lambda vs: _version_key(vs[0]), reverse=True)


async def _read_original(doc: dict) -> str:
    """The uploaded file exactly as written (tables intact). Falls back to Supermemory's indexed text."""
    import httpx

    from app.services import supermemory

    key = supermemory.document_fetch_key(doc)
    if not key:
        raise PricingDocError([f"A pricing doc has no fetchable id in Supermemory: {doc.get('title')!r}"])
    try:
        url = await supermemory.get_document_file_url(document_key=key)
        if url:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
    except (supermemory.SupermemoryError, httpx.HTTPError) as exc:
        logger.warning("pricing doc %s: file download failed, using indexed text: %s", key, exc)
    return await supermemory.get_document_content(custom_id=key)


async def describe_upload(version: str) -> str:
    """One plain sentence for the person who just uploaded a doc of `version`."""
    from app.services import supermemory

    docs = await supermemory.list_all_container_documents(force_refresh=True)
    have = {n for d in docs if _meta(d).get("pricingVersion") == version and (n := _meta(d).get("pricingDoc")) in DOC_NAMES}
    missing = [n for n in DOC_NAMES if n not in have]
    try:
        state = await load_pricing_book()
    except Exception:  # noqa: BLE001 - nothing usable yet is a normal first-upload state
        state = None
    keeps = f" Budgets keep using {state.book.version}." if state else " No pricing version is live yet."
    if missing:
        return f"Uploaded. Still needed for {version}: {', '.join(missing)}.{keeps}"
    if state and state.book.version == version:
        old = sorted({str(_meta(d)["pricingVersion"]) for d in docs if _meta(d).get("pricingVersion") not in (None, version)})
        tail = f" Older docs ({', '.join(old)}) are still in the knowledge base; delete them so agents see one Pricing Book." if old else ""
        b = state.book
        return (f"Pricing {version} is live: {len(b.catalog)} catalog items, valid through {b.valid_through:%B %d, %Y}. "
                f"Budgets now use {version}.{tail}")
    if state and version in state.skipped:
        return f"Uploaded, but the {version} set is not usable yet: {'; '.join(state.skipped[version][:5])}.{keeps}"
    return f"Uploaded. {version} is not newer than the live set.{keeps}"


async def load_pricing_book(*, force_refresh: bool = False) -> PricingState:
    """The newest complete, valid set of pricing docs in the knowledge base."""
    from app.services import supermemory
    from app.services.proposal_common import ProposalError

    docs = await supermemory.list_all_container_documents(force_refresh=force_refresh)
    skipped: dict[str, list[str]] = {}
    for version, slot in pick_sets(docs):
        ids = tuple(str(slot[n].get("customId") or slot[n].get("id")) for n in DOC_NAMES)
        if ids in _cache:
            return PricingState(_cache[ids], skipped)
        if ids in _failed:
            skipped[version] = _failed[ids]
            continue
        try:
            book = build_book(*[await _read_original(slot[n]) for n in DOC_NAMES])
        except PricingDocError as exc:
            _failed[ids] = skipped[version] = exc.problems
            logger.warning("pricing docs %s skipped: %s", version, exc)
            continue
        _cache[ids] = book
        return PricingState(book, skipped)
    have = sorted({str(_meta(d)["pricingDoc"]) for d in docs if _meta(d).get("pricingDoc")})
    detail = "; ".join(f"{v}: {'; '.join(p[:3])}" for v, p in skipped.items())
    raise ProposalError(
        "Pricing docs not usable. The knowledge base needs the Pricing Book, Rules and Wording and "
        f"Pricing Internal of the same version (found: {', '.join(have) or 'none'}). {detail}".strip(),
        status_code=424,
    )
