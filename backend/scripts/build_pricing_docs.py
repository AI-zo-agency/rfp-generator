"""Convert the client's zo-pricing .skill package into the 3 pricing docs the app reads.

    python scripts/build_pricing_docs.py <package.skill> <out_dir>

One-time converter: after the first upload, the pricing team edits the three docs
directly and the package is not needed again. Format rules live in
app/services/pricing_kb.py (the loader refuses a doc that breaks them).

  Pricing Book         client prices            category pricing, searchable
  Rules and Wording    prose and approved text  category pricing, searchable
  Pricing Internal     costs, rates, settings   category pricing_internal, hidden
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

PO_COLUMNS = ["Creative Director", "Strategist", "Copywriter", "Photo & Video"]
ROLE_ORDER = ["DS", "PD", "WD", "DG", "AM", "PM", "LD"]
SKIP_GUIDE_SECTIONS = ("Line codes", "Price at a glance", "Reply format")
# Sentences that carry costs, margins or PO vendors never reach the searchable docs.
LEAK = re.compile(
    r"53%|73%|gross profit|margin|all-in cost|loaded|\bPOs?\b|hard cost|0\.47|÷|×|3\.7 times|"
    r"floor price|cost ceiling|markup tier|net profit|utilization|"
    r"Curt|Justin|Citizen|Gil\b|Ben Edwards|Treeline|Morgan|Tisha|E2M",
    re.I,
)


SLOT_FIXES = [
    ("\n- [The rest of the lines for the engagement type.]", ""),
    ("[retainer / quote]", "[retainer or quote]"),
    ("[ad spend, which runs on [client]'s card on the ad account; PR and media relations; printing; "
     "new photo or video shoots; any website rebuild]", "[outside items]"),
    ("[the RFP's rule, such as GSA rates]", "[travel rule]"),
]


def read_package(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as z:
        return {Path(n).name: z.read(n).decode() for n in z.namelist() if n.endswith((".md", ".json"))}


def sections(md: str, level: str = "## ") -> dict[str, str]:
    """Heading text -> body text (heading line excluded)."""
    parts = re.split(rf"(?m)^(?={re.escape(level)})", md)
    return {
        p.splitlines()[0][len(level):].strip(): p.split("\n", 1)[1].strip() if "\n" in p else ""
        for p in parts
        if p.startswith(level)
    }


def numbered(secs: dict[str, str], n: int) -> str:
    return next(v for k, v in secs.items() if k.startswith(f"{n}."))


def scrub(md: str) -> str:
    """Drop table rows and sentences that match LEAK. A rule whose first sentence leaks goes entirely."""
    out = []
    for line in md.splitlines():
        if line.startswith("|"):
            if not LEAK.search(line):
                out.append(line)
            continue
        sents = re.split(r"(?<=[.!?])\s+", line)
        if LEAK.search(sents[0]):
            continue
        out.append(" ".join(s for s in sents if not LEAK.search(s)))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def demote(md: str, by: int = 1) -> str:
    return re.sub(r"(?m)^(#+) ", lambda m: "#" * (len(m.group(1)) + by) + " ", md)


def cell(v: object) -> str:
    return str(v).replace("|", "/").replace("\n", " ").strip()


def table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def md_rows(md: str) -> list[list[str]]:
    """Data rows of the first pipe table in md, as cell lists."""
    rows = [l for l in md.splitlines() if l.startswith("|")]
    return [[c.strip() for c in r.strip("|").split("|")] for r in rows[2:]]


def num(text: str) -> float:
    return float(text.replace(",", "").replace("$", ""))


def header(doc: str, version: str, effective: str, valid: str, description: str) -> str:
    title = {"Pricing Internal": "zö agency Pricing Internal (confidential)"}.get(doc, f"zö agency {doc}")
    meta = table(
        ["Field", "Value"],
        [["Document", doc], ["Version", version], ["Effective", effective],
         ["Valid through", valid], ["Description", description]],
    )
    return f"# {title}\n\n{meta}\n"


def iso(text: str) -> str:
    return datetime.strptime(text.strip(), "%B %d, %Y").strftime("%Y-%m-%d")


def build(files: dict[str, str]) -> dict[str, str]:
    meth = files["pricing-methodology.md"]
    cat = json.loads(files["catalog.json"])
    people = json.loads(files["people.json"])
    secs = sections(meth)

    head = re.search(r"\*\*(v\d+) · ([A-Za-z]+ \d+, \d{4})", meth)
    version, effective = head.group(1), iso(head.group(2))
    valid = iso(re.search(r"hold through ([A-Za-z]+ \d+, \d{4})", meth).group(1))
    stamp = (version, effective, valid)

    # ---- Doc 1: Pricing Book
    catalog_rows = [
        [i["code"], i["tab"], i["item"], f"{i['price']:g}", i["billing"], i["description"]]
        for i in cat["items"]
    ]
    fees_rows = [r[1:] for r in md_rows(numbered(secs, 10))]
    media_rows = []
    for _, spend, fee in md_rows(numbered(secs, 6)):
        pct = re.match(r"(\d+)%", fee).group(1)
        minimum = re.search(r"\$([\d,]+) minimum", fee)
        media_rows.append([
            "above" if "above" in spend else f"{num(spend.split()[0]):g}",
            pct,
            f"{num(minimum.group(1)):g}" if minimum else "",
        ])
    billing_rows = [r[1:] for r in md_rows(numbered(secs, 7))]
    billing_notes = "\n\n".join(
        l for l in numbered(secs, 7).splitlines() if re.match(r"\*\*7[f-h]\.", l)
    )
    traditional = next(l for l in numbered(secs, 6).splitlines() if l.startswith("**6a.")).replace("**6a.** ", "")
    book = "\n\n".join([
        header("Pricing Book", *stamp,
               "Client prices for every zö service, fees, media management and billing terms. Safe to quote."),
        "## Catalog\n\n" + table(["Code", "Category", "Item", "Price", "Unit", "What the client gets"], catalog_rows),
        "## Hourly rates and fees\n\n" + table(["Item", "Client price"], fees_rows),
        "## Digital media fees\n\n" + scrub(traditional) + "\n\n"
        + "Digital media: zö agency bills a monthly management fee, and the client's card stays on the ad account. "
        "The fee tapers with monthly spend.\n\n"
        + table(["Spend up to", "Fee percent", "Minimum fee"], media_rows),
        "## Billing terms\n\n" + table(["Name", "How it bills", "Terms"], billing_rows) + "\n\n" + scrub(billing_notes),
    ]) + "\n"

    # ---- Doc 2: Rules and Wording
    blocks = sections(files["standard-blocks.md"])
    billing_part = blocks["Billing and terms, by engagement type"].split("\n## Lines to delete", 1)[0]
    rename = {
        "Government contract (any engagement type)": "Government contract",
        "Production (print, swag, merchandise)": "Production",
    }
    approved = {rename.get(k, k): v for k, v in sections(billing_part, "### ").items()}
    for key, name in (("Outside the price", "Outside the price"), ("Travel", "Travel"),
                      ("Nonprofit discount", "Nonprofit discount"), ("Change orders", "Change orders"),
                      ("Rates, when an RFP asks for them", "Rates"), ("Tagline", "Tagline")):
        approved[name] = blocks[key]
    # The engine fills simple [slot] names from the plan; the client's free-text brackets become slots.
    for old, new in SLOT_FIXES:
        hit = [k for k, v in approved.items() if old in v]
        assert hit, f"slot text not found in the approved wording: {old[:50]!r}"
        approved = {k: v.replace(old, new) for k, v in approved.items()}
    guide = sections(files["page-copy-guide.md"])
    rules = "\n\n".join([
        header("Rules and Wording", *stamp,
               "How zö agency prices proposals, the approved wording for billing and terms, and the budget page guide."),
        "## How zö agency prices\n\n" + scrub("\n\n".join(
            l for l in numbered(secs, 9).splitlines() if re.match(r"\*\*9[abh]\.", l))),
        "## RFP pricing rules\n\n" + scrub("\n\n".join(
            l for l in numbered(secs, 9).splitlines() if re.match(r"\*\*9[cefgij]\.", l))),
        "## Standing rules\n\n" + scrub(numbered(secs, 8)),
        "## Approved wording\n\n" + "\n\n".join(f"### {k}\n\n{scrub(v)}" for k, v in approved.items()),
        "## Budget page guide\n\n" + "\n\n".join(
            f"### {k}\n\n{scrub(v)}" for k, v in guide.items() if not k.startswith(SKIP_GUIDE_SECTIONS)),
        "## Lines to replace in old drafts\n\n" + blocks["Lines to delete from older drafts"].split("\n\n", 1)[1],
    ]) + "\n"

    # ---- Doc 3: Pricing Internal
    roles = {p["key"]: p for p in people["people"]}
    keys = [k for k in ROLE_ORDER if k in roles]
    assert set(keys) == set(roles), f"unexpected role keys {set(roles) - set(keys)}"
    markup = [r[1:] for r in md_rows(numbered(secs, 5))]  # drop the methodology's row-label column
    cost_rows = []
    for i in cat["items"]:
        pos = i["po_by_topic"]
        other = {k: v for k, v in pos.items() if k not in PO_COLUMNS}
        cost_rows.append(
            [i["code"]]
            + [f"{i['hours_by_role'].get(k, 0):g}" if k in i["hours_by_role"] else "" for k in keys]
            + [f"{pos[c]:g}" if c in pos else "" for c in PO_COLUMNS]
            + [f"{sum(other.values()):g}" if other else ""]
            + [f"{i['hard_cost']:g}" if i["hard_cost"] else ""]
            + ["; ".join(re.sub(r"^Other: ", "", k) for k in other)]
        )
    internal = "\n\n".join([
        header("Pricing Internal", *stamp,
               "Confidential. Settings, loaded rates, vendors, costs behind every catalog price and the pricing rules for the budget AI."),
        "## Settings\n\n" + table(["Setting", "Value"], [
            ["Margin floor", "53%"], ["Target multiplier", "3.7"], ["Blended rate", "275"],
            ["Minimum price per in-house hour", "220"], ["Nonprofit discount", "12%"],
            ["Traditional media commission", "15%"],
        ]),
        "## Negotiated rates\n\n" + table(["Client", "Rate"], [["City of Bend", "250"]]),
        "## Roles\n\n" + table(["Key", "Role", "Loaded cost per hour"],
                               [[k, roles[k]["name"], f"{roles[k]['loaded']:g}"] for k in keys]),
        "## Vendors\n\n" + table(["PO type", "Usual vendor", "Rate"],
                                 [[k, v["usual"], v["rate"]] for k, v in people["po_topics"].items()]),
        "## Markup tiers\n\n" + table(["Multiplier", "Gross profit", "When to use"], markup),
        "## Catalog costs\n\n" + table(
            ["Code", *keys, *PO_COLUMNS, "Other POs", "Hard cost", "Other PO names"], cost_rows),
        "## Task library\n\n" + demote(files["task-library.md"].split("\n", 1)[1].strip(), 1),
        "## Notes for the pricing AI\n\n" + "\n\n".join(
            f"### {k}\n\n{numbered(secs, n)}"
            for n, k in ((1, "Rules"), (2, "Pricing math"), (3, "People and rates"),
                         (4, "Pricing by project type"), (9, "RFP and proposal pricing"),
                         (13, "Assumptions and open items"))
        ),
    ]) + "\n"

    return {
        "01_Pricing_Book.md": book,
        "02_Rules_and_Wording.md": rules,
        "03_Pricing_Internal.md": internal,
    }


if __name__ == "__main__":
    pkg, out = Path(sys.argv[1]), Path(sys.argv[2])
    docs = build(read_package(pkg))
    out.mkdir(parents=True, exist_ok=True)
    for name, text in docs.items():
        (out / name).write_text(text)
        print(f"{name}: {len(text):,} chars")
