"""Contract tests for content API endpoints.

Verifies:
- GET /api/content returns paginated response with correct shape
- GET /api/content with source_id filter returns filtered results
- GET /api/content with status filter returns filtered results
"""

import pytest
from httpx import AsyncClient

from tests.factories import create_content, create_source, create_thinker

pytestmark = pytest.mark.anyio


class TestContentEndpointContract:
    """Contract tests for /api/content endpoints."""

    async def test_list_content_returns_paginated_response(self, client: AsyncClient, session):
        """GET /api/content returns 200 with paginated shape."""
        thinker = await create_thinker(session)
        source = await create_source(session, thinker_id=thinker.id)
        await create_content(session, source_id=source.id, source_owner_id=thinker.id)
        await session.commit()

        resp = await client.get("/api/content")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert "page" in body
        assert "size" in body
        assert "pages" in body
        assert body["total"] >= 1

    async def test_list_content_item_shape(self, client: AsyncClient, session):
        """Each content item has required fields."""
        thinker = await create_thinker(session)
        source = await create_source(session, thinker_id=thinker.id)
        await create_content(session, source_id=source.id, source_owner_id=thinker.id)
        await session.commit()

        resp = await client.get("/api/content")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        required_keys = {
            "id",
            "source_id",
            "source_owner_id",
            "title",
            "status",
            "discovered_at",
        }
        assert required_keys.issubset(set(item.keys()))

    async def test_filter_by_source_id(self, client: AsyncClient, session):
        """GET /api/content?source_id={uuid} returns content for that source."""
        thinker = await create_thinker(session)
        source1 = await create_source(session, thinker_id=thinker.id)
        source2 = await create_source(session, thinker_id=thinker.id)
        await create_content(session, source_id=source1.id, source_owner_id=thinker.id)
        await create_content(session, source_id=source2.id, source_owner_id=thinker.id)
        await session.commit()

        resp = await client.get("/api/content", params={"source_id": str(source1.id)})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all(item["source_id"] == str(source1.id) for item in items)

    async def test_filter_by_status(self, client: AsyncClient, session):
        """GET /api/content?status=pending returns only pending content."""
        thinker = await create_thinker(session)
        source = await create_source(session, thinker_id=thinker.id)
        await create_content(session, source_id=source.id, source_owner_id=thinker.id, status="pending")
        await create_content(session, source_id=source.id, source_owner_id=thinker.id, status="done")
        await session.commit()

        resp = await client.get("/api/content", params={"status": "pending"})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all(item["status"] == "pending" for item in items)

    async def test_filter_by_thinker_id_uses_junction(self, client: AsyncClient, session):
        """GET /api/content?thinker_id={uuid} must filter via content_thinkers
        junction, not the deprecated Content.source_owner_id FK (which is
        None on all new content since Phase 13 / PR #17).

        Source: ADMIN-REVIEW HI-02 / DATA-REVIEW C1.
        """
        from tests.factories import create_content_thinker

        # Create a content row with NO source_owner_id set (the modern path).
        thinker = await create_thinker(session)
        source = await create_source(session)  # no thinker_id on source
        content = await create_content(session, source_id=source.id)
        # Link via junction only.
        await create_content_thinker(
            session,
            content_id=content.id,
            thinker_id=thinker.id,
            role="primary",
            confidence=9,
        )
        # A second content row belonging to a different thinker to make sure
        # we are actually filtering.
        other_thinker = await create_thinker(session)
        other_content = await create_content(session, source_id=source.id)
        await create_content_thinker(
            session,
            content_id=other_content.id,
            thinker_id=other_thinker.id,
            role="primary",
            confidence=9,
        )
        await session.commit()

        resp = await client.get("/api/content", params={"thinker_id": str(thinker.id)})
        assert resp.status_code == 200
        body = resp.json()
        ids = {item["id"] for item in body["items"]}
        assert str(content.id) in ids, "content linked via content_thinkers junction was not returned"
        assert str(other_content.id) not in ids, "filter returned content for a different thinker"
