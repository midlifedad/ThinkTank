"""Integration tests for admin LLM panel and category management."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    create_category,
    create_llm_review,
    create_source,
    create_system_config,
    create_thinker,
)

pytestmark = pytest.mark.anyio

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test",
)


def _now():
    return datetime.now(UTC)


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


class TestLLMPanelPage:
    """Test the LLM panel full page."""

    async def test_returns_200(self, admin_client):
        response = await admin_client.get("/admin/llm/")
        assert response.status_code == 200
        assert "LLM" in response.text

    async def test_contains_htmx_partials(self, admin_client):
        response = await admin_client.get("/admin/llm/")
        assert "hx-get" in response.text
        assert "every 10s" in response.text


class TestPendingPartial:
    """Test the pending reviews partial."""

    async def test_returns_200(self, admin_client):
        response = await admin_client.get("/admin/llm/partials/pending")
        assert response.status_code == 200

    async def test_shows_pending_review(self, admin_client, session: AsyncSession):
        await create_llm_review(
            session,
            review_type="thinker_approval",
            decision=None,
        )
        await session.commit()

        response = await admin_client.get("/admin/llm/partials/pending")
        assert response.status_code == 200
        assert "thinker_approval" in response.text

    async def test_completed_review_not_in_pending(self, admin_client, session: AsyncSession):
        await create_llm_review(
            session,
            review_type="thinker_approval",
            decision="approve",
            decision_reasoning="Looks good",
        )
        await session.commit()

        response = await admin_client.get("/admin/llm/partials/pending")
        assert response.status_code == 200
        assert "No pending reviews" in response.text

    async def test_timeout_highlight(self, admin_client, session: AsyncSession):
        # Set timeout to 2 hours
        await create_system_config(session, key="llm_timeout_hours", value=2, set_by="test")
        # Create a review from 3 hours ago (should be timed out)
        await create_llm_review(
            session,
            review_type="source_approval",
            decision=None,
            created_at=_now() - timedelta(hours=3),
        )
        await session.commit()

        response = await admin_client.get("/admin/llm/partials/pending")
        assert response.status_code == 200
        assert "timeout-highlight" in response.text


class TestRecentPartial:
    """Test the recent decisions partial."""

    async def test_returns_200(self, admin_client):
        response = await admin_client.get("/admin/llm/partials/recent")
        assert response.status_code == 200

    async def test_shows_completed_review(self, admin_client, session: AsyncSession):
        await create_llm_review(
            session,
            review_type="thinker_approval",
            decision="approve",
            decision_reasoning="Expert in AI safety",
            tokens_used=150,
        )
        await session.commit()

        response = await admin_client.get("/admin/llm/partials/recent")
        assert response.status_code == 200
        assert "approve" in response.text


class TestStatusPartial:
    """Test the LLM status partial."""

    async def test_returns_200(self, admin_client):
        response = await admin_client.get("/admin/llm/partials/status")
        assert response.status_code == 200

    async def test_shows_pending_count(self, admin_client, session: AsyncSession):
        await create_llm_review(session, decision=None)
        await create_llm_review(session, decision=None)
        await session.commit()

        response = await admin_client.get("/admin/llm/partials/status")
        assert response.status_code == 200
        assert "2" in response.text


class TestOverride:
    """Test the human override endpoint."""

    async def test_override_updates_review(self, admin_client, session: AsyncSession):
        review = await create_llm_review(
            session,
            review_type="thinker_approval",
            decision=None,
            context_snapshot={},
        )
        await session.commit()

        response = await admin_client.post(
            f"/admin/llm/override/{review.id}",
            data={
                "override_decision": "approve",
                "override_reasoning": "Manual approval by admin",
                "admin_username": "testadmin",
            },
        )
        assert response.status_code == 200

        # Verify the review was updated
        await session.refresh(review)
        assert review.decision == "approve"
        assert review.overridden_by == "testadmin"
        assert review.override_reasoning == "Manual approval by admin"
        assert review.overridden_at is not None

    async def test_override_applies_to_thinker(self, admin_client, session: AsyncSession):
        thinker = await create_thinker(session, approval_status="pending_llm")
        review = await create_llm_review(
            session,
            review_type="thinker_approval",
            decision=None,
            context_snapshot={"thinker_id": str(thinker.id)},
        )
        await session.commit()

        response = await admin_client.post(
            f"/admin/llm/override/{review.id}",
            data={
                "override_decision": "approve",
                "override_reasoning": "Manually approved",
                "admin_username": "admin",
            },
        )
        assert response.status_code == 200

        # Verify thinker was updated. Form value "approve" maps to canonical
        # "approved" (see override route; raw form values were writing invalid
        # status pre-Phase-4).
        await session.refresh(thinker)
        assert thinker.approval_status == "approved"

    async def test_override_applies_to_source(self, admin_client, session: AsyncSession):
        thinker = await create_thinker(session)
        source = await create_source(session, thinker_id=thinker.id, approval_status="pending_llm")
        review = await create_llm_review(
            session,
            review_type="source_approval",
            decision=None,
            context_snapshot={"source_id": str(source.id)},
        )
        await session.commit()

        response = await admin_client.post(
            f"/admin/llm/override/{review.id}",
            data={
                "override_decision": "approve",
                "override_reasoning": "Source verified",
                "admin_username": "admin",
            },
        )
        assert response.status_code == 200

        await session.refresh(source)
        assert source.approval_status == "approved"

    async def test_override_nonexistent_review(self, admin_client):
        fake_id = uuid.uuid4()
        response = await admin_client.post(
            f"/admin/llm/override/{fake_id}",
            data={
                "override_decision": "approve",
                "override_reasoning": "Test",
                "admin_username": "admin",
            },
        )
        assert response.status_code == 404


class TestCategoriesPage:
    """Test the category management page."""

    async def test_returns_200(self, admin_client):
        response = await admin_client.get("/admin/categories/")
        assert response.status_code == 200
        assert "Category" in response.text

    async def test_tree_partial_returns_200(self, admin_client):
        response = await admin_client.get("/admin/categories/partials/tree")
        assert response.status_code == 200

    async def test_tree_shows_categories(self, admin_client, session: AsyncSession):
        await create_category(session, name="Knowledge", slug="knowledge")
        await session.commit()

        response = await admin_client.get("/admin/categories/partials/tree")
        assert response.status_code == 200
        assert "Knowledge" in response.text

    async def test_create_category(self, admin_client, session: AsyncSession):
        response = await admin_client.post(
            "/admin/categories/create",
            data={
                "name": "Technology",
                "slug": "technology",
                "description": "Tech topics",
                "parent_id": "",
            },
        )
        assert response.status_code == 200
        assert "Technology" in response.text

    async def test_delete_category_with_children_returns_400(self, admin_client, session: AsyncSession):
        parent = await create_category(session, name="Parent", slug="parent-cat")
        await create_category(session, name="Child", slug="child-cat", parent_id=parent.id)
        await session.commit()

        response = await admin_client.post(f"/admin/categories/delete/{parent.id}")
        assert response.status_code == 400
        assert "Cannot delete" in response.text
