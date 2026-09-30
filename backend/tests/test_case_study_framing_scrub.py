from app.services.proposal_integrity_guards import _scrub_case_study_false_framing as scrub


def test_terminal_period_kept_and_no_trailing_space():
    out, logs = scrub("We rebuilt the site instead of starting from a blank page.\n\nNext para.")
    assert out == "We rebuilt the site.\n\nNext para."
    assert logs == ["Case study: scrubbed false framing"]


def test_no_space_before_comma():
    out, _ = scrub("We rebuilt the site instead of starting from a blank page, and it grew.")
    assert out == "We rebuilt the site, and it grew."


def test_newline_not_consumed():
    out, _ = scrub("Work.\ninstead of starting from a blank page here")
    assert out == "Work.\ninstead of starting from a blank page here"
    out, _ = scrub("Work.\nWe rebuilt instead of starting from a blank page here")
    assert out == "Work.\nWe rebuilt here"


def test_existing_asset_uses_prior_engagement_replacement():
    # Expected string follows the code's rule: the "existing asset" patterns
    # are replaced with "a prior engagement".
    out, _ = scrub("This was built on an existing asset for a client.")
    assert out == "This was a prior engagement for a client."


def test_unchanged_input_is_byte_identical():
    text = "Two  spaces here \nand a trailing tab\t\n\n\nkept ."
    out, logs = scrub(text)
    assert out == text
    assert logs == []
