"""Contract tests for thinker API endpoints.

Verifies:
- GET /api/thinkers returns paginated response with correct shape
- GET /api/thinkers with tier/status filters returns filtered results
- GET /api/thinkers/{id} returns thinker or 404
- POST /api/thinkers creates thinker with 201
- POST /api/thinkers with empty body returns 422
- PATCH /api/thinkers/{id} updates thinker fields
"""

import uuid

import pytest
from httpx import AsyncClient

from tests.factories import create_thinker

pytestmark = pytest.mark.anyio


class TestThinkerEndpointContract:
    """Contract tests for /api/thinkers endpoints."""

    async def test_list_thinkers_returns_paginated_response(self, client: AsyncClient, session):
        """GET /api/thinkers returns 200 with paginated shape."""
        await create_thinker(session, name="Alice")
        await session.commit()

        resp = await client.get("/api/thinkers")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert "page" in body
        assert "size" in body
        assert "pages" in body
        assert body["total"] >= 1
        assert isinstance(body["items"], list)
        assert len(body["items"]) >= 1

    async def test_list_thinkers_item_shape(self, client: AsyncClient, session):
        """Each thinker item has required fields."""
        await create_thinker(session, name="Bob")
        await session.commit()

        resp = await client.get("/api/thinkers")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        required_keys = {"id", "name", "slug", "tier", "bio", "approval_status", "active", "added_at"}
        assert required_keys.issubset(set(item.keys()))

    async def test_filter_by_tier(self, client: AsyncClient, session):
        """GET /api/thinkers?tier=1 returns only tier-1 thinkers."""
        await create_thinker(session, tier=1)
        await create_thinker(session, tier=2)
        await session.commit()

        resp = await client.get("/api/thinkers", params={"tier": 1})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all(item["tier"] == 1 for item in items)

    async def test_filter_by_status(self, client: AsyncClient, session):
        """GET /api/thinkers?status=approved returns only approved thinkers."""
        await create_thinker(session, approval_status="approved")
        await create_thinker(session, approval_status="pending_llm")
        await session.commit()

        resp = await client.get("/api/thinkers", params={"status": "approved"})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all(item["approval_status"] == "approved" for item in items)

    async def test_get_thinker_by_id(self, client: AsyncClient, session):
        """GET /api/thinkers/{id} returns the thinker."""
        thinker = await create_thinker(session, name="Charlie")
        await session.commit()

        resp = await client.get(f"/api/thinkers/{thinker.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Charlie"
        assert body["id"] == str(thinker.id)

    async def test_get_thinker_not_found(self, client: AsyncClient):
        """GET /api/thinkers/{id} returns 404 for unknown id."""
        fake_id = uuid.uuid4()
        resp = await client.get(f"/api/thinkers/{fake_id}")
        assert resp.status_code == 404
        assert "detail" in resp.json()

    async def test_create_thinker(self, client: AsyncClient):
        """POST /api/thinkers with valid body returns 201."""
        payload = {
            "name": "New Thinker",
            "slug": f"new-thinker-{uuid.uuid4().hex[:8]}",
            "tier": 2,
            "bio": "A new thinker for testing.",
        }
        resp = await client.post("/api/thinkers", json=payload)
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "New Thinker"
        assert "id" in body

    async def test_create_thinker_validation_error(self, client: AsyncClient):
        """POST /api/thinkers with empty body returns 422."""
        resp = await client.post("/api/thinkers", json={})
        assert resp.status_code == 422
        assert "detail" in resp.json()

    async def test_update_thinker(self, client: AsyncClient, session):
        """PATCH /api/thinkers/{id} updates the specified fields."""
        thinker = await create_thinker(session, bio="Old bio")
        await session.commit()

        resp = await client.patch(f"/api/thinkers/{thinker.id}", json={"bio": "New bio"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["bio"] == "New bio"

    async def test_update_thinker_not_found(self, client: AsyncClient):
        """PATCH /api/thinkers/{id} returns 404 for unknown id."""
        fake_id = uuid.uuid4()
        resp = await client.patch(f"/api/thinkers/{fake_id}", json={"bio": "x"})
        assert resp.status_code == 404
        assert "detail" in resp.json()
