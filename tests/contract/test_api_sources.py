"""Contract tests for source API endpoints.

Verifies:
- GET /api/sources returns paginated response with correct shape
- GET /api/sources with thinker_id filter returns filtered results
- GET /api/sources with approval_status filter returns filtered results
"""

import pytest
from httpx import AsyncClient

from tests.factories import create_source, create_thinker

pytestmark = pytest.mark.anyio


class TestSourceEndpointContract:
    """Contract tests for /api/sources endpoints."""

    async def test_list_sources_returns_paginated_response(self, client: AsyncClient, session):
        """GET /api/sources returns 200 with paginated shape."""
        thinker = await create_thinker(session)
        await create_source(session, thinker_id=thinker.id)
        await session.commit()

        resp = await client.get("/api/sources")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert "page" in body
        assert "size" in body
        assert "pages" in body
        assert body["total"] >= 1

    async def test_list_sources_item_shape(self, client: AsyncClient, session):
        """Each source item has required fields."""
        thinker = await create_thinker(session)
        await create_source(session, thinker_id=thinker.id)
        await session.commit()

        resp = await client.get("/api/sources")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        required_keys = {
            "id",
            "thinker_id",
            "source_type",
            "name",
            "url",
            "approval_status",
            "active",
            "error_count",
            "created_at",
        }
        assert required_keys.issubset(set(item.keys()))

    async def test_filter_by_thinker_id(self, client: AsyncClient, session):
        """GET /api/sources?thinker_id={uuid} returns sources for that thinker."""
        thinker1 = await create_thinker(session)
        thinker2 = await create_thinker(session)
        await create_source(session, thinker_id=thinker1.id)
        await create_source(session, thinker_id=thinker2.id)
        await session.commit()

        resp = await client.get("/api/sources", params={"thinker_id": str(thinker1.id)})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all(item["thinker_id"] == str(thinker1.id) for item in items)

    async def test_filter_by_approval_status(self, client: AsyncClient, session):
        """GET /api/sources?approval_status=approved returns only approved sources."""
        thinker = await create_thinker(session)
        await create_source(session, thinker_id=thinker.id, approval_status="approved")
        await create_source(session, thinker_id=thinker.id, approval_status="pending_llm")
        await session.commit()

        resp = await client.get("/api/sources", params={"approval_status": "approved"})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all(item["approval_status"] == "approved" for item in items)
