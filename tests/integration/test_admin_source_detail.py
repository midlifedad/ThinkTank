"""Integration tests for source detail page, episodes partial, and errors partial."""

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    create_content,
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
    """HTTP client for admin integration tests."""
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

    from thinktank.config import get_settings

    get_settings.cache_clear()

    from thinktank.admin.main import app as admin_app

    transport = ASGITransport(app=admin_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    get_settings.cache_clear()


class TestSourceDetail:
    """Test the source detail page."""

    async def test_detail_page_loads(self, admin_client, session: AsyncSession):
        """GET /admin/sources/{id} returns 200 with source name and thinker name."""
        thinker = await create_thinker(session, name="Detail Thinker", slug="detail-thinker")
        source = await create_source(
            session,
            thinker_id=thinker.id,
            name="Detail Source Feed",
            url="https://example.com/detail-feed.xml",
        )
        await session.commit()

        response = await admin_client.get(f"/admin/sources/{source.id}")
        assert response.status_code == 200
        assert "Detail Source Feed" in response.text
        assert "Detail Thinker" in response.text

    async def test_detail_page_404(self, admin_client):
        """GET /admin/sources/{random_uuid} returns 404."""
        response = await admin_client.get(f"/admin/sources/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_detail_shows_health_info(self, admin_client, session: AsyncSession):
        """Detail page shows error_count and item_count values."""
        thinker = await create_thinker(session, name="Health Thinker", slug="health-thinker")
        source = await create_source(
            session,
            thinker_id=thinker.id,
            name="Health Source",
            url="https://example.com/health-feed.xml",
            error_count=3,
            item_count=10,
        )
        await session.commit()

        response = await admin_client.get(f"/admin/sources/{source.id}")
        assert response.status_code == 200
        assert "3" in response.text
        assert "10" in response.text


class TestSourceEpisodes:
    """Test the episodes partial endpoint."""

    async def test_episodes_partial_empty(self, admin_client, session: AsyncSession):
        """GET episodes partial with no content returns 200 with empty message."""
        thinker = await create_thinker(session, name="No Eps Thinker", slug="no-eps-thinker")
        source = await create_source(
            session,
            thinker_id=thinker.id,
            name="No Eps Source",
            url="https://example.com/no-eps-feed.xml",
        )
        await session.commit()

        response = await admin_client.get(
            f"/admin/sources/{source.id}/partials/episodes"
        )
        assert response.status_code == 200
        assert "No episodes found" in response.text

    async def test_episodes_partial_shows_content(
        self, admin_client, session: AsyncSession
    ):
        """GET episodes partial with seeded content shows both titles."""
        thinker = await create_thinker(session, name="Eps Thinker", slug="eps-thinker")
        source = await create_source(
            session,
            thinker_id=thinker.id,
            name="Eps Source",
            url="https://example.com/eps-feed.xml",
        )
        await create_content(
            session,
            source_id=source.id,
            source_owner_id=thinker.id,
            title="Episode Alpha",
        )
        await create_content(
            session,
            source_id=source.id,
            source_owner_id=thinker.id,
            title="Episode Beta",
        )
        await session.commit()

        response = await admin_client.get(
            f"/admin/sources/{source.id}/partials/episodes"
        )
        assert response.status_code == 200
        assert "Episode Alpha" in response.text
        assert "Episode Beta" in response.text


class TestSourceErrors:
    """Test the error history partial endpoint."""

    async def test_errors_partial_empty(self, admin_client, session: AsyncSession):
        """GET errors partial with no failed jobs returns 200 with empty message."""
        thinker = await create_thinker(session, name="No Errs Thinker", slug="no-errs-thinker")
        source = await create_source(
            session,
            thinker_id=thinker.id,
            name="No Errs Source",
            url="https://example.com/no-errs-feed.xml",
        )
        await session.commit()

        response = await admin_client.get(
            f"/admin/sources/{source.id}/partials/errors"
        )
        assert response.status_code == 200
        assert "No errors found" in response.text

    async def test_errors_partial_shows_failed_jobs(
        self, admin_client, session: AsyncSession
    ):
        """GET errors partial with a failed fetch job shows the error message."""
        thinker = await create_thinker(session, name="Err Thinker", slug="err-thinker")
        source = await create_source(
            session,
            thinker_id=thinker.id,
            name="Err Source",
            url="https://example.com/err-feed.xml",
        )
        await create_job(
            session,
            job_type="fetch_podcast_feed",
            status="failed",
            error="Connection timeout",
            error_category="network",
            payload={"source_id": str(source.id)},
        )
        await session.commit()

        response = await admin_client.get(
            f"/admin/sources/{source.id}/partials/errors"
        )
        assert response.status_code == 200
        assert "Connection timeout" in response.text
