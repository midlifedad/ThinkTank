"""Integration tests for the health endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_health_returns_200_when_db_connected(client: AsyncClient) -> None:
    """GET /health returns 200 with status=healthy when database is connected."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_response_includes_service_name(client: AsyncClient) -> None:
    """GET /health response includes the service name."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "thinktank-api"
