"""Integration tests for the health endpoint."""

import pytest
from httpx import AsyncClient


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


@pytest.mark.asyncio
async def test_health_includes_correlation_id_header(client: AsyncClient) -> None:
    """GET /health response includes X-Correlation-ID header from middleware."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert "x-correlation-id" in response.headers
    # Correlation ID should be a valid UUID-like string
    correlation_id = response.headers["x-correlation-id"]
    assert len(correlation_id) == 36  # UUID format: 8-4-4-4-12
    assert correlation_id.count("-") == 4


@pytest.mark.asyncio
async def test_correlation_ids_are_unique_per_request(client: AsyncClient) -> None:
    """Each request gets a unique correlation ID -- no leaking between requests."""
    response1 = await client.get("/health")
    response2 = await client.get("/health")
    id1 = response1.headers["x-correlation-id"]
    id2 = response2.headers["x-correlation-id"]
    assert id1 != id2, "Correlation IDs should be unique per request"
