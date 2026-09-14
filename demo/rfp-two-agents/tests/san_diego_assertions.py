"""Shared assertions for County of San Diego RFQ 13180 opportunity JSON."""

from __future__ import annotations

import json
import re
from typing import Any


def assert_san_diego_opportunity(data: dict[str, Any]) -> list[str]:
    """Return list of failure messages (empty = pass)."""

    failures: list[str] = []
    u = data.get("understanding") or {}
    client = str(u.get("client") or "")
    if "san diego" not in client.casefold() and "public works" not in json.dumps(u).casefold():
        failures.append("client should reference County of San Diego / Public Works")

    mf = u.get("memoryFacts")
    if not isinstance(mf, dict) or not mf.get("clientName"):
        failures.append("memoryFacts missing")

    tl = u.get("timelineIntel") or {}
    tl_s = json.dumps(tl)
    if "september 21, 2026" not in tl_s.casefold() and "2026-09-21" not in tl_s:
        failures.append("questionsDue September 21, 2026")
    if "october 30, 2026" not in tl_s.casefold() and "2026-10-30" not in tl_s:
        failures.append("quotesDue October 30, 2026")
    if "july 1, 2027" not in tl_s.casefold():
        failures.append("initial term July 1, 2027")
    if not re.search(r"four.*one[- ]year.*option|4.*one[- ]year", tl_s, re.I):
        failures.append("four one-year option periods")

    comp_items = (data.get("compliance") or {}).get("items") or []

    def find_req(*needles: str) -> dict[str, Any] | None:
        for it in comp_items:
            if not isinstance(it, dict):
                continue
            r = str(it.get("requirement") or "").casefold()
            if all(n.casefold() in r for n in needles):
                return it
        return None

    for form in ("pc600", "pc601", "pc610"):
        it = find_req(form)
        if not it:
            failures.append(f"{form.upper()} compliance missing")
        elif it.get("mandatory") is not True:
            failures.append(f"{form.upper()} should be mandatory=true")

    pc620 = find_req("pc620")
    if pc620 and pc620.get("mandatory") is not False:
        failures.append("PC620 conditional mandatory=false")

    sow_red = find_req("sow") or find_req("redline")
    if sow_red and sow_red.get("mandatory") is not False:
        failures.append("SOW redline conditional")

    refs = find_req("reference") or find_req("three")
    if not refs:
        failures.append("three references requirement")
    ref_blob = json.dumps(comp_items).casefold()
    if "one page" not in ref_blob and "1 page" not in ref_blob:
        failures.append("reference page limit each")

    sub = find_req("subcontract")
    if sub and sub.get("mandatory") is not False:
        failures.append("subcontractor conditional")

    pay = find_req("payment", "schedule") or find_req("payment schedule")
    if not pay:
        failures.append("Payment Schedule missing")
    elif pay.get("mandatory") is not True:
        failures.append("Payment Schedule mandatory")

    draft = find_req("draft", "agreement") or find_req("accept")
    if draft and draft.get("mandatory") is not True:
        failures.append("Draft Agreement acceptance mandatory")

    exc = find_req("exception")
    if exc and exc.get("mandatory") is not False:
        failures.append("exceptions conditional")

    w9 = find_req("w-9") or find_req("w9")
    if w9 and w9.get("mandatory") is not False:
        failures.append("W-9 post-award mandatory=false")

    s186 = find_req("18662") or find_req("§18662")
    if s186 and s186.get("mandatory") is not False:
        failures.append("§18662 post-award mandatory=false")

    ev = data.get("evaluation") or {}
    if ev.get("scoredResponseForm") is not False:
        failures.append("scoredResponseForm false")
    if ev.get("totalPoints") not in (None, ""):
        failures.append("totalPoints null")
    if ev.get("criteria"):
        failures.append("criteria empty")

    emph = " ".join(str(x) for x in (ev.get("emphasis") or [])).casefold()
    for term in ("technical", "experience", "capacity", "price", "sustainability", "cultural"):
        if term not in emph and term not in json.dumps(ev).casefold():
            failures.append(f"evaluation emphasis missing {term}")

    scope = data.get("scope") or {}
    scope_blob = json.dumps(scope).casefold()
    for phrase in (
        "comprehensive plan",
        "master messaging",
        "smart",
        "evaluation",
        "multilingual",
        "multicultural",
        "waterscape",
        "poo points",
    ):
        if phrase not in scope_blob:
            failures.append(f"scope missing {phrase}")

    opt_blob = json.dumps(scope.get("optional") or []).casefold()
    if "as needed" not in opt_blob and "as-needed" not in scope_blob:
        failures.append("scope.optional as-needed assessment")
    if "up to six" not in opt_blob and "up to 6" not in opt_blob:
        failures.append("scope.optional up to six presentations")

    notes = str(scope.get("notes") or "").casefold()
    if "monthly" not in notes or "quarterly" not in notes:
        failures.append("scope notes monthly vs quarterly conflict")

    all_scope = scope_blob + opt_blob
    if "at least one" not in all_scope and "at least 1" not in all_scope:
        failures.append("at least one presentation annually preserved")
    if "up to two" not in all_scope and "up to 2" not in all_scope:
        failures.append("up to two additional presentations")
    if "up to four" not in all_scope and "four meetings" not in all_scope:
        failures.append("up to four meetings annually")

    for out in u.get("desiredOutcomes") or []:
        ol = str(out).casefold()
        if any(x in ol for x in ("demonstrated", "experience", "qualification", "local knowledge")):
            failures.append(f"qualification in desiredOutcomes: {out}")

    sc_items = (data.get("successCriteria") or {}).get("items") or []
    for it in sc_items:
        c = str((it or {}).get("criterion") or "").casefold()
        if "page number" in c or "insurance" in c and "maintain" in c:
            failures.append(f"admin success criterion: {c}")

    if not data.get("provenance"):
        failures.append("provenance non-empty")

    allowed = {"understanding", "compliance", "scope", "evaluation", "successCriteria", "provenance"}
    extra = set(data.keys()) - allowed
    if extra:
        failures.append(f"extra top-level keys: {extra}")

    return failures
