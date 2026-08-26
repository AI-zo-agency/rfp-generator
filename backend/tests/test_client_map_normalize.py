from app.financial.client_map_normalize import normalize_name


def test_strips_legal_suffix_and_punctuation():
    assert normalize_name("Torrent Laboratories LLC") == "torrent laboratories"
    assert normalize_name("Clarus Eye Centre") == "clarus eye centre"
    # Mt. vs Mountain must NOT collapse here — Pass 1 stays exact; Pass 2 LLM covers that drift.
    assert normalize_name("Mt. View Heating, Inc.") != normalize_name("Mountain View Heating")
