"""Integration tests for kill switch against real PostgreSQL.

Tests the workers_active flag behavior from system_config.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_system_config


class TestIsWorkersActive:
    """Test is_workers_active against real DB."""

    async def test_returns_true_when_active(self, session: AsyncSession):
        """When workers_active = true, should return True."""
        from thinktank.queue.kill_switch import is_workers_active

        await create_system_config(
            session,
            key="workers_active",
            value=True,
        )

        result = await is_workers_active(session)
        assert result is True

    async def test_returns_false_when_inactive(self, session: AsyncSession):
        """When workers_active = false, should return False."""
        from thinktank.queue.kill_switch import is_workers_active

        await create_system_config(
            session,
            key="workers_active",
            value=False,
        )

        result = await is_workers_active(session)
        assert result is False

    async def test_returns_true_when_no_config(self, session: AsyncSession):
        """When no workers_active key exists, should return True (fail-open)."""
        from thinktank.queue.kill_switch import is_workers_active

        # No system_config seeded
        result = await is_workers_active(session)
        assert result is True

    async def test_handles_jsonb_dict_value_false(self, session: AsyncSession):
        """When JSONB value is {"value": false}, should return False."""
        from thinktank.queue.kill_switch import is_workers_active

        await create_system_config(
            session,
            key="workers_active",
            value={"value": False},
        )

        result = await is_workers_active(session)
        assert result is False

    async def test_handles_jsonb_dict_value_true(self, session: AsyncSession):
        """When JSONB value is {"value": true}, should return True."""
        from thinktank.queue.kill_switch import is_workers_active

        await create_system_config(
            session,
            key="workers_active",
            value={"value": True},
        )

        result = await is_workers_active(session)
        assert result is True

    async def test_handles_string_false(self, session: AsyncSession):
        """HANDLERS-REVIEW LO-01: JSONB string "false" must NOT be truthy.

        Operators editing via the admin UI can leave the value as a JSON
        string literal instead of a bool; previously bool("false") was
        True, silently leaving workers active.
        """
        from thinktank.queue.kill_switch import is_workers_active

        await create_system_config(
            session,
            key="workers_active",
            value="false",
        )

        result = await is_workers_active(session)
        assert result is False

    async def test_handles_string_true(self, session: AsyncSession):
        """JSONB string "true" evaluates truthy."""
        from thinktank.queue.kill_switch import is_workers_active

        await create_system_config(
            session,
            key="workers_active",
            value="true",
        )

        result = await is_workers_active(session)
        assert result is True

    async def test_handles_wrapped_string_false(self, session: AsyncSession):
        """{"value": "false"} must coerce to False (same trap as above)."""
        from thinktank.queue.kill_switch import is_workers_active

        await create_system_config(
            session,
            key="workers_active",
            value={"value": "false"},
        )

        result = await is_workers_active(session)
        assert result is False
