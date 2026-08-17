"""Tests for bio stub + designer-note Option B."""

from app.services.proposal_bio_stub import (
    extract_engagement_role,
    format_bio_stub_content,
    is_bio_pdf_designer_note,
    is_bio_stub_section,
    rfp_requires_inline_bios,
    resolve_bio_pdf_filename,
    skip_inline_bio_expansion,
    stub_from_extraction,
)
from app.services.proposal_rfp_optional_claim_scrub import strip_designer_notes


def test_rfp_inline_required_clear_language() -> None:
    assert rfp_requires_inline_bios(
        "Offerors shall include resumes in the proposal body within the page limit."
    )


def test_rfp_default_attachment_ok_when_silent() -> None:
    assert not rfp_requires_inline_bios("Describe your team approach and methodology.")


def test_rfp_attachment_language_not_inline() -> None:
    assert not rfp_requires_inline_bios(
        "Attach resumes as Appendix B. Do not include full CVs in the narrative."
    )


def test_format_stub_has_designer_note_no_key_accounts() -> None:
    body = format_bio_stub_content(
        member="Sonja Anderson",
        role="Account lead",
        kb_available=True,
        inline_required=False,
    )
    assert "Sonja Anderson" in body
    assert "Account lead" in body
    assert "Insert approved bio PDF — 04_Bio_SonjaAnderson.pdf" in body
    assert "**Key Accounts**" not in body
    assert "SCHOTT" not in body
    assert is_bio_pdf_designer_note(body)


def test_missing_pdf_manual_fill() -> None:
    body = format_bio_stub_content(
        member="Sonja Anderson",
        role="Lead",
        kb_available=False,
    )
    assert "MANUAL FILL: Ella" in body
    assert "not found" in body.casefold()


def test_inline_bullets_only_from_kb_text() -> None:
    kb = "# YEARS OF EXPERIENCE\nSEO Strategy | 4+ years\n# KEY ACCOUNTS\nInvented Client Corp\n"
    body = format_bio_stub_content(
        member="Harsh Mohite",
        role="PPC",
        kb_text=kb,
        kb_available=True,
        inline_required=True,
    )
    assert "SEO Strategy" in body or "4" in body
    # Key Accounts heading must not be emitted even if present in source text blob
    assert "**Key Accounts**" not in body


def test_stub_from_extraction_drops_key_accounts_field() -> None:
    body = stub_from_extraction(
        member="Sonja Anderson",
        role="Lead",
        pdf_filename="04_Bio_SonjaAnderson.pdf",
        kb_text="irrelevant",
        kb_available=True,
        inline_required=False,
        extracted={"title": "CEO", "key_accounts": ["University of Washington", "SCHOTT"]},
    )
    assert "University of Washington" not in body
    assert "SCHOTT" not in body
    assert "CEO" not in body  # inline_required False → no title block


def test_resolve_filename_from_sources() -> None:
    name = resolve_bio_pdf_filename(
        "Sonja Anderson",
        ["folder/04_Bio_SonjaAnderson.pdf"],
    )
    assert name == "04_Bio_SonjaAnderson.pdf"


def test_scrub_preserves_bio_pdf_designer_note() -> None:
    body = (
        "### Sonja\n\n"
        "[DESIGNER NOTE: Insert approved bio PDF — 04_Bio_SonjaAnderson.pdf. "
        "Do not rewrite Key Accounts or work history in-manuscript.]\n\n"
        "[DESIGNER NOTE: render as swimlane diagram]\n"
    )
    out, n = strip_designer_notes(body)
    assert n == 1
    assert "Insert approved bio PDF" in out
    assert "swimlane" not in out.casefold()


def test_is_bio_stub_section() -> None:
    content = format_bio_stub_content(member="Curt Schultz", role="Creative")
    assert is_bio_stub_section("section-2-bio-curt-schultz", content)
    assert not is_bio_stub_section("section-1-who-we-are", content)


def test_extract_engagement_role_drops_resume_prose() -> None:
    body = (
        "### Ella Lindau\n"
        "**Role on this engagement:** Account and Operations Manager. "
        "She has 5 years with zö and previously led accounts at agencies.\n"
        "ACCOUNT AND OPERATIONS MANAGER | 5 YEARS WITH ZÖ AGENCY\n"
    )
    assert extract_engagement_role(body) == "Account and Operations Manager"


def test_skip_inline_bio_expansion_default() -> None:
    assert skip_inline_bio_expansion("Describe your team approach.")
    assert not skip_inline_bio_expansion(
        "Offerors shall include resumes in the proposal body within the page limit."
    )
