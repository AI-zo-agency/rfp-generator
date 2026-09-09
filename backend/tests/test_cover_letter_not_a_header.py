"""A cover letter must never be reduced to a company-block header label.

Regression: the RFP's "Cover Letter / Cover Page" TOC row was claimed by
ensure_company_block_wrapper_heading as the label for Sections 1.1-1.5, so the
tab shipped holding only "[DESIGNER NOTE: Sections 1.1-1.5 follow immediately
below...]" and no letter was ever written. A letter is authored prose addressed
to the buyer; the company block is boilerplate about the firm.
"""

from __future__ import annotations

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_fulfill_rfp_structure import (
    RfpSectionSpec,
    _spec_is_static_company_ask,
    ensure_company_block_wrapper_heading,
)

COMPANY_BLOCK_NOTE = "follow immediately below"


def _draft() -> ProposalDraft:
    return ProposalDraft(
        rfpId="r1",
        updatedAt="2026-09-03T00:00:00Z",
        sections=[
            ProposalSection(
                id="section-1-who-we-are", title="1.1 — Who We Are",
                content="We are a full-service agency.",
                source="generated", mode="write", status="generated",
            ),
            ProposalSection(
                id="section-1-business-info", title="1.3 — Business Information",
                content="Legal name, address.",
                source="generated", mode="write", status="generated",
            ),
        ],
    )


def test_cover_letter_spec_is_not_a_static_company_ask():
    # Even when the extractor stamps satisfiedByStaticCompanyBlock — overlapping
    # firm facts do not make a letter a heading.
    spec = RfpSectionSpec(
        rfp_title="Cover Letter / Cover Page",
        satisfied_by_static_company_block=True,
    )
    assert _spec_is_static_company_ask(_draft(), spec) is False


def test_wrapper_does_not_consume_the_cover_letter():
    draft = _draft()
    out, logs = ensure_company_block_wrapper_heading(
        draft,
        [RfpSectionSpec(rfp_title="Cover Letter / Cover Page",
                        satisfied_by_static_company_block=True)],
    )
    for s in out.sections:
        assert COMPANY_BLOCK_NOTE not in (s.content or ""), (
            f"cover letter was turned into a header stub: {s.title}"
        )


def test_firm_profile_header_seeds_from_who_we_are_not_note_only():
    """Firm Profile chrome must not look emptied when 1.1 already has prose."""
    from app.services.proposal_fulfill_rfp_structure import (
        COMPANY_BLOCK_HEADER_ID,
        enrich_chrome_only_company_block_header,
        ensure_company_block_wrapper_heading,
        repair_empty_manuscript_sections,
    )

    draft = _draft()
    draft.sections[0] = draft.sections[0].model_copy(
        update={
            "content": (
                "zo means kindred, the people you gather close when the work matters. "
                "We built zö agency on that idea 13 years ago.\n\n"
                "We work with cities and counties because we believe in public service."
            )
        }
    )
    out, _logs = ensure_company_block_wrapper_heading(
        draft,
        [
            RfpSectionSpec(
                rfp_title="B. Firm Profile and Qualifications",
                satisfied_by_static_company_block=True,
            )
        ],
    )
    header = next(s for s in out.sections if s.id == COMPANY_BLOCK_HEADER_ID)
    assert "zo means kindred" in (header.content or "")
    assert "DESIGNER NOTE" not in (header.content or "").upper() or "Who We Are" in (
        header.content or ""
    )
    assert "1.1" in (header.content or "") or "Who We Are" in (header.content or "")
    # Full static package must appear — not a truncated chrome lead.
    assert len((header.content or "").split()) >= 40

    # Existing note-only header is repaired in place with full 1.1–1.5.
    chrome = out.model_copy(
        update={
            "sections": [
                header.model_copy(
                    update={
                        "content": (
                            "[DESIGNER NOTE: Sections 1.1–1.5 follow immediately below — "
                            "this header matches the RFP TOC label only.]"
                        )
                    }
                ),
                *out.sections[1:],
            ]
        }
    )
    fixed, logs = repair_empty_manuscript_sections(chrome)
    assert any("restored" in x.casefold() or "filled" in x.casefold() for x in logs)
    assert "zo means kindred" in (fixed.sections[0].content or "")
    assert "follow immediately below" not in (fixed.sections[0].content or "").casefold()


def test_empty_section_never_persists_blank():
    from app.services.proposal_fulfill_rfp_structure import repair_empty_manuscript_sections

    draft = ProposalDraft(
        rfpId="r1",
        updatedAt="2026-09-03T00:00:00Z",
        sections=[
            ProposalSection(
                id="rfp-approach",
                title="Technical Approach",
                content="",
                source="generated",
                mode="write",
                status="generated",
            ),
        ],
    )
    out, logs = repair_empty_manuscript_sections(draft)
    assert any("filled empty" in x.casefold() for x in logs)
    assert "[MANUAL FILL" in (out.sections[0].content or "").upper()
    assert (out.sections[0].content or "").strip()


