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


def test_strip_designer_notes_preserves_all_notes() -> None:
    # The scan must NEVER vanish designer notes — they are legitimate handoffs
    # and are removed only at export. strip_designer_notes is a preserving no-op.
    body = "Intro.\n\n[DESIGNER NOTE: render as swimlane]\n\nOutro."
    out, n = strip_designer_notes(body)
    assert n == 0
    assert out == body
    assert "[DESIGNER NOTE: render as swimlane]" in out


def test_scrub_section_optional_claims_keeps_designer_note() -> None:
    # Full section scrub (as Complete & Clean runs it) leaves designer notes intact.
    body = "We deliver brand work.\n\n[DESIGNER NOTE: place hero image full-bleed]"
    out, logs = scrub_section_optional_claims(body, rfp_text="Design services RFP.")
    assert "[DESIGNER NOTE: place hero image full-bleed]" in out
    assert not any("DESIGNER NOTE" in log for log in logs)


def test_strip_auditor_echo_removes_deferred_upon_request_code() -> None:
    body = (
        "We maintain General Liability.\n\n"
        "[MANUAL FILL: Sonja — deterministic.fabricated_fact.deferred_information_upon_request_is_forbidden | DEFERRED INFORMATION]\n"
    )
    out, n = strip_auditor_echo_manual_fills(body)
    assert n == 1
    assert "deterministic" not in out
    assert "General Liability" in out


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
    # Uses an auditor-echo MANUAL FILL (which the scrub DOES remove) to prove the
    # budget id is skipped. Designer notes are asserted preserved in BOTH sections
    # — the scan must never vanish them.
    echo = "[MANUAL FILL: Sonja — Section ends mid-sentence without terminal punctuation]"
    draft = ProposalDraft(
        rfpId="r1",
        updatedAt="t",
        sections=[
            ProposalSection(
                id="s1",
                title="Approach",
                content=f"Hi.\n[DESIGNER NOTE: chart]\n{echo}\n",
                status="generated",
            ),
            ProposalSection(
                id="budget",
                title="Pricing",
                content=f"[DESIGNER NOTE: keep table]\n{echo}\n$1",
                status="generated",
            ),
        ],
    )
    updated, logs = apply_optional_claim_scrub_to_draft(
        draft, rfp_text="", skip_section_ids={"budget"}
    )
    # Non-budget section: auditor-echo removed, but its designer note survives.
    assert echo not in (updated.sections[0].content or "")
    assert "[DESIGNER NOTE: chart]" in (updated.sections[0].content or "")
    # Budget section skipped entirely: both tags untouched.
    assert echo in (updated.sections[1].content or "")
    assert "[DESIGNER NOTE: keep table]" in (updated.sections[1].content or "")
    # No designer-note removal is ever logged.
    assert not any("DESIGNER NOTE" in line for line in logs)


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
