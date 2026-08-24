"""The three real failures from the first live brief are the fixtures here.

None of them contains a digit, which is the whole reason this module exists.
"""

import pytest

from app.financial.figure_guard import (
    check_magnitude_claims,
    check_quantities,
    evidence_numbers,
    parse_quantities,
)


def _evidence() -> dict:
    """A trimmed copy of the evidence the failing brief was actually given."""
    return {
        "signals": [
            {
                "id": "ar-late",
                "figure": "$1,200",
                "detail": "2% of what's owed. Oldest is OCF at 72 days.",
            },
            {
                "id": "ap-over-cash",
                "figure": "$30,901",
                "detail": "$38,643 in bills against $7,742 cash.",
            },
            {"id": "segment-gap", "figure": "$288,199", "detail": "72% of income is classified."},
        ],
        "derived": {"ap_to_cash_ratio": "5.0x", "aged_share_pct": "2%"},
        "chase": [
            {"id": "chase:ocf", "client": "OCF", "figure": "$11,966", "oldest_days": 72,
             "invoices": 5},
        ],
        "hygiene": [
            {"id": "hygiene:unclassified-income", "figure": "$288,199", "amount": 288_198.76},
        ],
    }


# ── the three live failures ──────────────────────────────────────────────────

def test_a_figure_verbalised_at_the_wrong_magnitude_is_rejected():
    # Handed the string "$288,199", the model wrote this. Off by 2.6x, no digit.
    offender = check_quantities(
        "Nearly three-quarters of a million in revenue carries no line-of-business "
        "assignment.",
        evidence_numbers(_evidence()),
    )
    assert offender is not None
    assert "three-quarters" in offender


def test_a_ratio_the_model_derived_itself_is_rejected():
    # Given "$38,643 in bills against $7,742 cash", it computed 4x. Actual 4.99x.
    offender = check_quantities(
        "Bills due total nearly four times what we have on hand.",
        evidence_numbers(_evidence()),
    )
    assert offender is not None
    assert "four" in offender


def test_a_magnitude_claim_with_no_stated_share_is_rejected():
    # Aged AR is $1,200 in total. OCF is not "the bulk" of anything.
    assert check_magnitude_claims(
        "OCF and Mt. View represent the bulk of our aging receivables."
    ) == "the bulk of"


# ── correct prose must survive, or the guard gets switched off ───────────────

def test_correct_verbalisation_passes():
    # The model's own good notes read like this. If these fail, the guard is
    # useless because nobody will leave it turned on.
    allowed = evidence_numbers(_evidence())
    assert check_quantities("Seventy-two days out and still unpaid.", allowed) is None
    assert check_quantities("OCF is already at seventy-two days.", allowed) is None


def test_a_figure_quoted_verbatim_passes():
    allowed = evidence_numbers(_evidence())
    assert check_quantities("$288,199 of income is unclassified.", allowed) is None
    assert check_quantities("The ratio sits at 5.0x.", allowed) is None
    assert check_quantities("That is 2% of what's owed.", allowed) is None


def test_number_words_without_quantity_context_are_ignored():
    # These are the false positives that would sink the guard.
    allowed = evidence_numbers(_evidence())
    assert check_quantities("One thing to watch is collections.", allowed) is None
    assert check_quantities("The first of these is the oldest.", allowed) is None
    assert check_quantities("No single client dominates.", allowed) is None


def test_counts_that_appear_in_the_evidence_pass():
    allowed = evidence_numbers(_evidence())
    assert check_quantities("Two of the five invoices are recent.", allowed) is None


def test_a_magnitude_claim_backed_by_a_stated_share_passes():
    assert check_magnitude_claims("Most of the balance, some 62%, is current.") is None
    assert check_magnitude_claims("The majority — 88 percent — is not yet due.") is None


def test_a_magnitude_claim_is_judged_per_sentence():
    # The share in the first sentence does not license the claim in the second.
    assert check_magnitude_claims(
        "Coverage is 72%. OCF is the bulk of the aging book."
    ) == "the bulk of"


def test_clean_prose_with_no_quantities_passes_both_checks():
    assert check_quantities("Collections held up this week.", set()) is None
    assert check_magnitude_claims("Collections held up this week.") is None


# ── the parser itself ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("about one day", 1),
        ("nearly nine days", 9),
        ("about twelve days", 12),
        ("nearly nineteen days", 19),
        ("about twenty days", 20),
        ("seventy-two days", 72),
        ("ninety-nine days", 99),
        ("two hundred days", 200),
        ("two hundred fifty days", 250),
        ("nearly a hundred days", 100),
        ("three thousand", 3_000),
        ("nearly a million", 1_000_000),
        ("two million", 2_000_000),
        ("half of a million", 500_000),
        ("a third of a million", 333_333),
        ("two-thirds of a million", 666_667),
        ("three-quarters of a million", 750_000),
        ("three quarters of a million", 750_000),
    ],
)
def test_word_number_parser(text, expected):
    verbal = [q for q in parse_quantities(text) if q[2]]
    assert verbal, f"nothing parsed out of {text!r}"
    assert round(verbal[0][1]) == expected


@pytest.mark.parametrize(
    "hedge",
    ["nearly", "almost", "about", "roughly", "approximately", "around", "over", "under"],
)
def test_every_hedge_puts_a_bare_number_word_into_quantity_context(hedge):
    verbal = [q for q in parse_quantities(f"{hedge} seventy-two") if q[2]]
    assert verbal and verbal[0][1] == 72


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$288,199", 288_199),
        ("288199", 288_199),
        ("$1,200", 1_200),
        ("72", 72),
        ("2%", 2),
        ("5.0x", 5.0),
        ("1.2 million", 1_200_000),
        ("$288k", 288_000),
    ],
)
def test_digit_parser(text, expected):
    digits = [q for q in parse_quantities(text) if not q[2]]
    assert digits and digits[0][1] == expected


# ── the allowed set ──────────────────────────────────────────────────────────

def test_evidence_numbers_collects_raw_values_and_formatted_strings():
    allowed = evidence_numbers(_evidence())
    assert 288_199 in allowed          # from the "$288,199" figure string
    assert 288_198.76 in allowed       # from the raw amount beside it
    assert 72 in allowed
    assert 5.0 in allowed              # from the "5.0x" derived ratio


def test_evidence_numbers_ignores_booleans():
    # bool is an int subclass; True must not license the quantity 1.
    assert evidence_numbers({"slow_payer": True, "flag": False}) == set()


def test_evidence_numbers_walks_nested_lists_and_dicts():
    assert evidence_numbers({"a": [{"b": [7]}]}) == {7.0}


def test_a_rounded_restatement_of_a_real_figure_is_still_rejected():
    # "$288k" is the right magnitude and still forbidden: rule 1 says verbatim.
    assert check_quantities("$288k is unclassified.", evidence_numbers(_evidence()))
