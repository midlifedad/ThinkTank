"""Integration tests for source management: list, filters, add, approve, reject, force-refresh."""

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_source, create_source_thinker, create_thinker

pytestmark = pytest.mark.anyio

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test"
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


class TestSourcePage:
    """Test the source management page."""

    async def test_source_page_loads(self, admin_client, session: AsyncSession):
        """GET /admin/sources/ returns 200 with Source Management title."""
        response = await admin_client.get("/admin/sources/")
        assert response.status_code == 200
        assert "Source Management" in response.text

    async def test_source_page_has_filter_dropdowns(self, admin_client):
        """GET /admin/sources/ returns 200 with filter labels."""
        response = await admin_client.get("/admin/sources/")
        assert response.status_code == 200
        assert "Approval Status" in response.text
        assert "Source Type" in response.text


class TestSourceList:
    """Test the source list partial endpoint."""

    async def test_list_partial_empty(self, admin_client):
        """GET /admin/sources/partials/list with no sources returns empty message."""
        response = await admin_client.get("/admin/sources/partials/list")
        assert response.status_code == 200
        assert "No sources found" in response.text

    async def test_list_partial_shows_sources(self, admin_client, session: AsyncSession):
        """GET list partial with seeded sources shows both source names."""
        await create_thinker(session, name="List Thinker", slug="list-thinker")
        await create_source(session, name="Alpha Feed", url="https://example.com/alpha-src.xml")
        await create_source(session, name="Beta Feed", url="https://example.com/beta-src.xml")
        await session.commit()

        response = await admin_client.get("/admin/sources/partials/list")
        assert response.status_code == 200
        assert "Alpha Feed" in response.text
        assert "Beta Feed" in response.text

    async def test_filter_by_status(self, admin_client, session: AsyncSession):
        """GET with ?status=approved returns only approved sources."""
        await create_thinker(session, name="Status Thinker", slug="status-thinker")
        await create_source(
            session, name="Approved Source", url="https://example.com/approved-src.xml", approval_status="approved"
        )
        await create_source(
            session, name="Pending Source", url="https://example.com/pending-src.xml", approval_status="pending_llm"
        )
        await session.commit()

        response = await admin_client.get("/admin/sources/partials/list?status=approved")
        assert response.status_code == 200
        assert "Approved Source" in response.text
        assert "Pending Source" not in response.text

    async def test_filter_by_thinker(self, admin_client, session: AsyncSession):
        """GET with ?thinker_id={id} returns only that thinker's sources."""
        thinker_a = await create_thinker(session, name="Thinker A Src", slug="thinker-a-src")
        thinker_b = await create_thinker(session, name="Thinker B Src", slug="thinker-b-src")
        source_a = await create_source(session, name="A Source", url="https://example.com/a-source.xml")
        await create_source_thinker(session, source_id=source_a.id, thinker_id=thinker_a.id, relationship_type="host")
        source_b = await create_source(session, name="B Source", url="https://example.com/b-source.xml")
        await create_source_thinker(session, source_id=source_b.id, thinker_id=thinker_b.id, relationship_type="host")
        await session.commit()

        response = await admin_client.get(f"/admin/sources/partials/list?thinker_id={thinker_a.id}")
        assert response.status_code == 200
        assert "A Source" in response.text
        assert "B Source" not in response.text


class TestSourceAdd:
    """Test adding a new source."""

    async def test_add_creates_source(self, admin_client, session: AsyncSession):
        """POST /admin/sources/add creates a Source with pending_llm status."""
        thinker = await create_thinker(session, name="Add Thinker", slug="add-thinker")
        await session.commit()

        response = await admin_client.post(
            "/admin/sources/add",
            data={
                "name": "New Test Feed",
                "url": "https://example.com/new-test-feed.xml",
                "thinker_id": str(thinker.id),
                "source_type": "podcast_rss",
            },
        )
        assert response.status_code == 200

        from thinktank.models.source import Source

        result = await session.execute(
            select(Source).where(Source.name == "New Test Feed").execution_options(populate_existing=True)
        )
        source = result.scalar_one_or_none()
        assert source is not None
        assert source.approval_status == "pending_llm"
        # thinker_id is no longer set on source; junction row created instead
        from thinktank.models.source import SourceThinker

        junc_result = await session.execute(select(SourceThinker).where(SourceThinker.source_id == source.id))
        junc = junc_result.scalar_one_or_none()
        assert junc is not None
        assert junc.thinker_id == thinker.id

    async def test_add_rejects_malformed_thinker_id(self, admin_client, session: AsyncSession):
        """POST add with malformed thinker_id returns 422 (HI-04)."""
        response = await admin_client.post(
            "/admin/sources/add",
            data={
                "name": "Bad Thinker Feed",
                "url": "https://example.com/bad-thinker.xml",
                "thinker_id": "not-a-uuid",
                "source_type": "podcast_rss",
            },
        )
        assert response.status_code == 422

    async def test_add_rejects_missing_thinker_id(self, admin_client, session: AsyncSession):
        """POST add with UUID that doesn't exist as a thinker returns 422 (HI-04)."""
        missing = uuid.uuid4()
        response = await admin_client.post(
            "/admin/sources/add",
            data={
                "name": "Missing Thinker Feed",
                "url": "https://example.com/missing-thinker.xml",
                "thinker_id": str(missing),
                "source_type": "podcast_rss",
            },
        )
        assert response.status_code == 422

    async def test_add_returns_success_message(self, admin_client, session: AsyncSession):
        """POST add returns 200 with success message containing source name."""
        thinker = await create_thinker(session, name="Msg Thinker", slug="msg-thinker")
        await session.commit()

        response = await admin_client.post(
            "/admin/sources/add",
            data={
                "name": "Success Feed",
                "url": "https://example.com/success-feed.xml",
                "thinker_id": str(thinker.id),
            },
        )
        assert response.status_code == 200
        assert "Success Feed" in response.text


