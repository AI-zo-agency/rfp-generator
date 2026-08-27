"""Generate pipeline auto-chains the next Celery phase after success."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.proposal_generation_cancel import ProposalGenerationCancelled


class ChainNextGeneratePhaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_chains_phase_2_to_phase_3(self) -> None:
        from app.celery_app import _enqueue_next_generate_phase

        mock_result = MagicMock()
        mock_result.id = "task-phase-3"

        with (
            patch(
                "app.services.proposal_generation_cancel.check_generation_cancelled",
                new_callable=AsyncMock,
            ) as check_cancel,
            patch(
                "app.services.proposal_pipeline_checkpoint.record_phase_started",
                new_callable=AsyncMock,
            ) as started,
            patch(
                "app.celery_app.run_pipeline_phase_task"
            ) as task,
            patch("app.celery_app.settings") as settings,
        ):
            settings.celery_enabled = False
            task.delay.return_value = mock_result
            await _enqueue_next_generate_phase("rfp-1", "phase-2")
            check_cancel.assert_awaited()
            started.assert_awaited_with("rfp-1", "phase-3")
            task.delay.assert_called_once_with(
                "rfp-1", "phase-3", {"chain_next": True}
            )

    async def test_chains_budget_to_self_edit(self) -> None:
        from app.celery_app import _enqueue_next_generate_phase

        mock_result = MagicMock()
        mock_result.id = "task-self-edit"

        with (
            patch(
                "app.services.proposal_generation_cancel.check_generation_cancelled",
                new_callable=AsyncMock,
            ),
            patch(
                "app.services.proposal_pipeline_checkpoint.record_phase_started",
                new_callable=AsyncMock,
            ) as started,
            patch("app.celery_app.run_pipeline_phase_task") as task,
            patch("app.celery_app.settings") as settings,
        ):
            settings.celery_enabled = False
            task.delay.return_value = mock_result
            await _enqueue_next_generate_phase("rfp-1", "phase-3-5-budget")
            started.assert_awaited_with("rfp-1", "phase-3-6-self-edit")
            task.delay.assert_called_once_with(
                "rfp-1", "phase-3-6-self-edit", {"chain_next": True}
            )

    async def test_skips_chain_when_user_stopped(self) -> None:
        from app.celery_app import _enqueue_next_generate_phase

        with (
            patch(
                "app.services.proposal_generation_cancel.check_generation_cancelled",
                new_callable=AsyncMock,
                side_effect=ProposalGenerationCancelled(),
            ),
            patch(
                "app.celery_app.run_pipeline_phase_task"
            ) as task,
        ):
            await _enqueue_next_generate_phase("rfp-1", "phase-2")
            task.delay.assert_not_called()

    async def test_does_not_chain_standalone_jobs(self) -> None:
        from app.celery_app import _enqueue_next_generate_phase

        with patch("app.celery_app.run_pipeline_phase_task") as task:
            await _enqueue_next_generate_phase("rfp-1", "fulfill-scan")
            await _enqueue_next_generate_phase("rfp-1", "align-rfp-outline")
            task.delay.assert_not_called()


if __name__ == "__main__":
    unittest.main()
