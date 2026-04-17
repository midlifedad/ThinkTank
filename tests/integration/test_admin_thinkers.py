"""Integration tests for thinker management: list, search, add, edit, toggle, and filter."""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_category, create_source, create_thinker, create_thinker_category


async def _verify_thinker(session: AsyncSession, thinker_id):
    """Load a thinker from DB bypassing identity map cache."""
    from thinktank.models.thinker import Thinker

    # Bypass identity map by using a fresh connection
    result = await session.execute(
        select(Thinker).where(Thinker.id == thinker_id).execution_options(populate_existing=True)
    )
    return result.scalar_one()


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


class TestThinkerPage:
    """Test the thinker management page loads correctly."""

    async def test_thinker_page_loads(self, admin_client):
        """GET /admin/thinkers/ returns 200 with page title."""
        response = await admin_client.get("/admin/thinkers/")
        assert response.status_code == 200
        assert "Thinker Management" in response.text

    async def test_thinker_page_has_search(self, admin_client):
        """Response contains search input element."""
        response = await admin_client.get("/admin/thinkers/")
        assert response.status_code == 200
        assert 'name="q"' in response.text
        assert "Search by name" in response.text


class TestThinkerList:
    """Test the thinker list partial endpoint."""

    async def test_list_empty(self, admin_client):
        """GET list partial returns 200 with no-thinkers message when DB is empty."""
        response = await admin_client.get("/admin/thinkers/partials/list")
        assert response.status_code == 200
        assert "No thinkers found" in response.text

    async def test_list_shows_thinkers(self, admin_client, session: AsyncSession):
        """Seeded thinkers appear in the list partial."""
        await create_thinker(session, name="Alice Expert", slug="alice-expert")
        await create_thinker(session, name="Bob Scholar", slug="bob-scholar")
        await session.commit()

        response = await admin_client.get("/admin/thinkers/partials/list")
        assert response.status_code == 200
        assert "Alice Expert" in response.text
        assert "Bob Scholar" in response.text

    async def test_list_shows_source_count(self, admin_client, session: AsyncSession):
        """Thinker with sources shows correct source count."""
        thinker = await create_thinker(session, name="Source Thinker", slug="source-thinker")
        await create_source(session, thinker_id=thinker.id, name="Source 1", url="https://example.com/feed1.xml")
        await create_source(session, thinker_id=thinker.id, name="Source 2", url="https://example.com/feed2.xml")
        await session.commit()

        response = await admin_client.get("/admin/thinkers/partials/list")
        assert response.status_code == 200
        assert "Source Thinker" in response.text
        # The source count "2" should appear in the response
        assert ">2<" in response.text.replace(" ", "").replace("\n", "")

    async def test_search_filters_by_name(self, admin_client, session: AsyncSession):
        """Search with ?q= filters thinkers by name using ILIKE."""
        await create_thinker(session, name="Alice Expert", slug="alice-expert")
        await create_thinker(session, name="Bob Scholar", slug="bob-scholar")
        await session.commit()

        response = await admin_client.get("/admin/thinkers/partials/list?q=Ali")
        assert response.status_code == 200
        assert "Alice Expert" in response.text
        assert "Bob Scholar" not in response.text

    async def test_filter_by_tier(self, admin_client, session: AsyncSession):
        """Filter with ?tier= shows only thinkers of that tier."""
        await create_thinker(session, name="Tier1 Thinker", slug="tier1", tier=1)
        await create_thinker(session, name="Tier2 Thinker", slug="tier2", tier=2)
        await session.commit()

        response = await admin_client.get("/admin/thinkers/partials/list?tier=1")
        assert response.status_code == 200
        assert "Tier1 Thinker" in response.text
        assert "Tier2 Thinker" not in response.text

    async def test_filter_by_active(self, admin_client, session: AsyncSession):
        """Filter with ?active=false shows only inactive thinkers."""
        await create_thinker(session, name="Active Thinker", slug="active-t", active=True)
        await create_thinker(session, name="Inactive Thinker", slug="inactive-t", active=False)
        await session.commit()

        response = await admin_client.get("/admin/thinkers/partials/list?active=false")
        assert response.status_code == 200
        assert "Inactive Thinker" in response.text
        assert "Active Thinker" not in response.text


