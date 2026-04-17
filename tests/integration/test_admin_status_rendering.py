"""Integration tests for admin partial template status rendering.

ADMIN-REVIEW CR-03, CR-04, HI-01: workers write
  done / cataloged / pending / error / skipped
but three admin partials checked for
  completed / transcribed / complete / failed
-> healthy and error states never rendered as such.

Source of truth: src/thinktank/models/constants.py (HEALTHY_CONTENT_STATUSES,
WARNING_CONTENT_STATUSES, ERROR_CONTENT_STATUSES).
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    create_content,
    create_content_thinker,
    create_job,
    create_source,
    create_thinker,
)

pytestmark = pytest.mark.anyio

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test",
)


@pytest.fixture
async def admin_client() -> AsyncClient:
    """HTTP client for admin template rendering tests."""
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

    from thinktank.config import get_settings

    get_settings.cache_clear()

    from thinktank.admin.main import app as admin_app

    transport = ASGITransport(app=admin_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    get_settings.cache_clear()


class TestQueueDepthDoneColumn:
    """queue_depth.html must count `done` (what workers actually write)."""

    async def test_done_jobs_counted_in_done_column(self, admin_client: AsyncClient, session: AsyncSession):
        """A job with status='done' (worker's terminal value) must show up
        in the Done column, not disappear into the void because the template
        only looked for 'completed'.
        """
        await create_job(session, job_type="fetch_podcast_feed", status="done")
        await session.commit()

        response = await admin_client.get("/admin/partials/queue-depth")
        assert response.status_code == 200
        body = response.text

        # The Done column header must be present.
        assert "Done" in body or "done" in body.lower()
        # fetch_podcast_feed row must render.
        assert "fetch_podcast_feed" in body
        # And the count of 1 must appear in that row (not just zeros).
        # The row has 4 status columns, three of which should be 0.
        # Verifying the cell value 1 exists somewhere in the row is a
        # reasonable proxy.
        assert ">1<" in body, "Done job count did not render; template still only counts 'completed'"


class TestThinkerContentStatusRendering:
    """thinker_content.html must render healthy/warning/error CSS classes
    for the statuses workers actually write.
    """

    async def _setup_content(self, session: AsyncSession, status: str) -> tuple[str, str]:
        thinker = await create_thinker(session)
        source = await create_source(session)
        content = await create_content(
            session,
            source_id=source.id,
            status=status,
            title=f"Episode status={status}",
        )
        await create_content_thinker(session, content_id=content.id, thinker_id=thinker.id)
        await session.commit()
        return str(thinker.id), status

    async def test_done_renders_healthy(self, admin_client: AsyncClient, session: AsyncSession):
        thinker_id, _ = await self._setup_content(session, "done")
        resp = await admin_client.get(f"/admin/thinkers/{thinker_id}/partials/content")
        assert resp.status_code == 200
        assert 'class="healthy"' in resp.text

    async def test_cataloged_renders_healthy(self, admin_client: AsyncClient, session: AsyncSession):
        thinker_id, _ = await self._setup_content(session, "cataloged")
        resp = await admin_client.get(f"/admin/thinkers/{thinker_id}/partials/content")
        assert resp.status_code == 200
        assert 'class="healthy"' in resp.text

    async def test_pending_renders_warning(self, admin_client: AsyncClient, session: AsyncSession):
        thinker_id, _ = await self._setup_content(session, "pending")
        resp = await admin_client.get(f"/admin/thinkers/{thinker_id}/partials/content")
        assert resp.status_code == 200
        assert 'class="warning"' in resp.text

    async def test_error_renders_error(self, admin_client: AsyncClient, session: AsyncSession):
        thinker_id, _ = await self._setup_content(session, "error")
        resp = await admin_client.get(f"/admin/thinkers/{thinker_id}/partials/content")
        assert resp.status_code == 200
        assert 'class="error"' in resp.text

    async def test_skipped_renders_error(self, admin_client: AsyncClient, session: AsyncSession):
        thinker_id, _ = await self._setup_content(session, "skipped")
        resp = await admin_client.get(f"/admin/thinkers/{thinker_id}/partials/content")
        assert resp.status_code == 200
        assert 'class="error"' in resp.text


class TestSourceEpisodesStatusRendering:
    """source_episodes.html must use the same status buckets."""

    async def _setup_episode(self, session: AsyncSession, status: str) -> str:
        source = await create_source(session)
        await create_content(
            session,
            source_id=source.id,
            status=status,
            title=f"Episode status={status}",
        )
        await session.commit()
        return str(source.id)

    async def test_done_renders_healthy(self, admin_client: AsyncClient, session: AsyncSession):
        source_id = await self._setup_episode(session, "done")
        resp = await admin_client.get(f"/admin/sources/{source_id}/partials/episodes")
        assert resp.status_code == 200
        assert 'class="healthy"' in resp.text

    async def test_cataloged_renders_healthy(self, admin_client: AsyncClient, session: AsyncSession):
        source_id = await self._setup_episode(session, "cataloged")
        resp = await admin_client.get(f"/admin/sources/{source_id}/partials/episodes")
        assert resp.status_code == 200
        assert 'class="healthy"' in resp.text

    async def test_pending_renders_warning(self, admin_client: AsyncClient, session: AsyncSession):
        source_id = await self._setup_episode(session, "pending")
        resp = await admin_client.get(f"/admin/sources/{source_id}/partials/episodes")
        assert resp.status_code == 200
        assert 'class="warning"' in resp.text

    async def test_error_renders_error(self, admin_client: AsyncClient, session: AsyncSession):
        source_id = await self._setup_episode(session, "error")
        resp = await admin_client.get(f"/admin/sources/{source_id}/partials/episodes")
        assert resp.status_code == 200
        assert 'class="error"' in resp.text

    async def test_skipped_renders_error(self, admin_client: AsyncClient, session: AsyncSession):
        source_id = await self._setup_episode(session, "skipped")
        resp = await admin_client.get(f"/admin/sources/{source_id}/partials/episodes")
        assert resp.status_code == 200
        assert 'class="error"' in resp.text
