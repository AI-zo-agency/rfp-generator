"""Tests for RFP-optional claim scrub (Option B)."""

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_rfp_optional_claim_scrub import (
    apply_optional_claim_scrub_to_draft,
    scrub_named_subcontractors_when_rfp_silent,
    scrub_percent_time_when_rfp_silent,
    scrub_section_optional_claims,
    strip_auditor_echo_manual_fills,
    strip_designer_notes,
    strip_handoff_tags_for_scan,
)


def test_strip_designer_notes() -> None:
    body = "Intro.\n\n[DESIGNER NOTE: render as swimlane]\n\nOutro."
    out, n = strip_designer_notes(body)
    assert n == 1
    assert "DESIGNER NOTE" not in out
    assert "Intro." in out and "Outro." in out


def test_strip_auditor_echo_manual_fills_keeps_real_handoff() -> None:
    body = (
        "We deliver.\n\n"
        "[MANUAL FILL: Sonja — Section ends mid-sentence without terminal punctuation]\n"
        "[MANUAL FILL: Sonja — attach signed W-9]\n"
    )
    out, n = strip_auditor_echo_manual_fills(body)
    assert n == 1
    assert "mid-sentence" not in out
    assert "W-9" in out


def test_strip_handoff_tags_for_scan_exposes_terminal_punct() -> None:
    body = (
        "We serve public universities with clarity.\n\n"
        "[MANUAL FILL: Sonja — Section ends mid-sentence without terminal punctuation]"
    )
    scanned = strip_handoff_tags_for_scan(body)
    assert scanned.endswith(".")
    assert "MANUAL FILL" not in scanned


def test_percent_time_stripped_when_rfp_silent() -> None:
    body = "Account lead: 35% dedication to this engagement."
    out, n = scrub_percent_time_when_rfp_silent(body, rfp_text="Provide a methodology.")
    assert n >= 1
    assert "35%" not in out
    assert "[VERIFY: percent time]" not in out.casefold()


def test_percent_time_kept_when_rfp_requires() -> None:
    body = "Account lead: 35% dedication."
    rfp = "Offerors shall state percent-time allocation for each key person."
    out, n = scrub_percent_time_when_rfp_silent(body, rfp_text=rfp)
    assert n == 0
    assert "35%" in out


def test_named_sub_removed_when_rfp_silent() -> None:
    body = "Team\n\n- Subcontractor: Acme LLC\n\nWe staff in-house."
    out, n = scrub_named_subcontractors_when_rfp_silent(
        body, rfp_text="Describe your approach."
    )
    assert n == 1
    assert "Acme" not in out
    assert "in-house" in out


def test_named_sub_kept_when_rfp_requires() -> None:
    body = "- Subcontractor: Acme LLC\n"
    rfp = "Identify each proposed subcontractor by name."
    out, n = scrub_named_subcontractors_when_rfp_silent(body, rfp_text=rfp)
    assert n == 0
    assert "Acme" in out


def test_apply_to_draft_skips_budget_id() -> None:
    draft = ProposalDraft(
        rfpId="r1",
        updatedAt="t",
        sections=[
            ProposalSection(
                id="s1",
                title="Approach",
                content="Hi.\n[DESIGNER NOTE: chart]\n",
                status="generated",
            ),
            ProposalSection(
                id="budget",
                title="Pricing",
                content="[DESIGNER NOTE: keep table]\n$1",
                status="generated",
            ),
        ],
    )
    updated, logs = apply_optional_claim_scrub_to_draft(
        draft, rfp_text="", skip_section_ids={"budget"}
    )
    assert any("DESIGNER NOTE" in line for line in logs)
    assert "DESIGNER NOTE" not in (updated.sections[0].content or "")
    assert "DESIGNER NOTE" in (updated.sections[1].content or "")


def test_scrub_section_optional_claims_strips_unsupported_claim_flags() -> None:
    body = (
        "We modernized Deschutes County tourism.\n\n"
        "[FLAG: claim 'tourism_mci' not supported for Deschutes County — "
        "ClientList work type is 'Brand modernization']"
    )
    out, logs = scrub_section_optional_claims(body, rfp_text="Design services RFP.")
    assert "[FLAG:" not in out
    assert any("FLAG" in line for line in logs)


def test_scrub_rewrites_unsupported_website_before_stripping_flags() -> None:
    from app.services.evidence_trust.client_list import parse_client_list_markdown

    registry = parse_client_list_markdown(
        """
| Client | Sector | Work Type | Public |
|---|---|---|---|
| Oregon Employment Department | State Government | Precision geofencing and digital targeting | Yes |
| Deschutes Brand Only Co | Title Insurance | Brand modernization and branded templates | Yes |
"""
    )
    draft = ProposalDraft(
        rfpId="r1",
        updatedAt="t",
        sections=[
            ProposalSection(
                id="cs-oed",
                title="OED",
                content=(
                    "[FLAG: claim 'website_build' not supported for Oregon Employment Department]\n\n"
                    "Oregon Employment Department campaigns drove hits on our website "
                    "and stronger renewal intent."
                ),
                status="generated",
            ),
            ProposalSection(
                id="cs-dct",
                title="Deschutes",
                content=(
                    "For Deschutes Brand Only Co we created a professional website with "
                    "custom-programmed mortgage calculators."
                ),
                status="generated",
            ),
        ],
    )
    updated, logs = apply_optional_claim_scrub_to_draft(
        draft, rfp_text="Design services.", registry=registry
    )
    oed = updated.sections[0].content or ""
    dct = updated.sections[1].content or ""
    assert "[FLAG:" not in oed
    assert "hits on our website" not in oed.casefold()
    assert "geofencing" in oed.casefold()
    assert "mortgage calculator" not in dct.casefold()
    assert "professional website" not in dct.casefold()
    assert any("corrected" in line.casefold() for line in logs)