class TestThinkerAdd:
    """Test the add thinker form and create endpoint."""

    async def test_add_form_loads(self, admin_client):
        """GET add-form partial returns 200 with form elements."""
        response = await admin_client.get("/admin/thinkers/partials/add-form")
        assert response.status_code == 200
        assert 'name="name"' in response.text
        assert 'name="tier"' in response.text
        assert "Add Thinker" in response.text

    async def test_add_form_shows_categories(self, admin_client, session: AsyncSession):
        """Seeded categories appear in the add form."""
        await create_category(session, name="Philosophy", slug="philosophy")
        await create_category(session, name="Economics", slug="economics")
        await session.commit()

        response = await admin_client.get("/admin/thinkers/partials/add-form")
        assert response.status_code == 200
        assert "Philosophy" in response.text
        assert "Economics" in response.text

    async def test_add_thinker_creates_record(self, admin_client, session: AsyncSession):
        """POST /admin/thinkers/add creates thinker with awaiting_llm status."""
        response = await admin_client.post(
            "/admin/thinkers/add",
            data={"name": "Nassim Taleb", "tier": "1", "bio": "Antifragile author"},
        )
        assert response.status_code == 200
        assert "Nassim Taleb" in response.text
        assert "added" in response.text.lower()

        # Verify in DB
        from thinktank.models.thinker import Thinker

        result = await session.execute(select(Thinker).where(Thinker.slug == "nassim-taleb"))
        thinker = result.scalar_one_or_none()
        assert thinker is not None
        assert thinker.approval_status == "awaiting_llm"
        assert thinker.tier == 1
        assert thinker.active is True

    async def test_add_thinker_creates_llm_job(self, admin_client, session: AsyncSession):
        """Adding a thinker creates an llm_approval_check job."""
        await admin_client.post(
            "/admin/thinkers/add",
            data={"name": "Tyler Cowen", "tier": "2", "bio": "Economist"},
        )

        from thinktank.models.job import Job

        result = await session.execute(select(Job).where(Job.job_type == "llm_approval_check"))
        job = result.scalar_one_or_none()
        assert job is not None
        assert job.payload["entity_type"] == "thinker"
        assert job.status == "pending"

    async def test_add_thinker_creates_categories(self, admin_client, session: AsyncSession):
        """POST with category_ids creates ThinkerCategory rows."""
        cat = await create_category(session, name="Science", slug="science")
        await session.commit()

        await admin_client.post(
            "/admin/thinkers/add",
            data={
                "name": "Richard Dawkins",
                "tier": "1",
                "bio": "Evolutionary biologist",
                "category_ids": str(cat.id),
            },
        )

        from thinktank.models.category import ThinkerCategory
        from thinktank.models.thinker import Thinker

        thinker_result = await session.execute(select(Thinker).where(Thinker.slug == "richard-dawkins"))
        thinker = thinker_result.scalar_one()

        tc_result = await session.execute(select(ThinkerCategory).where(ThinkerCategory.thinker_id == thinker.id))
        tcs = tc_result.scalars().all()
        assert len(tcs) == 1
        assert tcs[0].category_id == cat.id
        assert tcs[0].relevance == 5

    async def test_add_thinker_returns_updated_list(self, admin_client, session: AsyncSession):
        """Response contains the new thinker name and success message."""
        response = await admin_client.post(
            "/admin/thinkers/add",
            data={"name": "Sam Harris", "tier": "2", "bio": "Neuroscientist"},
        )
        assert response.status_code == 200
        assert "Sam Harris" in response.text
        assert "LLM approval queued" in response.text


