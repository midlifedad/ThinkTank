"""Contract tests for OpenAPI documentation endpoints.

Verifies:
- GET /docs returns 200 (Swagger UI)
- GET /redoc returns 200 (ReDoc)
- GET /openapi.json returns valid JSON with paths for all 5 routers
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


class TestOpenAPIContract:
    """Contract tests for OpenAPI documentation."""

    async def test_swagger_ui_accessible(self, client: AsyncClient):
        """GET /docs returns 200."""
        resp = await client.get("/docs")
        assert resp.status_code == 200

    async def test_redoc_accessible(self, client: AsyncClient):
        """GET /redoc returns 200."""
        resp = await client.get("/redoc")
        assert resp.status_code == 200

    async def test_openapi_json_valid(self, client: AsyncClient):
        """GET /openapi.json returns valid JSON."""
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "paths" in schema
        assert "info" in schema

    async def test_openapi_contains_thinker_paths(self, client: AsyncClient):
        """OpenAPI schema includes thinker endpoints."""
        resp = await client.get("/openapi.json")
        paths = resp.json()["paths"]
        assert "/api/thinkers" in paths or "/api/thinkers/" in paths

    async def test_openapi_contains_sources_path(self, client: AsyncClient):
        """OpenAPI schema includes source endpoints."""
        resp = await client.get("/openapi.json")
        paths = resp.json()["paths"]
        assert "/api/sources" in paths or "/api/sources/" in paths

    async def test_openapi_contains_content_path(self, client: AsyncClient):
        """OpenAPI schema includes content endpoints."""
        resp = await client.get("/openapi.json")
        paths = resp.json()["paths"]
        assert "/api/content" in paths or "/api/content/" in paths

    async def test_openapi_contains_jobs_path(self, client: AsyncClient):
        """OpenAPI schema includes jobs endpoints."""
        resp = await client.get("/openapi.json")
        paths = resp.json()["paths"]
        assert "/api/jobs/status" in paths

    async def test_openapi_contains_config_path(self, client: AsyncClient):
        """OpenAPI schema includes config endpoints."""
        resp = await client.get("/openapi.json")
        paths = resp.json()["paths"]
        assert "/api/config" in paths or "/api/config/" in paths