class TestSourceApprove:
    """Test approving a source."""

    async def test_approve_sets_status(self, admin_client, session: AsyncSession):
        """POST approve sets approval_status to approved."""
        await create_thinker(session, name="Approve Thinker", slug="approve-thinker")
        source = await create_source(
            session,
            name="Pending Approve",
            url="https://example.com/pending-approve.xml",
            approval_status="pending_llm",
        )
        await session.commit()

        response = await admin_client.post(f"/admin/sources/{source.id}/approve", data={"reason": "Looks good"})
        assert response.status_code == 200

        from thinktank.models.source import Source

        result = await session.execute(
            select(Source).where(Source.id == source.id).execution_options(populate_existing=True)
        )
        updated = result.scalar_one()
        assert updated.approval_status == "approved"

    async def test_approve_creates_audit_trail(self, admin_client, session: AsyncSession):
        """After approve, an LLMReview exists with correct fields."""
        await create_thinker(session, name="Audit Thinker", slug="audit-thinker")
        source = await create_source(
            session, name="Audit Source", url="https://example.com/audit-source.xml", approval_status="pending_llm"
        )
        await session.commit()

        await admin_client.post(f"/admin/sources/{source.id}/approve", data={"reason": "Verified feed"})

        from thinktank.models.review import LLMReview

        result = await session.execute(
            select(LLMReview).where(LLMReview.review_type == "source_approval", LLMReview.decision == "approve")
        )
        review = result.scalar_one_or_none()
        assert review is not None
        assert review.trigger == "admin_override"
        assert review.context_snapshot["source_id"] == str(source.id)

    async def test_approve_returns_success(self, admin_client, session: AsyncSession):
        """POST approve returns 200 with success message."""
        await create_thinker(session, name="Approve Msg Thinker", slug="approve-msg-thinker")
        source = await create_source(
            session, name="Approve Msg Source", url="https://example.com/approve-msg.xml", approval_status="pending_llm"
        )
        await session.commit()

        response = await admin_client.post(f"/admin/sources/{source.id}/approve", data={"reason": "Good"})
        assert response.status_code == 200
        assert "approved" in response.text.lower()


class TestSourceReject:
    """Test rejecting a source."""

    async def test_reject_sets_status(self, admin_client, session: AsyncSession):
        """POST reject sets approval_status to rejected."""
        await create_thinker(session, name="Reject Thinker", slug="reject-thinker")
        source = await create_source(
            session, name="Pending Reject", url="https://example.com/pending-reject.xml", approval_status="pending_llm"
        )
        await session.commit()

        response = await admin_client.post(f"/admin/sources/{source.id}/reject", data={"reason": "Low quality"})
        assert response.status_code == 200

        from thinktank.models.source import Source

        result = await session.execute(
            select(Source).where(Source.id == source.id).execution_options(populate_existing=True)
        )
        updated = result.scalar_one()
        assert updated.approval_status == "rejected"

    async def test_reject_creates_audit_trail(self, admin_client, session: AsyncSession):
        """After reject, an LLMReview exists with decision=reject."""
        await create_thinker(session, name="Reject Audit Thinker", slug="reject-audit-thinker")
        source = await create_source(
            session,
            name="Reject Audit Source",
            url="https://example.com/reject-audit.xml",
            approval_status="pending_llm",
        )
        await session.commit()

        await admin_client.post(f"/admin/sources/{source.id}/reject", data={"reason": "Not relevant"})

        from thinktank.models.review import LLMReview

        result = await session.execute(
            select(LLMReview).where(LLMReview.review_type == "source_approval", LLMReview.decision == "reject")
        )
        review = result.scalar_one_or_none()
        assert review is not None
        assert review.trigger == "admin_override"
        assert review.context_snapshot["source_id"] == str(source.id)


class TestSourceForceRefresh:
    """Test force-refreshing a source."""

    async def test_force_refresh_creates_job(self, admin_client, session: AsyncSession):
        """POST force-refresh creates a fetch_podcast_feed job."""
        await create_thinker(session, name="Refresh Thinker", slug="refresh-thinker")
        source = await create_source(
            session, name="Refresh Source", url="https://example.com/refresh-source.xml", approval_status="approved"
        )
        await session.commit()

        response = await admin_client.post(f"/admin/sources/{source.id}/force-refresh")
        assert response.status_code == 200

        from thinktank.models.job import Job

        result = await session.execute(select(Job).where(Job.job_type == "fetch_podcast_feed"))
        job = result.scalar_one_or_none()
        assert job is not None
        assert job.payload["source_id"] == str(source.id)
        assert job.status == "pending"

    async def test_force_refresh_returns_success(self, admin_client, session: AsyncSession):
        """POST force-refresh returns 200 with refresh queued message."""
        await create_thinker(session, name="Refresh Msg Thinker", slug="refresh-msg-thinker")
        source = await create_source(
            session, name="Refresh Msg Source", url="https://example.com/refresh-msg.xml", approval_status="approved"
        )
        await session.commit()

        response = await admin_client.post(f"/admin/sources/{source.id}/force-refresh")
        assert response.status_code == 200
        assert "refresh queued" in response.text.lower()
