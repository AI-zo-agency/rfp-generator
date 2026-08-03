"""Relevance windowing keeps the search-matched passage instead of the head slice."""

from app.services.proposal_knowledge_base_tools import relevance_window


def test_short_content_returned_whole():
    content = "Sonja Anderson has 14 years of experience."
    out, windowed = relevance_window(content, "14 years", 1000)
    assert out == content
    assert windowed is False


def test_window_keeps_passage_buried_past_the_cap():
    # The fact sits well past a head-truncation boundary.
    filler = "irrelevant boilerplate. " * 2000
    fact = "Sonja Anderson holds a BFA and has 14 years of experience."
    content = filler + fact + filler

    head = content[:4000]
    assert fact not in head, "precondition: head slice must miss the fact"

    out, windowed = relevance_window(content, fact, 4000)
    assert windowed is True
    assert fact in out
    assert len(out) <= 4000


def test_window_matches_anchor_with_different_whitespace():
    filler = "x " * 5000
    fact = "Certifications: WBENC and WOSB only."
    content = filler + fact + filler
    # Anchor arrives whitespace-collapsed / reflowed, as chunk text often does.
    anchor = "Certifications:   WBENC\n and   WOSB only."

    out, windowed = relevance_window(content, anchor, 3000)
    assert windowed is True
    assert "WBENC" in out


def test_falls_back_to_head_slice_when_anchor_absent():
    content = "a" * 10_000
    out, windowed = relevance_window(content, "nothing that appears", 1000)
    assert windowed is True
    assert out == content[:1000]


def test_empty_anchor_falls_back_without_error():
    content = "b" * 5000
    out, windowed = relevance_window(content, "", 500)
    assert windowed is True
    assert len(out) == 500
