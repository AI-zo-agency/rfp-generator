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


def test_a_real_company_background_label_still_wraps():
    # The wrapper must keep working for what it is actually for.
    draft = _draft()
    out, logs = ensure_company_block_wrapper_heading(
        draft,
        [RfpSectionSpec(rfp_title="Company Background",
                        satisfied_by_static_company_block=True)],
    )
    assert any("Company Background" == (s.title or "") for s in out.sections)
    assert any(COMPANY_BLOCK_NOTE in (s.content or "") for s in out.sections)


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
    assert is_rfp_company_identity_form_section(
        section_id="rfp-closing-company-info",
        title="Company Information Form",
        content="x",
    )
