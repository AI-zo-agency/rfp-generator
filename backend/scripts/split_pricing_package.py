"""Split the client's zo-pricing .skill package into three knowledge-base docs.

    python scripts/split_pricing_package.py <package.skill> <out_dir>

1. Pricing Book: catalog prices, fees, paid media, billing, standing rules.
   Client-safe: nothing here would hurt if it landed in a proposal.
2. Proposal Pricing Rules: engagement types, RFP pricing rules, approved
   wording and page guidance for the budget page. Client-safe.
3. Pricing Internal: the full methodology plus the task library. Holds
   costs, loaded rates, POs and margins. Confidential.

Rerun on every new package version (v3, the 2027 update) instead of editing
the outputs by hand.
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

# Sentences carrying costs, margins or PO vendors never reach docs 1 and 2.
# Staff names are public (proposals name the team), but in the methodology they mark
# internal approvals ("Sonja decides"), so those rules are dropped too.
# ponytail: hand lists, update them when section 3 of the methodology gains a person or vendor.
LEAK = re.compile(
    r"53%|73%|gross profit|margin|all-in cost|loaded|\bPOs?\b|hard cost|0\.47|÷|×|3\.7 times|"
    r"floor price|cost ceiling|markup tier|net profit|utilization|"
    r"Curt|Justin|Citizen|Gil\b|Ben Edwards|Treeline|Morgan|Tisha|E2M",
    re.I,
)
STAFF = re.compile(
    r"Sonja|Ella|Alejandro|Shawn|Haley|Oyetola|Kelvin|Marcelle|Murilo|Miguel|Todd|Jon Dunnington|Ron\b"
)
INTERNAL = re.compile(f"{LEAK.pattern}|{STAFF.pattern}", re.I)
UNIT = {"One-time": "", "Monthly": " / month", "Quarterly": " / quarter"}
PAGE_COPY_SKIP = ("Line codes", "Price at a glance", "Reply format")  # skill-output specific


def read_package(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as z:
        return {Path(n).name: z.read(n).decode() for n in z.namelist() if n.endswith((".md", ".json"))}


def sections(md: str, level: str = "## ") -> dict[str, str]:
    """Heading text -> full section text (heading included)."""
    parts = re.split(rf"(?m)^(?={re.escape(level)})", md)
    return {p.splitlines()[0][len(level):].strip(): p.strip() for p in parts if p.startswith(level)}


def by_number(secs: dict[str, str], n: int) -> str:
    return next(v for k, v in secs.items() if k.startswith(f"{n}."))


def scrub(md: str, bad: re.Pattern = INTERNAL) -> str:
    """Drop table rows and sentences that match `bad`."""
    out = []
    for line in md.splitlines():
        if line.startswith("|"):
            if not bad.search(line):
                out.append(line)
            continue
        sents = re.split(r"(?<=[.!?])\s+", line)
        # A rule whose opening sentence is internal goes entirely; the rest would dangle.
        if bad.search(sents[0]):
            continue
        out.append(" ".join(s for s in sents if not bad.search(s)))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def demote(md: str) -> str:
    return re.sub(r"(?m)^(#+) ", r"#\1 ", md)


def price(item: dict) -> str:
    b = item["billing"]
    unit = UNIT.get(b, " / " + b.removeprefix("Per ").lower())
    return f"${item['price']:,.0f}{unit}"


def header(title: str, version: str, audience: str) -> str:
    return (
        f"# {title}\n\n**{version} · generated from the client's zo-pricing package · "
        f"do not edit by hand**\n\n{audience}\n"
    )


def build(files: dict[str, str]) -> dict[str, str]:
    meth = files["pricing-methodology.md"]
    catalog = json.loads(files["catalog.json"])
    m = re.search(r"\*\*(v\d+ · [^·*]+)", meth)
    version = m.group(1).strip() if m else "version unknown"
    secs = sections(meth)

    tabs: dict[str, list[str]] = {}
    for it in catalog["items"]:
        tabs.setdefault(it["tab"], []).append(
            f"| {it['code']} | {it['item']} | {price(it)} | {it['description']} |"
        )
    book = [header(
        "zö agency Pricing Book: services and client prices", version,
        "Client prices for every catalog item, with the blended hourly rate, fees, paid media "
        f"management, billing terms and standing rules. {catalog['note'].split('. ', 1)[-1].split('. Items below')[0]}.",
    )]
    book.append("## Catalog\n\nCatalog codes match the Pricing Book, the operations workbook and the client PDF.")
    for i, (tab, rows) in enumerate(tabs.items(), 1):
        book.append(f"### Tab {i}. {tab}\n\n| Code | Item | Price | What the client gets |\n|---|---|---|---|\n" + "\n".join(rows))
    book.append("## Blended hourly rate\n\nzö agency bills a blended rate of $275 an hour for every role. "
                "It never gives rates by role.")
    book += [scrub(by_number(secs, n)) for n in (10, 6, 7, 8)]

    copy_guide = sections(files["page-copy-guide.md"])
    rules = [header(
        "zö agency Proposal Pricing Rules: how to write the budget page", version,
        "How zö agency prices proposals and RFP responses, the approved wording for billing and "
        "terms, and what goes on the scope, timeline and budget pages. Catalog prices are in the "
        "Pricing Book document.",
    )]
    # Section 4 (engagement types) is cost math; its billing lines are in the standard blocks.
    rules.append(scrub(by_number(secs, 9)))
    blocks = files["standard-blocks.md"].split("\n", 1)[1]  # drop the file's own title
    rules.append("## Standard blocks: approved wording\n\n" + demote(scrub(blocks, LEAK)))
    rules.append("## Page copy guide\n\n" + "\n\n".join(
        demote(scrub(v, LEAK)) for k, v in copy_guide.items() if not k.startswith(PAGE_COPY_SKIP)
    ).replace("`standard-blocks.md`", "the standard blocks above"))

    internal = [header(
        "zö agency Pricing Internal: costs, rates and margins (confidential)", version,
        "Confidential. Loaded rates, POs, all-in costs, margins and the floor math behind every "
        "price. For the pricing engine and Sonja's approved quoters. Never quote from this "
        "document in a proposal.",
    ), meth.split("\n\n", 2)[2].strip(), "---\n\n" + demote(files["task-library.md"])]  # [2]: skip title, version

    return {
        "01_Pricing_Book_client_prices.md": "\n\n".join(book) + "\n",
        "02_Pricing_Rules_for_proposals.md": "\n\n".join(rules) + "\n",
        "03_Pricing_Internal_confidential.md": "\n\n".join(internal) + "\n",
    }


def check(docs: dict[str, str], files: dict[str, str]) -> None:
    safe = docs["01_Pricing_Book_client_prices.md"] + docs["02_Pricing_Rules_for_proposals.md"]
    leaks = sorted({m.group(0) for m in LEAK.finditer(safe)})
    assert not leaks, f"costs or vendors leaked into client-safe docs: {leaks}"
    staff = sorted({m.group(0) for m in STAFF.finditer(docs["01_Pricing_Book_client_prices.md"])})
    assert not staff, f"staff names in the Pricing Book: {staff}"
    codes = [i["code"] for i in json.loads(files["catalog.json"])["items"]]
    missing = [c for c in codes if f"| {c} |" not in docs["01_Pricing_Book_client_prices.md"]]
    assert not missing, f"catalog codes missing from the Pricing Book: {missing}"
    assert "$275 an hour" in safe


if __name__ == "__main__":
    pkg, out = Path(sys.argv[1]), Path(sys.argv[2])
    files = read_package(pkg)
    docs = build(files)
    check(docs, files)
    out.mkdir(parents=True, exist_ok=True)
    for name, text in docs.items():
        (out / name).write_text(text)
        print(f"{name}: {len(text):,} chars")