class TestThinkerEdit:
    """Test the edit thinker form and update endpoint."""

    async def test_edit_form_loads(self, admin_client, session: AsyncSession):
        """GET edit form returns 200 with pre-filled thinker values."""
        thinker = await create_thinker(session, name="Edit Test", slug="edit-test", tier=2)
        await session.commit()

        response = await admin_client.get(f"/admin/thinkers/{thinker.id}/edit")
        assert response.status_code == 200
        assert "Edit Test" in response.text
        assert "Edit Thinker" in response.text

    async def test_edit_thinker_updates_name(self, admin_client, session: AsyncSession):
        """POST edit with new name updates the thinker in DB."""
        thinker = await create_thinker(session, name="Old Name", slug="old-name", tier=2)
        await session.commit()

        response = await admin_client.post(
            f"/admin/thinkers/{thinker.id}/edit",
            data={"name": "New Name", "tier": "2", "bio": "Updated bio", "active": "on"},
        )
        assert response.status_code == 200
        assert "updated" in response.text.lower()

        # Verify in DB (use populate_existing to bypass identity map cache)
        updated = await _verify_thinker(session, thinker.id)
        assert updated.name == "New Name"
        assert updated.bio == "Updated bio"

    async def test_edit_thinker_updates_categories(self, admin_client, session: AsyncSession):
        """POST edit with different categories replaces old ThinkerCategory rows."""
        cat1 = await create_category(session, name="Cat1", slug="cat1")
        cat2 = await create_category(session, name="Cat2", slug="cat2")
        thinker = await create_thinker(session, name="Cat Thinker", slug="cat-thinker", tier=1)
        await create_thinker_category(session, thinker_id=thinker.id, category_id=cat1.id, relevance=5)
        await session.commit()

        # Edit to replace cat1 with cat2
        response = await admin_client.post(
            f"/admin/thinkers/{thinker.id}/edit",
            data={
                "name": "Cat Thinker",
                "tier": "1",
                "bio": "",
                "active": "on",
                "category_ids": str(cat2.id),
            },
        )
        assert response.status_code == 200

        from thinktank.models.category import ThinkerCategory

        tc_result = await session.execute(
            select(ThinkerCategory)
            .where(ThinkerCategory.thinker_id == thinker.id)
            .execution_options(populate_existing=True)
        )
        tcs = tc_result.scalars().all()
        assert len(tcs) == 1
        assert tcs[0].category_id == cat2.id


class TestThinkerToggle:
    """Test the toggle active/inactive endpoint."""

    async def test_toggle_deactivates(self, admin_client, session: AsyncSession):
        """POST toggle on active thinker sets active=False."""
        thinker = await create_thinker(session, name="Toggle Active", slug="toggle-active", active=True)
        await session.commit()

        response = await admin_client.post(f"/admin/thinkers/{thinker.id}/toggle-active")
        assert response.status_code == 200
        assert "deactivated" in response.text.lower()

        updated = await _verify_thinker(session, thinker.id)
        assert updated.active is False

    async def test_toggle_reactivates(self, admin_client, session: AsyncSession):
        """POST toggle on inactive thinker sets active=True."""
        thinker = await create_thinker(session, name="Toggle Inactive", slug="toggle-inactive", active=False)
        await session.commit()

        response = await admin_client.post(f"/admin/thinkers/{thinker.id}/toggle-active")
        assert response.status_code == 200
        assert "activated" in response.text.lower()

        updated = await _verify_thinker(session, thinker.id)
        assert updated.active is True

    async def test_toggle_preserves_data(self, admin_client, session: AsyncSession):
        """After deactivation, all thinker fields remain unchanged."""
        thinker = await create_thinker(
            session,
            name="Preserved Thinker",
            slug="preserved",
            tier=1,
            bio="Important bio",
            active=True,
        )
        await create_source(
            session,
            thinker_id=thinker.id,
            name="Preserved Source",
            url="https://example.com/preserved.xml",
        )
        await session.commit()

        # Toggle to deactivate
        await admin_client.post(f"/admin/thinkers/{thinker.id}/toggle-active")

        from thinktank.models.source import Source

        updated = await _verify_thinker(session, thinker.id)
        assert updated.active is False
        assert updated.name == "Preserved Thinker"
        assert updated.tier == 1
        assert updated.bio == "Important bio"

        # Sources still linked
        source_result = await session.execute(select(Source).where(Source.thinker_id == thinker.id))
        sources = source_result.scalars().all()
        assert len(sources) == 1
        assert sources[0].name == "Preserved Source"
