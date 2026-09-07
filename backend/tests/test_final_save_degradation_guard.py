"""A section that entered Final checks with content must never be SAVED as a stub.

Incident (Gilroy Garlic Festival): `rfp-structure-executive-summary` held 553
words in the "Before final checks" snapshot and was persisted as:

    [MANUAL FILL: Draft this RFP-required section — Executive Summary]
    RFP-required outline: - Executive Summary
    RFP instructions: Articulate the agency's philosophy on partnering with ...

The user had to rebuild it by hand in section chat.

`restore_sections_emptied_by_scan` exists precisely to stop that, and it DOES
recognise this stub shape. It simply ran too early: five mutating passes follow
it — orphan-VERIFY repair, pointer integrity, markup polish, Ralph's page-limit
refit, and a reorder with add_missing_mandated_stubs=True — and then the draft
is saved with no further check.
"""

from __future__ import annotations

import inspect

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_draft_structure_stubs import (
    restore_sections_emptied_by_scan,
    section_is_rfp_draft_stub,
)

REAL_BODY = (
    "## Executive Summary\n\n"
    "Most of our engagements start with a website that already works, a brand "
    "that already has an audience, and a client who does not want to relearn a "
    "platform. Gilroy Garlic Festival is exactly that kind of client. Your "
    "website is newly rebuilt, your social channels have momentum, and your "
    "brand carries fifty years of community goodwill. Our philosophy for this "
    "kind of work is direct: optimize before you replace, and leave alone what "
    "is already earning trust. At Torrent Laboratories we focused on the pages "
    "with the greatest visibility rather than overhauling the site at once."
)

STUB_BODY = (
    "## Executive Summary\n\n"
    "[MANUAL FILL: Draft this RFP-required section — Executive Summary]\n\n"
    "RFP-required outline:\n- Executive Summary\n\n"
    "RFP instructions: Articulate the agency's philosophy on partnering with "
    "established legacy brands and optimizing existing digital/marketing "
    "assets (rather than rebuilding from scratch).\n\n"
    "Evaluation weight: Not separately weighted"
)


def _section(body: str) -> ProposalSection:
    return ProposalSection(
        id="rfp-structure-executive-summary",
        title="Executive Summary",
        content=body,
        source="rfp",
        mode="write",
        status="generated",
    )


def _draft(body: str) -> ProposalDraft:
    return ProposalDraft(
        rfpId="manual-test",
        updatedAt="2026-09-02T00:00:00Z",
        sections=[_section(body)],
    )


class TestTheGuardRecognisesTheRealStub:
    def test_the_exact_shipped_stub_is_classified_as_a_stub(self):
        assert section_is_rfp_draft_stub(_section(STUB_BODY))

    def test_the_real_body_is_not(self):
        assert not section_is_rfp_draft_stub(_section(REAL_BODY))

    def test_restoring_brings_the_real_body_back(self):
        restored, logs = restore_sections_emptied_by_scan(
            _draft(STUB_BODY), [_section(REAL_BODY)]
        )
        assert "optimize before you replace" in (restored.sections[0].content or "")
        assert logs

    def test_restoring_is_idempotent_on_healthy_content(self):
        """Safe to run twice — the whole point of calling it again before save."""
        healthy = _draft(REAL_BODY)
        once, _ = restore_sections_emptied_by_scan(healthy, [_section(REAL_BODY)])
        twice, logs = restore_sections_emptied_by_scan(once, [_section(REAL_BODY)])
        assert twice.sections[0].content == REAL_BODY
        assert logs == []


class TestGuardIsTheLastWordBeforeSave:
    """Ordering is the actual defect — the guard was never blind, just early."""

    def test_guard_runs_after_the_stub_adding_reorder(self):
        from app.services import proposal_fulfill_rfp_gaps as mod

        src = inspect.getsource(mod._run_fulfill_rfp_gaps_body)
        reorder = src.rindex("add_missing_mandated_stubs=True")
        guard = src.rindex("restore_sections_emptied_by_scan")
        save = src.index("await asave_proposal_draft(draft)", reorder)
        assert reorder < guard < save, (
            "the degradation guard must run after the mandated-stub reorder "
            "and before the final save"
        )

    def test_no_mutating_pass_sits_between_the_guard_and_the_save(self):
        from app.services import proposal_fulfill_rfp_gaps as mod

        src = inspect.getsource(mod._run_fulfill_rfp_gaps_body)
        guard = src.rindex("restore_sections_emptied_by_scan")
        save = src.index("await asave_proposal_draft(draft)", guard)
        between = src[guard:save]
        for mutator in (
            "repair_orphan_verify_leftovers_in_draft",
            "apply_pointer_page_integrity_to_draft",
            "apply_designer_ready_markup_polish_to_draft",
            "reassert_rfp_page_limit_after_content_passes",
            "_reorder_draft_to_rfp_toc",
        ):
            assert mutator not in between, f"{mutator} runs after the final guard"


