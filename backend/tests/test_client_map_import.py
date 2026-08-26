from app.financial.client_map_import import collapse_tag_rows, rows_to_insert

RAW = [
    ["Tag Code", "Client", "City", "State", "Original A/M", "Current A/M", "Status", "Original Source", "Highest Value"],
    ["MVH", "Mountain View Heating", "Mountain View", "CA", "", "Sonja", "current", "RFP", "Over $10k"],
    ["MVH", "Mountain View Heating", "Mountain View", "CA", "", "Sonja", "current", "RFP", "Over $10k"],  # dupe
    ["ATC", "Arctic Chiller", "x", "OR", "", "", "current", "", ""],
    ["ATC", "Anti-Trafficking Criminal Intelligence Group", "y", "OR", "", "", "current", "", ""],
    ["zoa", "zo agency", "", "", "", "", "current", "", ""],
]


def test_collapse_keeps_exact_dupes_once_and_real_collisions():
    rows = collapse_tag_rows(RAW)
    tags = [(r["tag_code"], r["client_name"]) for r in rows]
    assert tags.count(("MVH", "Mountain View Heating")) == 1
    assert ("ATC", "Arctic Chiller") in tags
    assert ("ATC", "Anti-Trafficking Criminal Intelligence Group") in tags


def test_internal_flag_for_zo_agency():
    rows = collapse_tag_rows(RAW)
    zo = next(r for r in rows if r["tag_code"].upper() == "ZOA")
    assert zo["is_internal"] is True


def test_additive_skips_existing_tag_codes():
    collapsed = collapse_tag_rows(RAW)
    to_add = rows_to_insert(collapsed, existing_codes={"MVH"})
    assert all(r["tag_code"].upper() != "MVH" for r in to_add)
