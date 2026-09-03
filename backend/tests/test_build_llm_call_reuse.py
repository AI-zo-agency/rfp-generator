"""Unit tests for the two build-time cost fixes (R2 memo, R3 lock reuse).

No network/LLM calls: chat_json is patched to a counting stub.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.models.proposal import ManuscriptLocks
from app.services import proposal_fulfill_rfp_structure as structure_mod
from app.services.proposal_generator import _locks_are_plan_informed


# ---------------------------------------------------------------------------
# R2: extract_rfp_submission_format_specs process-local memo
# ---------------------------------------------------------------------------


def _valid_payload():
    return (
        {
            "sections": [
                {
                    "rfpTitle": "Technical Approach",
                    "requiredHeadings": [],
                    "instructions": "Describe your approach.",
                    "sameAskAs": [],
                    "satisfiedByStaticCompanyBlock": False,
                }
            ]
        },
        "test-provider",
    )


def _empty_payload():
    return ({"sections": []}, "test-provider")


class FormatSpecsMemoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # The memo is process-local module state — clear it so test order
        # can't make a later test pass only because an earlier test already
        # populated it.
        structure_mod._FORMAT_SPECS_CACHE.clear()

    def tearDown(self) -> None:
        structure_mod._FORMAT_SPECS_CACHE.clear()

    async def test_two_identical_calls_produce_one_chat_json_call(self) -> None:
        calls = {"n": 0}

        async def fake_chat_json(*args, **kwargs):
            calls["n"] += 1
            return _valid_payload()

        with mock.patch.object(structure_mod.llm, "is_configured", return_value=True), \
            mock.patch.object(structure_mod.llm, "chat_json", side_effect=fake_chat_json):
            specs1 = await structure_mod.extract_rfp_submission_format_specs(
                "Some RFP text describing submission format.",
                rfp_title="RFP One",
                existing_section_titles=["Cover Letter"],
            )
            specs2 = await structure_mod.extract_rfp_submission_format_specs(
                "Some RFP text describing submission format.",
                rfp_title="RFP One",
                existing_section_titles=["Cover Letter"],
            )

        self.assertEqual(calls["n"], 1)
        self.assertEqual(len(specs1), 1)
        self.assertEqual(len(specs2), 1)
        self.assertEqual(specs1[0].rfp_title, specs2[0].rfp_title)

    async def test_different_existing_titles_produce_two_calls(self) -> None:
        calls = {"n": 0}

        async def fake_chat_json(*args, **kwargs):
            calls["n"] += 1
            return _valid_payload()

        with mock.patch.object(structure_mod.llm, "is_configured", return_value=True), \
            mock.patch.object(structure_mod.llm, "chat_json", side_effect=fake_chat_json):
            await structure_mod.extract_rfp_submission_format_specs(
                "Some RFP text.",
                rfp_title="RFP One",
                existing_section_titles=["Cover Letter"],
            )
            await structure_mod.extract_rfp_submission_format_specs(
                "Some RFP text.",
                rfp_title="RFP One",
                existing_section_titles=["Executive Summary"],
            )

        self.assertEqual(calls["n"], 2)

    async def test_empty_result_is_not_cached(self) -> None:
        calls = {"n": 0}

        async def fake_chat_json(*args, **kwargs):
            calls["n"] += 1
            return _empty_payload()

        with mock.patch.object(structure_mod.llm, "is_configured", return_value=True), \
            mock.patch.object(structure_mod.llm, "chat_json", side_effect=fake_chat_json):
            specs1 = await structure_mod.extract_rfp_submission_format_specs(
                "Some RFP text.",
                rfp_title="RFP One",
                existing_section_titles=["Cover Letter"],
            )
            specs2 = await structure_mod.extract_rfp_submission_format_specs(
                "Some RFP text.",
                rfp_title="RFP One",
                existing_section_titles=["Cover Letter"],
            )

        self.assertEqual(specs1, [])
        self.assertEqual(specs2, [])
        self.assertEqual(calls["n"], 2)


# ---------------------------------------------------------------------------
# R3: _locks_are_plan_informed skip predicate
# ---------------------------------------------------------------------------


def _locks(**overrides) -> ManuscriptLocks:
    base = dict(
        primaryContactName="Jane Doe",
        primaryContactTitle="Senior Account Manager",
        needsHumanConfirm=False,
        builtWithPlan=True,
    )
    base.update(overrides)
    return ManuscriptLocks.model_validate(base)


def test_plan_informed_complete_locks_are_reused():
    assert _locks_are_plan_informed(_locks()) is True


def test_none_locks_force_rebuild():
    assert _locks_are_plan_informed(None) is False


def test_empty_primary_contact_forces_rebuild():
    assert _locks_are_plan_informed(_locks(primaryContactName="")) is False


def test_needs_human_confirm_forces_rebuild():
    assert _locks_are_plan_informed(_locks(needsHumanConfirm=True)) is False


def test_not_built_with_plan_forces_rebuild():
    assert _locks_are_plan_informed(_locks(builtWithPlan=False)) is False