class TestNewlyCreatedStubsAreFilledBeforeSave:
    """A stub minted at the end of Final checks must still get drafted.

    The only pass that turns a structure stub into real content —
    _finalize_fill_incomplete_tabs — ran near the START of the scan, while the
    reorder that CREATES stubs ran at the very end. Anything minted there was
    saved as "[MANUAL FILL: Draft this RFP-required section — X]" plus the RFP
    instructions, with nothing left to fill it. That is why References and
    Executive Summary shipped as instructions rather than content.
    """

    def test_stub_fill_runs_after_the_stub_creating_reorder(self):
        from app.services import proposal_fulfill_rfp_gaps as mod

        src = inspect.getsource(mod._run_fulfill_rfp_gaps_body)
        reorder = src.rindex("add_missing_mandated_stubs=True")
        fill = src.rindex("_finalize_fill_incomplete_tabs()")
        assert reorder < fill, (
            "stubs created by the final reorder would never be drafted"
        )

    def test_full_tail_order_is_create_then_fill_then_guard_then_save(self):
        from app.services import proposal_fulfill_rfp_gaps as mod

        src = inspect.getsource(mod._run_fulfill_rfp_gaps_body)
        reorder = src.rindex("add_missing_mandated_stubs=True")
        fill = src.rindex("_finalize_fill_incomplete_tabs()")
        guard = src.rindex("restore_sections_emptied_by_scan")
        save = src.index("await asave_proposal_draft(draft)", reorder)
        assert reorder < fill < guard < save, (
            "tail must be: create stubs -> fill them -> restore degraded -> save"
        )


class TestStructuredDataLossIsDegradation:
    """Losing the checkable facts is a regression even when the prose reads well.

    Real case: the References tab held three named contacts with working email
    addresses in a table, and came back as fluent prose saying "We are not able
    to publish complete reference contact records." Similar length, not
    stub-shaped, no word-count collapse — it passed every existing check while
    losing every fact the RFP asked for.
    """

    GOOD_REFS = (
        "## References\n\n"
        "| Organization | Contact | Phone / Email | Engagement Scope |\n"
        "|---|---|---|---|\n"
        "| Deschutes Public Library | Chantal Strobel | chantals@dpls.lib.or.us | "
        "Multi-year partnership 2013 to 2022 across five locations |\n"
        "| City of Bend | Mickie Derting | mderting@bendoregon.gov | "
        "Brand strategy and public narrative development |\n"
        "| City of Medford | Rich Rosenthal | rich.rosenthal@cityofmedford.org | "
        "Brand identity and pre-launch campaign |\n"
    )
    REFUSAL = (
        "## References\n\n"
        "We selected the two engagements profiled in Our Work because they mirror "
        "what the Association needs from us: multi-stakeholder marketing management "
        "for an organization with an established brand, active sponsors, and a "
        "community stake in the outcome. We are not able to publish complete "
        "reference contact records for those engagements in this proposal. When we "
        "do provide references, we will limit the list to contacts who can speak "
        "directly to the work being asked for.\n"
    )

    @staticmethod
    def _sec(body: str) -> ProposalSection:
        return ProposalSection(
            id="rfp-structure-references",
            title="References",
            content=body,
            source="rfp",
            mode="write",
            status="generated",
        )

    def _run(self, current: str, prior: str):
        draft = ProposalDraft(
            rfpId="t", updatedAt="2026-09-02T00:00:00Z", sections=[self._sec(current)]
        )
        return restore_sections_emptied_by_scan(draft, [self._sec(prior)])

    def test_deleted_contacts_are_restored(self):
        out, logs = self._run(self.REFUSAL, self.GOOD_REFS)
        assert "chantals@dpls.lib.or.us" in (out.sections[0].content or "")
        assert logs

    def test_the_log_names_the_real_reason(self):
        """A log saying 'reduced to a stub' sends the next debugger down the
        wrong path — this loss is deleted records, not a stub."""
        _out, logs = self._run(self.REFUSAL, self.GOOD_REFS)
        assert "contact record" in logs[0]
        assert "stub" not in logs[0]

    def test_prose_that_never_had_records_is_untouched(self):
        prior = (
            "Our approach to this engagement begins with a discovery phase that "
            "maps the existing brand, audience, and channel performance before we "
            "propose any change to what is already working well for the client."
        )
        current = (
            "Our approach begins with discovery: we map the existing brand, the "
            "audience, and channel performance before proposing any change at all."
        )
        _out, logs = self._run(current, prior)
        assert logs == [], "must not fire on a section that never carried records"

    def test_keeping_the_records_is_not_a_regression(self):
        _out, logs = self._run(self.GOOD_REFS, self.GOOD_REFS)
        assert logs == []