def test_repair_converts_mistitled_company_header_to_cover_stub():
    from app.services.proposal_fulfill_rfp_structure import (
        COMPANY_BLOCK_HEADER_ID,
        repair_cover_letter_misused_as_company_header,
    )
    from app.services.proposal_draft_structure_stubs import (
        cover_letter_lacks_letter_body,
        section_needs_presubmit_fill,
    )

    draft = _draft()
    draft.sections.insert(
        0,
        ProposalSection(
            id=COMPANY_BLOCK_HEADER_ID,
            title="Cover Letter / Cover Page",
            content=(
                "## Cover Letter / Cover Page\n\n"
                "[DESIGNER NOTE: Sections 1.1–1.5 follow immediately below — "
                "this header matches the RFP TOC label only.]"
            ),
            source="generated",
            mode="write",
            status="generated",
        ),
    )
    out, logs = repair_cover_letter_misused_as_company_header(draft)
    letter = next(s for s in out.sections if "cover letter" in (s.title or "").casefold())
    assert letter.id != COMPANY_BLOCK_HEADER_ID
    assert COMPANY_BLOCK_NOTE not in (letter.content or "").casefold()
    assert cover_letter_lacks_letter_body(letter.content or "")
    assert section_needs_presubmit_fill(letter)
    assert any(s.id == COMPANY_BLOCK_HEADER_ID for s in out.sections)
    assert any("converted mistitled" in line.casefold() for line in logs)


def test_a_real_company_background_label_still_wraps():
    # The wrapper must keep working for what it is actually for.
    draft = _draft()
    out, logs = ensure_company_block_wrapper_heading(
        draft,
        [RfpSectionSpec(rfp_title="Company Background",
                        satisfied_by_static_company_block=True)],
    )
    assert any("Company Background" == (s.title or "") for s in out.sections)
    header = next(s for s in out.sections if (s.title or "") == "Company Background")
    assert "We are a full-service agency" in (header.content or "")
    assert COMPANY_BLOCK_NOTE not in (header.content or "").casefold()


def test_insurance_toc_row_is_not_company_block_header():
    """Bare '9. Insurance' must not become the Sections 1.1–1.5 header label."""
    draft = _draft()
    draft.sections.append(
        ProposalSection(
            id="section-1-insurance",
            title="1.5 — Insurance Information",
            content="We maintain GL and E&O coverage.",
            source="generated",
            mode="write",
            status="generated",
        )
    )
    specs = [
        RfpSectionSpec(rfp_title="9. Insurance", satisfied_by_static_company_block=True),
        RfpSectionSpec(rfp_title="Company Background", satisfied_by_static_company_block=True),
    ]
    assert _spec_is_static_company_ask(draft, specs[0]) is False
    out, _logs = ensure_company_block_wrapper_heading(draft, specs)
    insurance_headers = [
        s
        for s in out.sections
        if s.id == "rfp-structure-company-block-header"
        and "insurance" in (s.title or "").casefold()
    ]
    assert not insurance_headers, (
        f"Insurance became company-block header: {[s.title for s in insurance_headers]}"
    )
    assert any(
        s.id == "rfp-structure-company-block-header"
        and (s.title or "") == "Company Background"
        for s in out.sections
    )


def test_certification_of_proposal_is_not_static_company_ask():
    draft = _draft()
    spec = RfpSectionSpec(rfp_title="12. Certification of Proposal")
    assert _spec_is_static_company_ask(draft, spec) is False


# --- second site: the company-identity FORM compressor ---------------------

from app.services.proposal_section_dedup import (  # noqa: E402
    is_rfp_company_identity_form_section,
)


def test_cover_letter_is_not_a_company_identity_form():
    # The letter names contact person, RFP number and proposer — the same fields
    # an identity form carries. That overlap got it compressed to
    # "See 1.3 — Business Information", so no letter was ever written.
    assert not is_rfp_company_identity_form_section(
        section_id="rfp-checklister-24",
        title=(
            "Cover Letter/Cover Page including Contact Person, RFP Title, "
            "RFP#, and Name of Proposer"
        ),
        content="| Legal Business Name | Zo |\n| Contact Person | Sonja |",
    )
    # "Cover Letter Form" matches the identity-form title pattern AND contains
    # "form", so without the guard this returns True — isolates the guard.
    assert not is_rfp_company_identity_form_section(
        section_id="rfp-x", title="Cover Letter Form", content="x"
    )


def test_a_real_company_identity_form_is_still_compressed():
    # Content shape (identity field table, little else) — not the title string.
    assert is_rfp_company_identity_form_section(
        section_id="rfp-closing-company-info",
        title="Company Information Form",
        content=(
            "| Field | Response |\n| --- | --- |\n"
            "| Legal Name | Z'Onion Creative Group LLC |\n"
            "| DBA | zö agency |\n"
            "| FEIN | 46-1234567 |\n"
            "| Office Address | Bend, OR |\n"
            "| Contact Phone | (541) 350-2778 |\n"
        ),
    )
