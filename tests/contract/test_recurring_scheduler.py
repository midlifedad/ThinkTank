"""Contract tests for the recurring-task executor tick.

Source: ARCH-REVIEW 2026-05-28 (A1). Before this executor, the Phase 11
scheduler configs were write-only: nothing enqueued refresh_due_sources (or
anything else) on a cadence, so the pipeline only moved via "Run Now".

Contract:
    - Given scheduler_<key> configs in system_config (or none)
    - When run_due_scheduled_tasks ticks
    - Then due, enabled, job-typed tasks get exactly one job enqueued and
      their schedule advanced; disabled/not-due/in-flight tasks are skipped
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_job, create_system_config
from thinktank.models.config_table import SystemConfig
from thinktank.models.job import Job
from thinktank.queue.scheduled_tasks import SCHEDULED_TASKS
from thinktank.worker.recurring import run_due_scheduled_tasks

pytestmark = pytest.mark.anyio

JOB_TYPED_TASKS = [t for t in SCHEDULED_TASKS if t["job_type"] is not None]


async def _jobs_of_type(session: AsyncSession, job_type: str) -> list[Job]:
    result = await session.execute(select(Job).where(Job.job_type == job_type))
    return list(result.scalars().all())


class TestFreshSystemBootstraps:
    """With no scheduler configs at all, every job-typed task fires once."""

    async def test_all_job_typed_tasks_enqueued_on_first_tick(self, session: AsyncSession):
        enqueued = await run_due_scheduled_tasks(session)

        assert enqueued == len(JOB_TYPED_TASKS)
        for task_def in JOB_TYPED_TASKS:
            jobs = await _jobs_of_type(session, task_def["job_type"])
            assert len(jobs) == 1, f"expected one {task_def['job_type']} job"
            assert jobs[0].payload == {"triggered_by": "recurring_scheduler"}

    async def test_first_tick_writes_schedule_state(self, session: AsyncSession):
        await run_due_scheduled_tasks(session)

        for task_def in JOB_TYPED_TASKS:
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key == f"scheduler_{task_def['key']}")
            )
            row = result.scalar_one()
            assert row.value["last_run_at"] is not None
            assert row.value["next_run_at"] is not None
            assert row.value["frequency_hours"] == task_def["default_hours"]

    async def test_second_tick_is_noop_until_due(self, session: AsyncSession):
        """After the first tick advanced next_run_at, an immediate re-tick
        enqueues nothing (jobs from tick 1 are still pending anyway)."""
        await run_due_scheduled_tasks(session)
        enqueued = await run_due_scheduled_tasks(session)
        assert enqueued == 0


class TestScheduleSemantics:
    """Executor honors the exact config shape the admin UI writes."""

    async def test_disabled_task_is_skipped(self, session: AsyncSession):
        for task_def in JOB_TYPED_TASKS:
            await create_system_config(
                session,
                key=f"scheduler_{task_def['key']}",
                value={"frequency_hours": 1, "enabled": False, "last_run_at": None, "next_run_at": None},
            )

        enqueued = await run_due_scheduled_tasks(session)

        assert enqueued == 0
        for task_def in JOB_TYPED_TASKS:
            assert await _jobs_of_type(session, task_def["job_type"]) == []

    async def test_not_yet_due_task_is_skipped(self, session: AsyncSession):
        future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        for task_def in JOB_TYPED_TASKS:
            await create_system_config(
                session,
                key=f"scheduler_{task_def['key']}",
                value={"frequency_hours": 4, "enabled": True, "next_run_at": future},
            )

        enqueued = await run_due_scheduled_tasks(session)
        assert enqueued == 0

    async def test_due_task_advances_by_configured_frequency(self, session: AsyncSession):
        task_def = JOB_TYPED_TASKS[0]
        past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        await create_system_config(
            session,
            key=f"scheduler_{task_def['key']}",
            value={"frequency_hours": 7, "enabled": True, "next_run_at": past},
        )
        # Disable the others to isolate this task.
        for other in JOB_TYPED_TASKS[1:]:
            await create_system_config(
                session,
                key=f"scheduler_{other['key']}",
                value={"frequency_hours": 1, "enabled": False},
            )

        enqueued = await run_due_scheduled_tasks(session)
        assert enqueued == 1

        result = await session.execute(select(SystemConfig).where(SystemConfig.key == f"scheduler_{task_def['key']}"))
        row = result.scalar_one()
        last_run = datetime.fromisoformat(row.value["last_run_at"])
        next_run = datetime.fromisoformat(row.value["next_run_at"])
        assert next_run - last_run == timedelta(hours=7)
        # UI-owned fields must survive the executor's update.
        assert row.value["enabled"] is True
        assert row.value["frequency_hours"] == 7


class TestInflightDedup:
    """A due task with a job already pending is skipped without advancing."""

    async def test_pending_job_blocks_reenqueue(self, session: AsyncSession):
        task_def = JOB_TYPED_TASKS[0]
        await create_job(session, job_type=task_def["job_type"], status="pending")
        for other in JOB_TYPED_TASKS[1:]:
            await create_system_config(
                session,
                key=f"scheduler_{other['key']}",
                value={"frequency_hours": 1, "enabled": False},
            )

        enqueued = await run_due_scheduled_tasks(session)

        assert enqueued == 0
        jobs = await _jobs_of_type(session, task_def["job_type"])
        assert len(jobs) == 1  # only the pre-existing one
        # Schedule NOT advanced -- next tick retries.
        result = await session.execute(select(SystemConfig).where(SystemConfig.key == f"scheduler_{task_def['key']}"))
        assert result.scalar_one_or_none() is None
